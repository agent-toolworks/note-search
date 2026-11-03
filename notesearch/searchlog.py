"""The search log: one JSON line per `ns search` and `ns get`, read by the hub.

It holds queries and references, never note text. With "log": "counts" in
ns.json the query words are dropped too (their counts are kept); with "off"
nothing is written. The file is private to its owner (mode 600), is rotated
at 10 MB (one older file kept) and readers ignore entries older than 90 days.

A line, version 1:

  search: {"v": 1, "event": "search", "t", "q", "session", "project",
           "groups", "wordings", "words", "filters", "since", "stages",
           "strict", "found", "offset", "refs", "tokens", "ms", "warnings"}
  get:    {"v": 1, "event": "get", "t", "q", "session", "project",
           "ref", "note", "tokens", "cut"}

`q` is the query id. A search prints it; `ns get --q <id>` passes it back.
`refs` are the notes a search returned, best first, starting at rank
offset + 1. `note` is a get's reference without its heading or line range,
so it can be matched against a search's `refs`.

Writing never fails a command: a log that cannot be written is skipped.
"""
from __future__ import annotations

import datetime as _dt
import glob
import json
import os
import re
import secrets

VERSION = 1
NAME = "searches.jsonl"
OLD_NAME = "searches.1.jsonl"
MAX_BYTES = 10 * 1000 * 1000
KEEP_DAYS = 90

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def path(cfg) -> str:
    return os.path.join(cfg.cache_dir, NAME)


def new_id() -> str:
    return secrets.token_hex(4)


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _claude_dirs() -> list:
    out = []
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        out.append(os.path.expanduser(os.environ["CLAUDE_CONFIG_DIR"]))
    out.extend(sorted(glob.glob(os.path.expanduser("~/.claude*"))))
    seen, uniq = set(), []
    for d in out:
        if d not in seen and os.path.isdir(d):
            seen.add(d)
            uniq.append(d)
    return uniq


def session() -> str | None:
    """The Claude Code session that ran ns: the transcript's id, which the hub
    links to. CLAUDE_CODE_SESSION_ID when set, else the session file Claude
    Code keeps for its process (CLAUDE_PID), else unknown."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip().lower()
    if _UUID.match(sid):
        return sid
    pid = os.environ.get("CLAUDE_PID", "").strip()
    if pid.isdigit():
        for d in _claude_dirs():
            try:
                with open(os.path.join(d, "sessions", pid + ".json"), encoding="utf-8") as fh:
                    sid = str(json.load(fh).get("sessionId", "")).lower()
            except (OSError, ValueError, AttributeError):
                continue
            if _UUID.match(sid):
                return sid
    return None


def project() -> str | None:
    if not os.environ.get("CLAUDECODE"):
        return None
    try:
        return os.getcwd()
    except OSError:
        return None


def search_entry(cfg, q, res, query: dict, refs: list, offset: int, tokens: int, ms: int, warnings: int) -> dict:
    wordings = query.get("wordings") or []
    entry = {
        "v": VERSION, "event": "search", "t": now(), "q": q, "session": session(), "project": project(),
        "groups": query.get("groups") or [], "wordings": wordings, "words": [len(w.split()) for w in wordings],
        "filters": query.get("filters") or {}, "since": query.get("since"),
        "stages": list(res.stages), "strict": bool(res.strict_found), "found": len(res.notes),
        "offset": offset, "refs": refs, "tokens": tokens, "ms": ms, "warnings": warnings,
    }
    if cfg.log == "counts":
        entry["wordings"] = None
        entry["filters"] = {k: None for k in entry["filters"]}
        entry["project"] = None
    return entry


def get_entry(cfg, q, ref: str, note: str, tokens: int, cut: bool) -> dict:
    return {"v": VERSION, "event": "get", "t": now(), "q": q, "session": session(),
            "project": None if cfg.log == "counts" else project(),
            "ref": ref, "note": note, "tokens": tokens, "cut": cut}


def write(cfg, entry: dict) -> bool:
    if cfg.log == "off":
        return False
    p = path(cfg)
    line = (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        os.makedirs(cfg.cache_dir, mode=0o700, exist_ok=True)
        try:
            if os.path.getsize(p) + len(line) > MAX_BYTES:
                os.replace(p, os.path.join(cfg.cache_dir, OLD_NAME))
        except OSError:
            pass
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)  # one write: lines from processes running at once don't mix
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


def _parse_time(t: str):
    try:
        return _dt.datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def read(cache_dir: str, days: int = KEEP_DAYS, now_dt=None) -> tuple:
    """(entries oldest first, number of lines skipped as unreadable)."""
    now_dt = now_dt or _dt.datetime.now(_dt.timezone.utc)
    cutoff = now_dt - _dt.timedelta(days=days)
    out, bad = [], 0
    for name in (OLD_NAME, NAME):
        try:
            fh = open(os.path.join(cache_dir, name), "rb")
        except OSError:
            continue
        with fh:
            for raw in fh:
                try:
                    e = json.loads(raw.decode("utf-8"))
                except ValueError:
                    bad += 1  # a line cut short by a full disk, or not ours
                    continue
                if not isinstance(e, dict) or e.get("v") != VERSION or e.get("event") not in ("search", "get"):
                    bad += 1
                    continue
                t = _parse_time(e.get("t"))
                if t is None:
                    bad += 1
                    continue
                if t >= cutoff:
                    e["_t"] = t
                    out.append(e)
    out.sort(key=lambda e: e["_t"])
    return out, bad
