"""`ns add`: create a note in the one folder a group allows writing to.

Writing is off unless a group names a `write` folder, and then ns only
creates new files there. It never edits a note it did not create: `--append`
extends a note ns created (marked `ns_created:`) inside that folder, and
nothing else. No deletes, no commits; the new file is indexed at once and
committing it is the person's choice.

Before writing, the group is searched for the same title and entities. Close
matches stop the write (exit 3) unless the agent says the note is different
(`--new`) or replaces one (`--supersedes <ref>`, which the old note's hits
then show).

What was observed and what was concluded stay apart: the body needs an
`Observed` or a `Concluded` heading.
"""
from __future__ import annotations

import errno
import json
import os
import re
import time

from . import refs
from .doctor import _SECRETS
from .index import Index, SUPERSEDES
from .search import Searcher

CREATED = "ns_created"
MAX_BODY = 200_000
_KEPT_APART = re.compile(r"^#{1,6}[ \t]+(observed|concluded)\b", re.I | re.M)
_HEADING = re.compile(r"^(#{1,6})([ \t]+\S.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"a", "an", "and", "at", "by", "for", "in", "is", "of", "on", "or", "the", "to", "with"}

TEMPLATE = """## Observed

What you saw: commands run, output, error text, what the person said.

## Concluded

What you worked out from it, and how sure you are."""


class Refused(Exception):
    """A write that ns will not do. `code` is the exit status."""

    def __init__(self, message, code=2, data=None):
        super().__init__(message)
        self.code = code
        self.data = data or {}


def today() -> str:
    return time.strftime("%Y-%m-%d")


def _words(text: str) -> list:
    return [w for w in _WORD.findall((text or "").lower()) if w not in _STOP]


def slug(title: str) -> str:
    return "-".join(_WORD.findall(title.lower()))[:60].strip("-") or "note"


def _quote(value: str) -> str:
    value = " ".join(str(value).split())
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')


def _field(key: str, value) -> str:
    if isinstance(value, list):
        return "%s: [%s]" % (key, ", ".join(_quote(v) for v in value))
    return "%s: %s" % (key, _quote(value))


def shift_headings(body: str, top: int) -> str:
    """Move the body's headings down so the highest is at level `top`; code blocks are left alone."""
    lines = body.split("\n")
    fenced, levels = False, []
    for line in lines:
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            m = _HEADING.match(line)
            if m:
                levels.append(len(m.group(1)))
    delta = top - min(levels) if levels else 0
    if delta <= 0:
        return body
    out, fenced = [], False
    for line in lines:
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            m = _HEADING.match(line)
            if m:
                line = "#" * min(6, len(m.group(1)) + delta) + m.group(2)
        out.append(line)
    return "\n".join(out)


def check_body(body: str) -> str:
    body = body.replace("\r\n", "\n").strip("\n")
    if not body.strip():
        raise Refused("the note's text comes on standard input, and it is empty")
    if len(body.encode("utf-8")) > MAX_BODY:
        raise Refused("the note's text is over %d KB; split it into smaller notes" % (MAX_BODY // 1000))
    if not _KEPT_APART.search(body):
        raise Refused("keep what was observed apart from what was concluded: give the text an "
                      "\"Observed\" heading, a \"Concluded\" heading, or both, like this:\n\n" + TEMPLATE)
    for n, line in enumerate(body.split("\n"), 1):
        for kind, rx in _SECRETS:
            if rx.search(line):
                raise Refused("line %d of the text looks like a credential (%s); take it out and "
                              "say where it is kept instead" % (n, kind))
    return body


def _target(group) -> tuple:
    """(absolute write folder, its group-relative prefix) — or Refused."""
    d, inside = group.write_dir()
    if d is None:
        raise Refused(inside)
    rel = d.prefix + (inside + "/" if inside else "")
    return group.write, rel


def _duplicates(conn, cfg, gid: str, title: str, entities: list) -> list:
    """Notes in the group with the same title, or sharing an entity and half the title's words."""
    want = _words(title)
    norm = " ".join(want)
    ents = {e.lower() for e in entities}
    wordings = [title] + entities[:4]
    res = Searcher(conn, cfg).search(wordings, [gid])
    out = []
    for n in res.notes:
        row = conn.execute("SELECT entities FROM files WHERE id=?", (n.file_id,)).fetchone()
        theirs = set(json.loads(row[0] or "[]")) if row else set()
        words = set(_words(n.title))
        shared_words = len(set(want) & words)
        if norm and " ".join(_words(n.title)) == norm:
            out.append({"ref": n.ref, "title": n.title, "why": "same title"})
        elif ents & theirs and want and shared_words * 2 >= len(set(want)):
            out.append({"ref": n.ref, "title": n.title,
                        "why": "shares %s; title words %d/%d" % (", ".join(sorted(ents & theirs)), shared_words, len(set(want)))})
    return out


def _indexed_ref(cfg, path: str, gid: str):
    idx = Index(cfg)
    idx.update()
    conn = idx.open()
    row = conn.execute("SELECT m.rel FROM members m JOIN files f ON f.id = m.file_id WHERE f.real=? AND m.grp=?",
                       (os.path.realpath(path), gid)).fetchone()
    idx.close()
    return "%s:%s" % (gid, row[0]) if row else None


def _create(path: str, text: str) -> bool:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o644)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            return False
        raise
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return True


def add(cfg, conn, gid: str, title: str, body: str, entities=None, tags=None, source=None,
        new=False, supersedes=None) -> dict:
    group = cfg.groups.get(gid)
    if group is None:
        raise Refused("no group %s; the groups are %s" % (gid, ", ".join(cfg.groups)))
    folder, rel_prefix = _target(group)
    title = " ".join((title or "").split())
    if not title:
        raise Refused("give the note a --title")
    body = check_body(body)
    entities = [e.strip() for e in (entities or []) if e.strip()]
    tags = [t.strip() for t in (tags or []) if t.strip()]
    warnings = []
    old = None
    if supersedes:
        r = refs.parse(supersedes)
        row = conn.execute("SELECT 1 FROM members WHERE grp=? AND rel=?", (r.grp, r.rel)).fetchone()
        if not row:
            raise Refused("--supersedes %s: no such note in the index" % supersedes)
        old = "%s:%s" % (r.grp, r.rel)
    elif not new:
        dups = _duplicates(conn, cfg, gid, title, entities)
        if dups:
            raise Refused("not written: %d close match%s in %s" % (len(dups), "" if len(dups) == 1 else "es", gid),
                          code=3, data={"duplicates": dups})
    roles = group.roles
    key = lambda role, default: (getattr(roles, role) or [default])[0]
    day = today()
    fm = [_field(key("title", "title"), title)]
    if entities:
        fm.append(_field(key("entities", "entities"), entities))
        if not roles.entities:
            warnings.append("group %s maps no field to the entities role, so the entities are written as "
                            "\"entities:\" but not ranked as entities; see the README's field roles" % gid)
    if tags:
        fm.append(_field(key("tags", "tags"), tags))
    if source:
        fm.append(_field("source", source))
    fm.append("%s: %s" % (key("date", "updated"), day))
    if old:
        fm.append(_field(SUPERSEDES, old))
        fm.append(_field(key("links", "related"), [old.split(":", 1)[1]]))
    fm.append("%s: %s" % (CREATED, day))
    # No "# Title" line: the title is in the frontmatter, and a heading with
    # nothing under it would be the section a search lands on.
    text = "---\n%s\n---\n\n%s\n" % ("\n".join(fm), shift_headings(body, 2))
    os.makedirs(folder, exist_ok=True)
    if os.path.islink(folder):
        raise Refused("the write folder %s is a symbolic link; ns writes only into a real folder" % folder)
    base = "%s-%s" % (day, slug(title))
    for n in range(1, 100):
        name = base + (".md" if n == 1 else "-%d.md" % n)
        path = os.path.join(folder, name)
        if _create(path, text):
            break
    else:
        raise Refused("%s: 99 notes with this name already exist" % base)
    ref = _indexed_ref(cfg, path, gid) or "%s:%s%s" % (gid, rel_prefix, name)
    return {"ok": True, "written": True, "ref": ref, "path": path, "supersedes": old, "warnings": warnings}


def _last_byte(path: str) -> bytes:
    with open(path, "rb") as fh:
        size = fh.seek(0, os.SEEK_END)
        if not size:
            return b""
        fh.seek(size - 1)
        return fh.read(1)


def append(cfg, conn, ref_text: str, body: str) -> dict:
    r = refs.parse(ref_text)
    group = cfg.groups.get(r.grp)
    if group is None:
        raise Refused("no group %s" % r.grp)
    folder, _ = _target(group)
    row = conn.execute("SELECT f.real FROM members m JOIN files f ON f.id = m.file_id WHERE m.grp=? AND m.rel=?",
                       (r.grp, r.rel)).fetchone()
    if not row:
        raise Refused("no note %s:%s in the index" % (r.grp, r.rel))
    path = row[0]
    real_folder = os.path.realpath(folder)
    if os.path.islink(path) or not os.path.realpath(path).startswith(real_folder.rstrip(os.sep) + os.sep):
        raise Refused("%s:%s was not created by ns (it is outside the group's write folder); "
                      "ns never changes a note it did not create" % (r.grp, r.rel))
    with open(path, encoding="utf-8", errors="replace") as fh:
        head = fh.read(4096)
    if not re.search(r"^%s:\s*\d{4}-\d{2}-\d{2}\s*$" % CREATED, head.split("\n---", 1)[0], re.M):
        raise Refused("%s:%s has no %s: field, so ns did not create it and will not change it"
                      % (r.grp, r.rel, CREATED))
    body = check_body(body)
    addition = "\n## Added %s\n\n%s\n" % (today(), shift_headings(body, 3))
    ends_with_newline = _last_byte(path) in (b"", b"\n")
    flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(("" if ends_with_newline else "\n") + addition)
    ref = _indexed_ref(cfg, path, r.grp) or "%s:%s" % (r.grp, r.rel)
    return {"ok": True, "written": True, "ref": ref, "path": path, "appended": True, "warnings": []}
