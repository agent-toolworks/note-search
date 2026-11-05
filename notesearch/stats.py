"""Measures over the search log: whether searches worked. Used by hub-source/run.

A search counts once, however many pages the agent asked for: a search with
--offset is a page of an earlier one, not a search of its own.

- Opened: a search was followed by `ns get` of a note it returned. The get is
  tied to the search by its query id (`ns get --q`), else to the latest search
  in the same session, at most OPEN_WINDOW earlier, that returned that note.
- Searched again: another search in the same session within RESEARCH_WINDOW,
  the sign that the first one did not find it. Only searches whose session is
  known count, since without it two agents' searches cannot be told apart.
"""
from __future__ import annotations

import datetime as _dt
import statistics
from collections import Counter, defaultdict

OPEN_WINDOW = _dt.timedelta(minutes=10)
RESEARCH_WINDOW = _dt.timedelta(minutes=2)
RESEARCH_TARGET = 20  # percent; the design's target


class Search:
    __slots__ = ("e", "t", "opened", "ranks", "again", "gets")

    def __init__(self, e):
        self.e = e
        self.t = e["_t"]
        self.opened = False
        self.ranks = []
        self.again = False
        self.gets = []

    @property
    def found(self):
        return self.e.get("found") or 0

    @property
    def strict(self):
        return bool(self.e.get("strict"))

    @property
    def session(self):
        return self.e.get("session")

    def rank_of(self, note):
        refs = self.e.get("refs") or []
        if note in refs:
            return refs.index(note) + 1 + (self.e.get("offset") or 0)
        return None


def link(entries: list) -> tuple:
    """(searches, gets): each search knows whether it was opened and searched again."""
    searches, gets, by_q = [], [], {}
    for e in entries:
        if e["event"] == "search":
            s = Search(e)
            if (e.get("offset") or 0) > 0:
                # a later page: its hits count as the earlier search's when one matches
                prev = _page_of(searches, e)
                if prev is not None:
                    by_q[e.get("q")] = prev
                    prev.e.setdefault("_pages", []).append(e)
                    continue
            searches.append(s)
            if e.get("q"):
                by_q[e["q"]] = s
        else:
            gets.append(e)
    for g in gets:
        s = by_q.get(g.get("q")) if g.get("q") else None
        if s is None:
            s = _latest_with(searches, g)
        if s is None:
            continue
        rank = _rank(s, g.get("note"))
        s.opened = True
        s.gets.append(g)
        if rank:
            s.ranks.append(rank)
    by_session = defaultdict(list)
    for s in searches:
        if s.session:
            by_session[s.session].append(s)
    for items in by_session.values():
        for a, b in zip(items, items[1:]):
            if b.t - a.t <= RESEARCH_WINDOW:
                a.again = True
    return searches, gets


def _page_of(searches, e):
    for s in reversed(searches):
        if e["_t"] - s.t > OPEN_WINDOW:
            return None
        if s.session == e.get("session") and s.e.get("wordings") == e.get("wordings") \
                and s.e.get("groups") == e.get("groups"):
            return s
    return None


def _rank(s, note):
    r = s.rank_of(note)
    if r:
        return r
    for page in s.e.get("_pages", []):
        refs = page.get("refs") or []
        if note in refs:
            return refs.index(note) + 1 + (page.get("offset") or 0)
    return None


def _latest_with(searches, g):
    for s in reversed(searches):
        if s.t > g["_t"]:
            continue
        if g["_t"] - s.t > OPEN_WINDOW:
            return None
        if s.session != g.get("session"):
            continue
        if s.session is None and s.e.get("project") != g.get("project"):
            continue
        if _rank(s, g.get("note")):
            return s
    return None


def _pct(part, whole):
    return None if not whole else round(100.0 * part / whole)


def _median(xs):
    return None if not xs else statistics.median(xs)


def measures(searches: list, gets: list, since) -> dict:
    ss = [s for s in searches if s.t >= since]
    gs = [g for g in gets if g["_t"] >= since]
    hits = [s for s in ss if s.found]
    known = [s for s in ss if s.session]
    opened = [s for s in hits if s.opened]
    ranked = [s for s in opened if s.ranks]
    relaxed = [s for s in hits if not s.strict and "relaxed" in (s.e.get("stages") or [])]
    named = [s for s in hits if "names" in (s.e.get("stages") or [])]
    meant = [s for s in hits if "semantic" in (s.e.get("stages") or [])]
    return {
        "searches": len(ss),
        "gets": len(gs),
        "found_nothing": len(ss) - len(hits),
        "no_strict": len([s for s in ss if not s.strict]),
        "open_rate": _pct(len(opened), len(hits)),
        "again_rate": _pct(len([s for s in known if s.again]), len(known)),
        "first_opened": _pct(len([s for s in ranked if min(s.ranks) == 1]), len(ranked)),
        "median_rank": _median([min(s.ranks) for s in ranked]),
        "tokens_search": _median([s.e.get("tokens") or 0 for s in ss]),
        "tokens_get": _median([g.get("tokens") or 0 for g in gs]),
        "relaxed": len(relaxed),
        "relaxed_open_rate": _pct(len([s for s in relaxed if s.opened]), len(relaxed)),
        "names": len(named),
        "names_open_rate": _pct(len([s for s in named if s.opened]), len(named)),
        "semantic": len(meant),
        "semantic_open_rate": _pct(len([s for s in meant if s.opened]), len(meant)),
        "median_ms": _median([s.e.get("ms") or 0 for s in ss]),
    }


def per_day(searches: list, since, until) -> dict:
    """{group: Counter(day -> searches)}; a search over two groups counts for each."""
    out = defaultdict(Counter)
    for s in searches:
        if s.t < since:
            continue
        for g in s.e.get("groups") or ["?"]:
            out[g][s.t.date()] += 1
    return out


def by_group(searches: list, since) -> dict:
    out = defaultdict(lambda: {"searches": 0, "opened": 0, "last": None})
    for s in searches:
        for g in s.e.get("groups") or []:
            row = out[g]
            if s.t >= since:
                row["searches"] += 1
                row["opened"] += 1 if s.opened else 0
            if row["last"] is None or s.t > row["last"]:
                row["last"] = s.t
    return out
