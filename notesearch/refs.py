"""References and `ns get`: one section or line range, never a whole large file.

A reference is `group:path`, `group:path#Heading` (or a heading path,
`#Incidents › 2026-10 timeouts`) or `group:path:L40-72`. The text is read
from the file itself, so it is always current, and capped at the get budget;
a cut answer says where to continue.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_RANGE = re.compile(r"^(.*):L(\d+)(?:-(\d+))?$")
CHARS_PER_TOKEN = 4


class RefError(Exception):
    pass


@dataclass
class Ref:
    grp: str
    rel: str
    heading: str = ""
    start: int = 0
    end: int = 0


def parse(ref: str) -> Ref:
    ref = ref.strip()
    if ":" not in ref:
        raise RefError("a reference is group:path, group:path#Heading or group:path:L10-40, not %r" % ref)
    grp, rest = ref.split(":", 1)
    m = _RANGE.match(rest)
    if m:
        start = int(m.group(2))
        end = int(m.group(3) or m.group(2))
        if start < 1 or end < start:
            raise RefError("line range %s is not valid" % ref)
        return Ref(grp, m.group(1), start=start, end=end)
    if "#" in rest:
        rel, heading = rest.split("#", 1)
        return Ref(grp, rel, heading=heading.strip())
    return Ref(grp, rest)


def tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


@dataclass
class Got:
    ref: str
    title: str
    date: str
    stale: bool
    partial: bool
    start: int
    end: int
    text: str
    cut_at: int = 0  # the first line not shown, when the answer was cut
    outline: list = None
    total_lines: int = 0

    def as_dict(self) -> dict:
        return {"ref": self.ref, "title": self.title, "date": self.date, "stale": self.stale,
                "partial": self.partial, "lines": [self.start, self.end], "text": self.text,
                "cut_at": self.cut_at or None, "outline": self.outline, "tokens": tokens(self.text)}


def _read_lines(path: str, start: int, end: int, budget_chars: int) -> tuple:
    """Lines start..end of a file, stopping before budget_chars. (text, last line shown)."""
    out = []
    used = 0
    last = start - 1
    with open(path, "rb") as fh:
        for no, raw in enumerate(fh, 1):
            if no < start:
                continue
            if no > end:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if no == 1 and line.startswith("﻿"):
                line = line[1:]
            if used + len(line) + 1 > budget_chars and out:
                break
            if used + len(line) + 1 > budget_chars:
                line = line[: max(0, budget_chars - used - 1)] + "…"
            out.append(line)
            used += len(line) + 1
            last = no
    return "\n".join(out), last


def get(conn, cfg, ref_text: str) -> Got:
    ref = parse(ref_text)
    if ref.grp not in cfg.groups:
        raise RefError("unknown group %s; groups are: %s" % (ref.grp, ", ".join(cfg.groups)))
    row = conn.execute(
        "SELECT f.id, f.real, f.title, f.date, f.stale, f.partial FROM members m JOIN files f ON f.id = m.file_id "
        "WHERE m.grp=? AND m.rel=?", (ref.grp, ref.rel)).fetchone()
    if not row:
        raise RefError("no note %s:%s in the index" % (ref.grp, ref.rel))
    fid, real, title, date, stale, partial = row
    budget = cfg.limits.get_tokens * CHARS_PER_TOKEN
    sections = conn.execute(
        "SELECT heading, hpath, line_start, line_end, chars FROM sections WHERE file_id=? ORDER BY ord", (fid,)).fetchall()
    note_chars = sum(s[4] for s in sections)
    sections = [s[:4] for s in sections]
    last_line = max((s[3] for s in sections), default=0)
    base = "%s:%s" % (ref.grp, ref.rel)
    outline = None
    if ref.heading:
        want = ref.heading.lower()
        first = None
        for i, (heading, hpath, a, b) in enumerate(sections):
            hp = hpath.lower()
            if heading.lower() == want or hp == want or hp.endswith(" › " + want):
                first = i
                break
        if first is None:
            heads = []
            for h, hp, _, _ in sections:
                if hp and hp not in heads:
                    heads.append(hp)
            raise RefError("no heading %r in %s; headings: %s" % (ref.heading, base, "; ".join(heads[:20]) or "none"))
        hpath = sections[first][1]
        start = sections[first][2]
        end = sections[first][3]
        for heading, hp, a, b in sections[first + 1:]:
            if hp == hpath or hp.startswith(hpath + " › "):
                end = max(end, b)
            else:
                break
    elif ref.start:
        start, end = ref.start, ref.end
    else:
        start, end = 1, last_line or 1
        heads = []
        seen = set()
        for heading, hp, a, b in sections:
            if hp and hp not in seen:
                seen.add(hp)
                heads.append("L%d %s" % (a, hp))
        # The outline helps only when the note does not fit.
        outline = heads[:60] if note_chars > budget and len(heads) > 1 else None
    outline_cost = sum(len(h) + 1 for h in outline or [])
    if outline and outline_cost < budget // 2:
        budget -= outline_cost
    elif outline:
        outline = outline[: max(1, len(outline) // 3)]
        budget -= sum(len(h) + 1 for h in outline)
    text, shown = _read_lines(real, start, end, max(200, budget))
    cut = shown + 1 if shown < end else 0
    return Got(ref=base + ("#" + ref.heading if ref.heading else ""), title=title, date=date,
               stale=bool(stale), partial=bool(partial), start=start, end=shown if shown >= start else start,
               text=text, cut_at=cut, outline=outline, total_lines=end)
