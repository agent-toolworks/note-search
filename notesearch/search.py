"""Search: one call should be enough.

A search takes up to 5 wordings at once (the calling agent supplies the
others), and for each:

1. strict: every word, BM25-ranked with column weights;
2. names: a name that is not in the index (`payment-gatway`) is replaced by
   the nearest name that is (`payment-gateway`), and the wording runs again;
3. relaxed: if fewer than 3 notes matched, any word, keeping only sections
   that match at least half of the words.

The wordings' rankings are merged by reciprocal rank fusion, then adjusted:
an exact match on a note's entities counts more, a stale note less, a
much-linked note a little more. One hit per note: its best section.

In a group whose engine is "embeddings" (see embed.py), each wording also
finds the sections closest to it in meaning, a ranking of their own merged
the same way ("semantic"). Such a hit, like a relaxed one, is never shown as
a strict one, and an answer with no strict hit says so.
"""
from __future__ import annotations

import difflib
import itertools
import json
import math
import os
import re
import time
from dataclasses import dataclass, field

from . import embed, names
from .index import FTS_COLUMNS, unavailable

MAX_WORDINGS = 5
SECTIONS_PER_QUERY = 400
SNIPPET_READ = 8192
RELAXED_MAX = 5000  # sections kept for ranking in the relaxed stage, those holding the most words first
RRF_K = 60
STAGE_WEIGHT = {"strict": 1.0, "names": 0.9, "relaxed": 0.4, "semantic": 0.5}
STAGE_ORDER = {"strict": 0, "names": 1, "relaxed": 2, "semantic": 3}
WORD_STAGES = ("strict", "names")  # the stages whose hits count as a strict match
# A meaning hit counts by how close it is to the wording's best one, not by its
# rank: with RRF_K at 60, ranks 1 and 2 differ by under 2%, less than the boosts
# in _finish, which then decided the order instead of the similarity.
SEMANTIC_TAU = 0.03

DEFAULT_WEIGHTS = {
    "title": 10.0, "aliases": 10.0, "entities": 8.0, "headings": 5.0, "path": 3.0,
    "meta": 3.0, "tags": 2.0, "idents": 1.0, "body": 1.0,
}

STOPWORDS = set("""
a an and are as at be been but by can could did do does for from had has have how i if in into is it its
me my of on or our should so than that the their them then there these they this those to was we were what
when where which who whom why will with would you your about any all also just get got use used using
there's what's how's who's is it's we're don't doesn't
""".split())

_TOKEN = re.compile(r'"([^"]+)"|(\S+)')
_EDGE = re.compile(r"^[^\w/]+|[^\w]+$")


@dataclass
class Term:
    text: str
    phrase: bool = False

    @property
    def fts(self) -> str:
        return '"' + self.text.replace('"', '""') + '"'


@dataclass
class Hit:
    file_id: int
    section_id: int
    stage: str
    rank: int
    terms: list  # the words that found it, to build its snippet later
    coverage: tuple = (0, 0)
    sim: float = 0.0  # semantic: cosine similarity to the wording


@dataclass
class Note:
    file_id: int
    grp: str
    rel: str
    title: str
    heading: str
    hpath: str
    line_start: int
    line_end: int
    date: str
    date_src: str
    status: str
    stale: bool
    partial: bool
    snippet: str
    stage: str
    coverage: tuple
    sections: int
    score: float
    related: list = field(default_factory=list)
    superseded_by: list = field(default_factory=list)

    @property
    def ref(self) -> str:
        return "%s:%s" % (self.grp, self.rel)

    @property
    def section_ref(self) -> str:
        return "%s#%s" % (self.ref, self.heading) if self.heading else self.ref

    def as_dict(self) -> dict:
        return {
            "ref": self.ref, "section": self.section_ref, "lines": [self.line_start, self.line_end],
            "title": self.title, "heading_path": self.hpath, "date": self.date, "date_source": self.date_src,
            "status": self.status, "stale": self.stale, "partial": self.partial, "snippet": self.snippet,
            "stage": self.stage, "coverage": list(self.coverage), "sections_matched": self.sections,
            "score": round(self.score, 5), "related": self.related, "superseded_by": self.superseded_by,
        }


@dataclass
class Result:
    notes: list
    wordings: list
    groups: list
    stages: list
    strict_found: bool
    nearest: list
    warnings: list
    ms: int = 0


def parse_terms(wording: str) -> list:
    out = []
    for m in _TOKEN.finditer(wording):
        if m.group(1):
            if any(c.isalnum() for c in m.group(1)):
                out.append(Term(m.group(1).strip(), True))
            continue
        tok = _EDGE.sub("", m.group(2))
        if tok and any(c.isalnum() for c in tok):
            out.append(Term(tok))
    return out


def content_terms(terms: list) -> list:
    kept = [t for t in terms if t.phrase or t.text.lower() not in STOPWORDS]
    return kept or terms


# The note named after what was asked for -- its title, an alias or its file name.
# 1.3 lost to the 1.5 a note gets for merely listing the thing as an entity, so a
# superseded decision or an incident outranked the page about it. Measured
# 2026-11-03 over four question sets (fixtures/eval, Copperleaf docs, Copperleaf
# plus 5,000 distractors, the real-notes set): 1.6 moved three questions to first
# place and none down; 2.0 and 2.5 cost real-notes questions.
OWN_BOOST = 1.6


class Searcher:
    def __init__(self, conn, cfg, weights=None):
        self.conn = conn
        self.cfg = cfg
        w = dict(DEFAULT_WEIGHTS)
        w.update(weights or {})
        self.weights = ", ".join("%g" % w[c] for c in FTS_COLUMNS)
        self._cut = None

    # -- scope -----------------------------------------------------------
    def _scope(self, groups: list, filters: dict, since: str) -> tuple:
        """(SQL fragment, params) limiting file ids to the search's scope; ("", []) for everything."""
        if not filters and not since and set(groups) == set(self.cfg.groups):
            return "", []
        sql = "SELECT file_id FROM members WHERE grp IN (%s)" % ",".join("?" * len(groups))
        params = list(groups)
        if since:
            sql += " AND file_id IN (SELECT id FROM files WHERE date >= ?)"
            params.append(since)
        if filters:
            allowed = []
            rows = self.conn.execute(
                "SELECT DISTINCT f.id, f.fm FROM files f JOIN members m ON m.file_id=f.id WHERE m.grp IN (%s)"
                % ",".join("?" * len(groups)), groups)
            for fid, fm in rows:
                data = {k.lower(): v for k, v in json.loads(fm or "{}").items()}
                ok = True
                for key, want in filters.items():
                    v = data.get(key.lower())
                    vals = v if isinstance(v, list) else ([v] if v is not None else [])
                    if not any(str(x).strip().lower() == want.lower() for x in vals):
                        ok = False
                        break
                if ok:
                    allowed.append(fid)
            sql += " AND file_id IN (SELECT value FROM json_each(?))"
            params.append(json.dumps(allowed))
        return sql, params

    def _common_cut(self) -> int:
        if self._cut is None:
            total = self.conn.execute("SELECT max(id) FROM sections").fetchone()[0] or 0
            self._cut = max(2000, total // 20)
        return self._cut

    def _run(self, match: str, scope: tuple, limit: int = SECTIONS_PER_QUERY) -> list:
        # No snippet here: SQLite would build one for every matching section
        # before sorting, which costs ten times the search. See snippet().
        where = ""
        params = [match]
        if scope[0]:
            where += " AND s.file_id IN (%s)" % scope[0]
            params += scope[1]
        sql = (
            "SELECT sec_fts.rowid, s.file_id, bm25(sec_fts, %s) AS score "
            "FROM sec_fts JOIN sections s ON s.id = sec_fts.rowid "
            "WHERE sec_fts MATCH ?%s ORDER BY score LIMIT ?" % (self.weights, where))
        return self.conn.execute(sql, params + [limit]).fetchall()

    @staticmethod
    def snippet(path: str, byte_start: int, line_start: int, line_end: int, terms: list, width: int = 24) -> str:
        """About `width` words of a section, read from the note itself, around
        the first word that matches the search."""
        try:
            with open(path, "rb") as fh:
                fh.seek(byte_start or 0)
                raw = fh.read(SNIPPET_READ)
        except OSError:
            return ""
        lines = raw.decode("utf-8", errors="replace").splitlines()[: max(1, line_end - line_start + 1)]
        words = " ".join(lines).split()
        if not words:
            return ""
        keys = []
        for t in terms:
            for w in t.lower().split():
                w = w.strip("`'\".,;:()[]")
                if len(w) >= 3:
                    keys.append(w[: max(3, len(w) - 2)])  # a rough stem: "retries" finds "retry"
        # The passage holding the most distinct search words; the first such
        # passage wins, and text is preferred over the heading line.
        skip = len(lines[0].split()) if lines and lines[0].lstrip().startswith("#") and len(lines) > 1 else 0
        found = [(i, {k for k in keys if k in w.lower()}) for i, w in enumerate(words)]
        found = [(i, ks) for i, ks in found if ks]
        at, best = min(skip, len(words) - 1), -1
        for i, _ in found:
            start = max(0, i - 6)
            have = set()
            for j, ks in found:
                if start <= j < start + width:
                    have |= ks
            score = len(have) * 2 + (1 if i >= skip else 0)
            if score > best:
                at, best = i, score
        start = max(0, at - 6)
        out = " ".join(words[start:start + width])
        return ("…" if start else "") + out + ("…" if start + width < len(words) else "")

    # -- names -----------------------------------------------------------
    def known(self, term: str) -> bool:
        return self.conn.execute("SELECT 1 FROM vocab WHERE term=?", (term.lower(),)).fetchone() is not None

    def candidates(self, term: str, limit: int = 3, floor: float = 0.8) -> list:
        t = term.lower()
        grams = sorted(set(names.trigrams(t)))
        if len(t) < 4 or not grams:
            return []
        match = " OR ".join('"%s"' % g.replace('"', '""') for g in grams)
        rows = self.conn.execute(
            "SELECT term FROM vocab_tri WHERE vocab_tri MATCH ? ORDER BY rank LIMIT 400", (match,)).fetchall()
        scored = []
        for (cand,) in rows:
            if cand == t:
                continue
            ratio = difflib.SequenceMatcher(None, t, cand).ratio()
            if len(t) >= 4 and t in cand:
                ratio = max(ratio, 0.85 + 0.1 * len(t) / len(cand))
            if ratio >= floor:
                scored.append((ratio, cand))
        scored.sort(key=lambda x: (-x[0], len(x[1]), x[1]))
        return [c for _, c in scored[:limit]]

    # -- one wording -----------------------------------------------------
    def _wording(self, terms: list, scope: tuple, stages: set) -> list:
        content = content_terms(terms)
        hits: list = []
        rank = 0
        seen_sections = set()

        def add(rows, stage, match: list, coverage=None):
            nonlocal rank
            for sid, fid, _score in rows:
                if sid in seen_sections:
                    continue
                seen_sections.add(sid)
                cov = coverage.get(sid, (0, len(content))) if coverage else (len(content), len(content))
                hits.append(Hit(fid, sid, stage, rank, match, cov))
                rank += 1

        strict_match = " ".join(t.fts for t in content)
        strict = self._run(strict_match, scope)
        if strict:
            stages.add("strict")
        add(strict, "strict", [t.text for t in content])
        files = {h.file_id for h in hits}

        unknown = [t for t in content if not t.phrase and len(t.text) >= 4
                   and (names.is_name(t.text) or len(files) < 3) and not self.known(t.text)]
        if unknown:
            fixed = []
            changed = False
            for t in content:
                if t in unknown:
                    cands = self.candidates(t.text)
                    if cands:
                        fixed.append(Term(cands[0], True))
                        changed = True
                        continue
                fixed.append(t)
            if changed:
                fixed_match = " ".join(t.fts for t in fixed)
                rows = self._run(fixed_match, scope)
                if rows:
                    stages.add("names")
                add(rows, "names", [t.text for t in fixed])
                files = {h.file_id for h in hits}

        if len(files) < 3 and len(content) > 1:
            # Count, for each section, how many of the words it holds, from each
            # word's own list of sections: exact, and cheaper than ranking every
            # section that holds any word. A word in a large share of all
            # sections tells them apart too little to count, and would cost the
            # most; it is left out here (the strict stage used it).
            cut = self._common_cut()
            counts: dict = {}
            informative = []
            for t in content:
                ids = [r[0] for r in self.conn.execute(
                    "SELECT rowid FROM sec_fts WHERE sec_fts MATCH ? LIMIT ?", (t.fts, cut + 1))]
                if len(ids) > cut:
                    continue
                informative.append(t)
                for sid in ids:
                    counts[sid] = counts.get(sid, 0) + 1
            n = len(informative)
            # Two words: either will do. Three or more: at least half, and at least two.
            need = 1 if n <= 2 else max(2, (n + 1) // 2)
            qualify = sorted((sid for sid, c in counts.items() if c >= need and sid not in seen_sections),
                             key=lambda sid: (-counts[sid], sid))[:RELAXED_MAX]
            if qualify:
                # Rank every section holding any of the words and keep those that
                # qualify. Each word is in at most `cut` sections, so this is
                # bounded; asking SQLite for "rowid IN (list)" instead runs one
                # full-text lookup per listed section, which is far slower.
                any_match = " OR ".join(t.fts for t in informative)
                keep = set(qualify)
                rows = [r for r in self._run(any_match, scope, limit=n * cut + 1) if r[0] in keep]
                rows = rows[:SECTIONS_PER_QUERY]
                if rows:
                    stages.add("relaxed")
                add(rows, "relaxed", [t.text for t in informative],
                    {sid: (counts.get(sid, 0), len(content)) for sid, _, _ in rows})
        return hits

    # -- the whole search ------------------------------------------------
    def search(self, wordings: list, groups: list, filters: dict = None, since: str = None,
               limit: int = 50) -> Result:
        start = time.time()
        warnings = []
        wordings = [w for w in (w.strip() for w in wordings) if w]
        if len(wordings) > MAX_WORDINGS:
            warnings.append("only the first %d wordings were used" % MAX_WORDINGS)
            wordings = wordings[:MAX_WORDINGS]
        scope = self._scope(groups, filters or {}, since)
        stages: set = set()
        per_file: dict = {}
        all_terms = []
        lists = []
        for w in wordings:
            terms = parse_terms(w)
            all_terms.extend(content_terms(terms))
            if not terms:
                warnings.append("wording %r has no words to search for" % w)
                continue
            lists.append(self._wording(terms, scope, stages))
        lists.extend(self._semantic(wordings, groups, filters or {}, since, stages, warnings))
        for hits in lists:
            top_sim = hits[0].sim if hits else 0.0
            file_rank = {}
            for h in hits:
                if h.file_id not in file_rank:
                    file_rank[h.file_id] = (len(file_rank), h)
                entry = per_file.setdefault(h.file_id, {"score": 0.0, "best": None, "sections": set()})
                entry["sections"].add(h.section_id)
            for fid, (r, h) in file_rank.items():
                entry = per_file[fid]
                if h.stage == "semantic":
                    entry["score"] += STAGE_WEIGHT[h.stage] * math.exp((h.sim - top_sim) / SEMANTIC_TAU) / RRF_K
                else:
                    entry["score"] += STAGE_WEIGHT[h.stage] / (RRF_K + r)
                best = entry["best"]
                if best is None or (STAGE_ORDER[h.stage], r) < (STAGE_ORDER[best[0].stage], best[1]):
                    entry["best"] = (h, r)
        notes = self._finish(per_file, all_terms, wordings, groups, limit)
        strict_found = any(n.stage in WORD_STAGES for n in notes)
        nearest = []
        if not strict_found:
            for t in all_terms:
                if not t.phrase:
                    for c in self.candidates(t.text, limit=3, floor=0.75):
                        if c not in nearest and self._run('"%s"' % c.replace('"', '""'), scope, 1):
                            nearest.append(c)
        return Result(notes, wordings, groups, sorted(stages, key=STAGE_ORDER.get), strict_found,
                      nearest[:5], warnings, int((time.time() - start) * 1000))

    def _semantic(self, wordings: list, groups: list, filters: dict, since: str, stages: set, warnings: list) -> list:
        """One list of hits per wording, from the sections closest in meaning, in embedded groups."""
        embedded = self.cfg.embedded(groups)
        if not embedded or not wordings:
            return []
        scope = self._scope(embedded, filters, since)
        try:
            found = embed.search(self.conn, self.cfg, wordings, scope)
        except embed.Unavailable as exc:
            warnings.append(unavailable(exc))
            return []
        out = []
        for w, rows in zip(wordings, found):
            terms = [t.text for t in content_terms(parse_terms(w))]
            hits = [Hit(fid, sid, "semantic", r, terms, sim=sim) for r, (sid, fid, sim) in enumerate(rows)]
            if hits:
                stages.add("semantic")
            out.append(hits)
        return out

    def _finish(self, per_file: dict, terms: list, wordings: list, groups: list, limit: int) -> list:
        if not per_file:
            return []
        ids = list(per_file)
        rows = self.conn.execute(
            "SELECT id, title, date, date_src, status, stale, partial, entities, stem, aliases FROM files "
            "WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(ids),)).fetchall()
        info = {r[0]: r for r in rows}
        stems = {r[0]: r[8] for r in rows}
        inlinks = dict(self.conn.execute(
            "SELECT target, count(DISTINCT file_id) FROM links WHERE target IN (SELECT value FROM json_each(?)) GROUP BY target",
            (json.dumps(list(set(stems.values()))),)).fetchall())
        wanted = {t.text.lower() for t in terms} | {w.lower().strip() for w in wordings}
        scored = []
        for fid, entry in per_file.items():
            if fid not in info:
                continue
            _, title, date, date_src, status, stale, partial, entities, stem, aliases = info[fid]
            score = entry["score"]
            ents = set(json.loads(entities or "[]"))
            if ents & wanted:
                score *= 1.5
            # The note named after what was asked for: its title, an alias or its file name.
            own = {(title or "").lower(), stem} | {a.lower() for a in json.loads(aliases or "[]")}
            if own & wanted:
                score *= OWN_BOOST
            if stale:
                score *= 0.5
            score *= 1 + 0.05 * min(inlinks.get(stem, 0), 4)
            scored.append((score, fid))
        scored.sort(key=lambda x: (-x[0], x[1]))
        out = []
        order = {g: i for i, g in enumerate(groups)}
        for score, fid in scored[:limit]:
            entry = per_file[fid]
            hit = entry["best"][0]
            sec = self.conn.execute(
                "SELECT s.heading, s.hpath, s.line_start, s.line_end, s.byte_start, f.real "
                "FROM sections s JOIN files f ON f.id = s.file_id WHERE s.id=?", (hit.section_id,)).fetchone()
            if not sec:
                continue
            mem = self.conn.execute("SELECT grp, rel FROM members WHERE file_id=?", (fid,)).fetchall()
            mem = sorted((m for m in mem if m[0] in order), key=lambda m: order[m[0]])
            if not mem:
                continue
            _, title, date, date_src, status, stale, partial, _, _, _ = info[fid]
            out.append(Note(
                file_id=fid, grp=mem[0][0], rel=mem[0][1], title=title, heading=sec[0], hpath=sec[1],
                line_start=sec[2], line_end=sec[3], date=date, date_src=date_src, status=status,
                stale=bool(stale), partial=bool(partial),
                snippet=self.snippet(sec[5], sec[4], sec[2], sec[3], hit.terms),
                stage=hit.stage, coverage=hit.coverage, sections=len(entry["sections"]), score=score))
        self._replacements(out, groups)
        return out

    def _replacements(self, notes: list, groups: list):
        """Mark each note that a newer note supersedes (`supersedes: group:path`)."""
        if not notes:
            return
        by_ref = {}
        for n in notes:
            for grp, rel in self.conn.execute("SELECT grp, rel FROM members WHERE file_id=?", (n.file_id,)):
                by_ref["%s:%s" % (grp, rel)] = n
        rows = self.conn.execute(
            "SELECT f.supersedes, m.grp, m.rel FROM files f JOIN members m ON m.file_id = f.id "
            "WHERE f.supersedes IN (SELECT value FROM json_each(?))", (json.dumps(list(by_ref)),)).fetchall()
        order = {g: i for i, g in enumerate(groups)}
        for old, grp, rel in sorted(rows, key=lambda r: order.get(r[1], len(order))):
            n = by_ref.get(old)
            new = "%s:%s" % (grp, rel)
            if n is not None and new not in n.superseded_by and new != n.ref:
                n.superseded_by.append(new)

    def related(self, file_id: int, groups: list, limit: int = 3) -> list:
        """References of notes linked from or to this one, in the given groups."""
        stem = self.conn.execute("SELECT stem FROM files WHERE id=?", (file_id,)).fetchone()
        if not stem:
            return []
        q = ",".join("?" * len(groups))
        rows = self.conn.execute(
            "SELECT DISTINCT m.grp, m.rel FROM links l JOIN files f ON f.stem = l.target "
            "JOIN members m ON m.file_id = f.id WHERE l.file_id = ? AND f.id != ? AND m.grp IN (%s) "
            "UNION SELECT DISTINCT m.grp, m.rel FROM links l JOIN members m ON m.file_id = l.file_id "
            "WHERE l.target = ? AND l.file_id != ? AND m.grp IN (%s)" % (q, q),
            [file_id, file_id] + groups + [stem[0], file_id] + groups).fetchall()
        return ["%s:%s" % r for r in rows[:limit]]
