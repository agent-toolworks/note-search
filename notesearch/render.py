"""Answers as compact text (for agents: fewest tokens) or JSON (for programs)."""
from __future__ import annotations

from collections import Counter

from .refs import tokens

SNIPPET_CHARS = 220


def _trim(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _date(note) -> str:
    return ("modified " + note.date) if note.date_src == "modified" else note.date


def note_lines(i: int, note) -> list:
    flags = []
    if note.stale:
        flags.append("STALE" + (" (%s)" % note.status if note.status else ""))
    if note.superseded_by:
        flags.append("superseded by " + ", ".join(note.superseded_by))
    if note.partial:
        flags.append("partly indexed")
    if note.stage == "relaxed":
        flags.append("partial match %d/%d words" % tuple(note.coverage))
    elif note.stage == "names":
        flags.append("matched a corrected name")
    head = "%d %s  L%d-%d  %s" % (i, note.section_ref, note.line_start, note.line_end, _date(note))
    if flags:
        head += "  [" + "; ".join(flags) + "]"
    lines = [head]
    if note.snippet:
        lines.append("  " + _trim(note.snippet, SNIPPET_CHARS))
    extra = []
    if note.sections > 1:
        extra.append("+%d more sections" % (note.sections - 1))
    if note.related:
        extra.append("related: " + ", ".join(note.related))
    if extra:
        lines.append("  " + " · ".join(extra))
    return lines


def search_text(res, budget: int, offset: int, related, q: str = "") -> tuple:
    """(text, number of notes shown). `q`, the query id, goes at the end of the first line."""
    notes = res.notes[offset:]
    by_group = Counter(n.grp for n in res.notes)
    groups = ", ".join("%s %d" % (g, by_group[g]) for g in res.groups if by_group[g])
    out = []
    if not res.notes:
        msg = "no notes match in %s" % ", ".join(res.groups)
        if res.nearest:
            msg += "; nearest names: " + ", ".join(res.nearest)
        out.append(msg + (" · q %s" % q if q else ""))
    else:
        head = "%d notes (%s) · %s · %d wording%s · %d ms" % (
            len(res.notes), groups, "+".join(res.stages) or "-", len(res.wordings),
            "" if len(res.wordings) == 1 else "s", res.ms)
        out.append(head + (" · q %s" % q if q else ""))
        if not res.strict_found:
            out.append("no strict match; partial matches only" +
                       ("; nearest names: " + ", ".join(res.nearest) if res.nearest else ""))
    # The lines that come after the notes count too: the warnings always, and
    # the "N more" line whenever a note is left out.
    tail = ["warning: " + w for w in res.warnings]
    used = tokens("\n".join(out + tail))
    more_cost = tokens("%d more: repeat with --offset %d" % (len(notes), offset + len(notes))) + 1
    shown = 0
    for i, note in enumerate(notes, offset + 1):
        note.related = related(note)
        lines = note_lines(i, note)
        cost = tokens("\n".join(lines)) + 1
        left_out = i - offset < len(notes)
        if shown and used + cost + (more_cost if left_out else 0) > budget:
            break
        out.extend(lines)
        used += cost
        shown += 1
    rest = len(notes) - shown
    if rest > 0:
        out.append("%d more: repeat with --offset %d" % (rest, offset + shown))
    for w in res.warnings:
        out.append("warning: " + w)
    return "\n".join(out), shown


def search_json(res, offset: int, limit: int, related, query: dict) -> dict:
    notes = res.notes[offset: offset + limit]
    for n in notes:
        n.related = related(n)
    return {
        "ok": True, "query": query, "stages": res.stages, "strict_found": res.strict_found,
        "total": len(res.notes), "offset": offset, "more": max(0, len(res.notes) - offset - len(notes)),
        "notes": [n.as_dict() for n in notes], "nearest": res.nearest, "warnings": res.warnings, "ms": res.ms,
    }


def get_text(got, warnings: list) -> str:
    flags = []
    if got.stale:
        flags.append("STALE")
    if got.partial:
        flags.append("partly indexed")
    head = "%s  L%d-%d  %s%s" % (got.ref, got.start, got.end, got.date, ("  [" + "; ".join(flags) + "]") if flags else "")
    out = [head]
    if got.outline:
        out.append("outline (%d lines in all; ask for a heading or a range):" % got.total_lines)
        out.extend("  " + h for h in got.outline)
        out.append("---")
    out.append(got.text)
    if got.cut_at:
        base = got.ref.split("#", 1)[0]
        out.append("[cut at line %d; continue with: ns get %s:L%d-%d]" % (got.cut_at, base, got.cut_at, got.total_lines))
    for w in warnings:
        out.append("warning: " + w)
    return "\n".join(out)
