"""`ns doctor`: check the setup and the index; each problem with its fix."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys

from . import config as cfglib
from .index import Index

_SECRETS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("password", re.compile(r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*[^\s`'\"<>]{6,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("API key", re.compile(r"(?i)\b(?:api[_-]?key|secret|token)\s*[:=]\s*[A-Za-z0-9_\-]{20,}")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.")),
    ("connection string with a password", re.compile(r"\b[a-z][a-z0-9+]*://[^\s:/@]+:[^\s@/]{3,}@")),
]


class Checks:
    def __init__(self):
        self.items = []

    def add(self, status, what, fix=None, details=None):
        self.items.append({"status": status, "check": what, "fix": fix, "details": details or []})

    def text(self) -> str:
        out = []
        for it in self.items:
            out.append("%-4s %s" % (it["status"], it["check"]))
            for d in it["details"][:12]:
                out.append("       %s" % d)
            if len(it["details"]) > 12:
                out.append("       … %d more" % (len(it["details"]) - 12))
            if it["fix"]:
                out.append("     fix: %s" % it["fix"])
        return "\n".join(out)

    @property
    def failed(self) -> bool:
        return any(it["status"] == "FAIL" for it in self.items)


def run(args) -> tuple:
    c = Checks()
    v = sys.version_info
    c.add("OK" if v >= (3, 8) else "FAIL", "python %d.%d.%d" % (v[0], v[1], v[2]),
          None if v >= (3, 8) else "install Python 3.8 or newer")
    try:
        mem = sqlite3.connect(":memory:")
        mem.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        mem.execute("CREATE VIRTUAL TABLE t3 USING fts5(x, tokenize='trigram')")
        c.add("OK", "sqlite %s with full-text search (FTS5) and trigrams" % sqlite3.sqlite_version)
    except sqlite3.OperationalError as exc:
        c.add("FAIL", "sqlite %s lacks a feature: %s" % (sqlite3.sqlite_version, exc),
              "use a Python whose SQLite is 3.34 or newer with FTS5 (the macOS system python3 has it)")
        return 1, c.text(), {"ok": False, "checks": c.items}

    try:
        cfg = cfglib.load(args.config)
    except cfglib.ConfigError as exc:
        path = args.config or os.environ.get("NS_CONFIG") or cfglib.DEFAULT_PATH
        missing = any("does not exist" in p for p in exc.problems)
        c.add("FAIL", "config %s" % path,
              ("create it; the smallest one is {\"groups\": {\"notes\": {\"dirs\": [\"~/notes\"]}}}" if missing
               else "edit the file; then run ns config check"), exc.problems)
        return 1, c.text(), {"ok": False, "checks": c.items}
    c.add("OK", "config %s: %d group%s" % (cfg.path, len(cfg.groups), "" if len(cfg.groups) == 1 else "s"))

    for gid, g in cfg.groups.items():
        dirs, warns = g.resolve()
        if not dirs:
            c.add("FAIL", "group %s covers no folder" % gid, "fix its dirs in %s" % cfg.path, warns)
        elif warns:
            c.add("WARN", "group %s: %d folder%s, with problems" % (gid, len(dirs), "" if len(dirs) == 1 else "s"),
                  "fix or remove the missing folders in %s" % cfg.path, warns)
        else:
            c.add("OK", "group %s: %d folder%s" % (gid, len(dirs), "" if len(dirs) == 1 else "s"))
        if g.write:
            d, why = g.write_dir()
            if d is None:
                c.add("FAIL", why, "point write at a visible folder inside the group's dirs in %s" % cfg.path)
            else:
                c.add("OK", "group %s: ns add writes to %s" % (gid, g.write))

    idx = Index(cfg)
    try:
        report = idx.update()
        conn = idx.open()
        check = conn.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        c.add("FAIL", "index %s cannot be read: %s" % (idx.path, exc), "ns index --rebuild")
        return 1, c.text(), {"ok": False, "checks": c.items}
    size = sum(os.path.getsize(idx.path + s) for s in ("", "-wal") if os.path.exists(idx.path + s))
    notes = conn.execute("SELECT count(*) FROM files").fetchone()[0]
    sections = conn.execute("SELECT count(*) FROM sections").fetchone()[0]
    if check != "ok":
        c.add("FAIL", "index integrity: %s" % check, "ns index --rebuild")
    else:
        c.add("OK", "index %s: %d notes, %d sections, %.1f MB, updated in %d ms" % (
            idx.path, notes, sections, size / 1e6, report.ms))
    if report.busy:
        c.add("WARN", "another process holds the index lock", "wait for it; if none is running, delete %s" % idx.lock_path)
    for w in report.warnings:
        if "set aside" in w:
            c.add("WARN", w)

    bad_fm = conn.execute(
        "SELECT m.grp, m.rel, f.fm_error FROM files f JOIN members m ON m.file_id=f.id "
        "WHERE f.fm_error != '' GROUP BY f.id ORDER BY m.grp, m.rel").fetchall()
    if bad_fm:
        c.add("WARN", "%d note%s with frontmatter that could not be read; indexed as plain text" % (
            len(bad_fm), "" if len(bad_fm) == 1 else "s"),
              "fix the header if its fields matter for search", ["%s:%s: %s" % r for r in bad_fm])
    dup = conn.execute(
        "SELECT m.grp, m.rel, f.fm_warnings FROM files f JOIN members m ON m.file_id=f.id "
        "WHERE f.fm_warnings NOT IN ('', '[]') GROUP BY f.id ORDER BY m.grp, m.rel").fetchall()
    if dup:
        c.add("WARN", "%d note%s with duplicate frontmatter keys" % (len(dup), "" if len(dup) == 1 else "s"), None,
              ["%s:%s: %s" % (g, r, "; ".join(json.loads(w))) for g, r, w in dup])
    partial = conn.execute(
        "SELECT m.grp, m.rel, f.size FROM files f JOIN members m ON m.file_id=f.id WHERE f.partial=1 "
        "GROUP BY f.id").fetchall()
    if partial:
        c.add("WARN", "%d note%s larger than %g MB, indexed only up to that size" % (
            len(partial), "" if len(partial) == 1 else "s", cfg.limits.max_file_mb),
              "raise limits.max_file_mb in %s if their ends matter" % cfg.path,
              ["%s:%s (%.1f MB)" % (g, r, s / 1e6) for g, r, s in partial])
    skips = conn.execute("SELECT grp, rel, reason FROM skips ORDER BY reason, grp, rel").fetchall()
    if skips:
        reasons = {}
        for g, r, why in skips:
            reasons.setdefault(why, []).append("%s:%s" % (g, r))
        for why, items in reasons.items():
            fix = None
            if why.startswith("cannot read"):
                fix = "check the files' permissions"
            elif why.startswith("link"):
                fix = "add the link's target folder to a group if it should be searched"
            c.add("WARN", "%d file%s skipped: %s" % (len(items), "" if len(items) == 1 else "s", why), fix, items)

    if args.secrets:
        found = []
        for real, grp, rel in conn.execute(
                "SELECT f.real, m.grp, m.rel FROM files f JOIN members m ON m.file_id=f.id GROUP BY f.id"):
            try:
                with open(real, "rb") as fh:
                    for no, raw in enumerate(fh, 1):
                        line = raw.decode("utf-8", errors="replace")
                        for kind, rx in _SECRETS:
                            if rx.search(line):
                                found.append("%s:%s:L%d %s" % (grp, rel, no, kind))
                                break
            except OSError:
                continue
        if found:
            c.add("WARN", "%d place%s that look like credentials (values not shown)" % (
                len(found), "" if len(found) == 1 else "s"),
                  "move the credentials out of the notes, or exclude those files from their group",
                  sorted(set(found)))
        else:
            c.add("OK", "no text that looks like credentials")
    code = 1 if c.failed else 0
    return code, c.text(), {"ok": not c.failed, "checks": c.items}
