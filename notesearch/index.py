"""The index: one SQLite file, kept current without anyone running a build.

Every command first scans the groups' folders, comparing file sizes and
modification times with what the index holds, and re-reads only what
changed. Each file is updated in its own transaction, so a crash leaves the
index consistent. SQLite's write-ahead log lets searches run while another
process updates; a lock file allows one writer, and a second writer skips the
update and says so rather than waiting.

The index holds only copies of notes. A schema change, or an index SQLite
cannot read, is set aside and rebuilt; nothing is lost.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from . import config as cfglib
from . import frontmatter as fmlib
from . import names
from .split import read_note

SCHEMA = "3"
FTS_COLUMNS = ("title", "aliases", "entities", "headings", "path", "meta", "tags", "idents", "body")
BODY_COL = FTS_COLUMNS.index("body")

_DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY, real TEXT UNIQUE NOT NULL, size INT, mtime_ns INT,
  grp TEXT, rel TEXT, stem TEXT, title TEXT, fm TEXT, fm_error TEXT, fm_warnings TEXT,
  date TEXT, date_src TEXT, status TEXT, stale INT, partial INT,
  entities TEXT, aliases TEXT, pieces INT, indexed_at REAL, supersedes TEXT);
CREATE INDEX IF NOT EXISTS files_supersedes ON files(supersedes);
CREATE INDEX IF NOT EXISTS files_stem ON files(stem);
CREATE TABLE IF NOT EXISTS members(grp TEXT, rel TEXT, file_id INT, PRIMARY KEY(grp, rel));
CREATE INDEX IF NOT EXISTS members_file ON members(file_id);
CREATE TABLE IF NOT EXISTS sections(
  id INTEGER PRIMARY KEY, file_id INT, ord INT, heading TEXT, hpath TEXT,
  line_start INT, line_end INT, chars INT, byte_start INT);
CREATE INDEX IF NOT EXISTS sections_file ON sections(file_id, ord);
CREATE TABLE IF NOT EXISTS links(file_id INT, target TEXT);
CREATE INDEX IF NOT EXISTS links_target ON links(target);
CREATE INDEX IF NOT EXISTS links_file ON links(file_id);
CREATE TABLE IF NOT EXISTS vocab(id INTEGER PRIMARY KEY, term TEXT UNIQUE);
CREATE VIRTUAL TABLE IF NOT EXISTS vocab_tri USING fts5(term, tokenize='trigram');
CREATE TABLE IF NOT EXISTS binaries(real TEXT PRIMARY KEY, size INT, mtime_ns INT);
CREATE TABLE IF NOT EXISTS skips(grp TEXT, rel TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS groups_state(grp TEXT PRIMARY KEY, fingerprint TEXT, files INT, updated REAL);
"""

# The full-text table keeps no copy of the text when SQLite allows deleting
# from such a table (3.43 and newer): two thirds of the index's size. Snippets
# and `ns get` read the notes themselves. Older SQLite keeps the copy.
CONTENTLESS = sqlite3.sqlite_version_info >= (3, 43, 0)
_FTS = ("CREATE VIRTUAL TABLE IF NOT EXISTS sec_fts USING fts5("
        "title, aliases, entities, headings, path, meta, tags, idents, body, "
        "tokenize='porter unicode61 remove_diacritics 2'%s)")

_WIKI = re.compile(r"\[\[([^\]|#\n]+)(?:#[^\]|\n]*)?(?:\|[^\]\n]*)?\]\]")
_MDLINK = re.compile(r"\]\(([^)\s]+?\.(?:md|markdown))(?:#[^)\s]*)?\)", re.I)
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# The field `ns add --supersedes` writes: the reference of the note this one replaces.
SUPERSEDES = "supersedes"


class NotReady(Exception):
    """There is no usable index, and another process holds the write lock."""


@dataclass
class Report:
    added: int = 0
    changed: int = 0
    removed: int = 0
    unchanged: int = 0
    skipped: list = field(default_factory=list)  # (group, rel, reason)
    warnings: list = field(default_factory=list)
    busy: bool = False
    ms: int = 0


def stem_of(path: str) -> str:
    base = os.path.basename(path)
    for ext in cfglib.EXTENSIONS:
        if base.lower().endswith(ext):
            return base[: -len(ext)].lower()
    return base.lower()


def _under(path: str, roots: list) -> bool:
    return any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def scan(cfg) -> tuple:
    """Walk every group. Returns (seen, skipped, warnings).

    seen maps a file's real path to {size, mtime_ns, members: [(group, rel)]}.
    A file reached twice is listed once, with each group it belongs to.

    Each folder's real path is worked out once; a file's only when it is a
    symbolic link. That keeps a scan of 10,000 notes to one stat each.
    """
    seen: dict = {}
    skipped: list = []
    warnings: list = []
    for g in cfg.groups.values():
        dirs, warns = g.resolve()
        warnings.extend(warns)
        real_roots = [os.path.realpath(d.path) for d in dirs]
        taken = set()
        for d in dirs:
            visited = set()
            stack = [(d.path, os.path.realpath(d.path), "")]
            while stack:
                dirpath, real_dir, rel_dir = stack.pop()
                if real_dir in visited:
                    continue
                visited.add(real_dir)
                try:
                    entries = sorted(os.scandir(dirpath), key=lambda e: e.name)
                except OSError as exc:
                    warnings.append("group %s: cannot read %s: %s" % (g.id, dirpath, exc.strerror))
                    continue
                subdirs = []
                for e in entries:
                    name = e.name
                    if name.startswith("."):
                        continue
                    rel = (rel_dir + "/" + name) if rel_dir else name
                    try:
                        link = e.is_symlink()
                        is_dir = e.is_dir()  # follows links
                    except OSError:
                        continue
                    if is_dir:
                        if g.excluded(rel, is_dir=True):
                            continue
                        real_sub = os.path.realpath(e.path) if link else os.path.join(real_dir, name)
                        if link and not _under(real_sub, real_roots):
                            skipped.append((g.id, d.prefix + rel, "link leads outside the group's folders"))
                            continue
                        subdirs.append((e.path, real_sub, rel))
                        continue
                    if not name.lower().endswith(cfglib.EXTENSIONS) or g.excluded(rel):
                        continue
                    ref_rel = d.prefix + rel
                    real = os.path.join(real_dir, name)
                    if link:
                        real = os.path.realpath(e.path)
                        if not _under(real, real_roots):
                            skipped.append((g.id, ref_rel, "link leads outside the group's folders"))
                            continue
                    try:
                        st = e.stat()
                    except OSError as exc:
                        skipped.append((g.id, ref_rel, "cannot read: %s" % exc.strerror))
                        continue
                    entry = seen.setdefault(real, {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "members": []})
                    if any(m[0] == g.id for m in entry["members"]):
                        continue  # the same file twice in one group: keep the first path
                    if ref_rel in taken:
                        # Two folders of one group hold the same path: tell them apart.
                        ref_rel = os.path.basename(d.path.rstrip(os.sep)) + "/" + ref_rel
                    taken.add(ref_rel)
                    entry["members"].append((g.id, ref_rel))
                stack.extend(reversed(subdirs))
    return seen, skipped, warnings


class Index:
    def __init__(self, cfg):
        self.cfg = cfg
        self.dir = cfg.cache_dir
        self.path = os.path.join(self.dir, "index.db")
        self.lock_path = os.path.join(self.dir, "index.lock")
        self.conn = None
        self.notices: list = []
        self.wait = 10.0  # seconds to wait for another writer before searching without updating

    # -- opening ---------------------------------------------------------
    def _set_aside(self, why: str):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            p = self.path + suffix
            if os.path.exists(p):
                os.replace(p, "%s.%s-%s%s" % (self.path, why, stamp, suffix))
        self.notices.append("the index was %s; it was set aside and rebuilt" % (
            "from an older version" if why == "old" else "unreadable"))

    def open(self, writer: bool = False) -> sqlite3.Connection:
        """Open the index. Only the holder of the write lock (`writer`) may
        create it, or set aside one that is old or unreadable; anyone else
        gets NotReady instead, and never touches the files."""
        if self.conn is not None:
            return self.conn
        old = os.umask(0o077)
        try:
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
            self._private_dir()
            for attempt in range(2):
                conn = sqlite3.connect(self.path, timeout=15)
                try:
                    conn.execute("PRAGMA busy_timeout=15000")
                    have = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
                    if have:
                        row = conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                        if not row or row[0] != SCHEMA:
                            conn.close()
                            if not writer:
                                raise NotReady("the index is from an older version; it is rebuilt at the next update")
                            self._set_aside("old")
                            continue
                    elif not writer:
                        conn.close()
                        raise NotReady("there is no index yet")
                    else:
                        conn.execute("PRAGMA journal_mode=WAL")
                        conn.executescript(_DDL)
                        conn.execute(_FTS % (", content='', contentless_delete=1" if CONTENTLESS else ""))
                        conn.execute("INSERT OR REPLACE INTO meta VALUES('schema', ?)", (SCHEMA,))
                        conn.execute("INSERT OR REPLACE INTO meta VALUES('fts', ?)",
                                     ("contentless" if CONTENTLESS else "stored",))
                        conn.commit()
                    conn.execute("PRAGMA synchronous=NORMAL")
                    # Read through the operating system's file cache, which other
                    # ns processes have already warmed, instead of a cold private cache.
                    conn.execute("PRAGMA mmap_size=1073741824")
                    conn.execute("PRAGMA cache_size=-32000")
                    self.conn = conn
                    return conn
                except sqlite3.DatabaseError:
                    conn.close()
                    if attempt or not writer:
                        raise
                    self._set_aside("corrupt")
            raise sqlite3.DatabaseError("cannot open the index at %s" % self.path)
        finally:
            os.umask(old)

    def _private_dir(self):
        """The index holds copies of notes' words: keep its folder to its owner,
        which also covers SQLite's log files, created later with the usual mode."""
        try:
            st = os.stat(self.dir)
            if st.st_uid == os.getuid() and st.st_mode & 0o077:
                os.chmod(self.dir, 0o700)
        except OSError:
            pass

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def rebuild(self) -> bool:
        """Delete the index; the next update builds it. False if another process holds the lock."""
        self.close()
        with self._writer() as got:
            if not got:
                return False
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(self.path + suffix)
                except FileNotFoundError:
                    pass
        return True

    @contextmanager
    def _writer(self):
        old = os.umask(0o077)
        try:
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
            fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        finally:
            os.umask(old)
        deadline = time.time() + self.wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                    os.close(fd)
                    raise
                if time.time() >= deadline:
                    os.close(fd)
                    yield False
                    return
                time.sleep(0.05)
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    # -- updating --------------------------------------------------------
    def update(self) -> Report:
        start = time.time()
        report = Report()
        with self._writer() as got:
            if not got:
                report.busy = True
                report.warnings.append("another process is updating the index; results may miss the newest changes")
                try:
                    self.open()
                except (NotReady, sqlite3.DatabaseError) as exc:
                    raise NotReady("%s, and another process is building it; try again in a moment" % exc)
                return report
            try:
                self._update(report)
            except sqlite3.DatabaseError as exc:
                if "malformed" not in str(exc) and "not a database" not in str(exc):
                    raise
                self.close()
                self._set_aside("corrupt")
                report = Report()
                self._update(report)
        report.warnings = self.notices + report.warnings
        self.notices = []
        report.ms = int((time.time() - start) * 1000)
        return report

    def _update(self, report: Report):
        conn = self.open(writer=True)
        seen, skipped, warnings = scan(self.cfg)
        report.warnings.extend(warnings)
        report.skipped.extend(skipped)
        fingerprints = {gid: g.fingerprint() for gid, g in self.cfg.groups.items()}
        stored = dict(conn.execute("SELECT grp, fingerprint FROM groups_state"))
        redo = {gid for gid, fp in fingerprints.items() if stored.get(gid) != fp}
        existing = {row[1]: row for row in conn.execute("SELECT id, real, size, mtime_ns, grp, rel FROM files")}
        binaries = {row[0]: (row[1], row[2]) for row in conn.execute("SELECT real, size, mtime_ns FROM binaries")}
        self._vocab = None
        for real, info in seen.items():
            primary = info["members"][0]
            old = existing.get(real)
            if real in binaries and binaries[real] == (info["size"], info["mtime_ns"]):
                for gid, rel in info["members"]:
                    report.skipped.append((gid, rel, "binary file"))
                if old:
                    self._delete(conn, old[0])
                continue
            if old and old[2] == info["size"] and old[3] == info["mtime_ns"] and old[4] == primary[0] \
                    and old[5] == primary[1] and primary[0] not in redo:
                report.unchanged += 1
                continue
            outcome = self._index_file(conn, real, info, old[0] if old else None)
            if outcome == "binary":
                with conn:
                    conn.execute("INSERT OR REPLACE INTO binaries VALUES(?,?,?)", (real, info["size"], info["mtime_ns"]))
                for gid, rel in info["members"]:
                    report.skipped.append((gid, rel, "binary file"))
            elif outcome == "gone":
                pass
            elif isinstance(outcome, str):
                for gid, rel in info["members"]:
                    report.skipped.append((gid, rel, outcome))
            elif old:
                report.changed += 1
            else:
                report.added += 1
        for real, row in existing.items():
            if real not in seen:
                self._delete(conn, row[0])
                report.removed += 1
        ids = dict(conn.execute("SELECT real, id FROM files"))
        members = {(gid, rel, ids[real]) for real, info in seen.items() if real in ids for gid, rel in info["members"]}
        stored_members = set(conn.execute("SELECT grp, rel, file_id FROM members"))
        skips = sorted(set(report.skipped))
        stored_skips = sorted(set(conn.execute("SELECT grp, rel, reason FROM skips")))
        counts: dict = {}
        for gid, _, _ in members:
            counts[gid] = counts.get(gid, 0) + 1
        state = {(gid, fp, counts.get(gid, 0)) for gid, fp in fingerprints.items()}
        stored_state = set(conn.execute("SELECT grp, fingerprint, files FROM groups_state"))
        changed = report.added or report.changed or report.removed
        if members != stored_members or skips != stored_skips or state != stored_state or changed:
            with conn:
                if members != stored_members:
                    conn.execute("DELETE FROM members")
                    conn.executemany("INSERT OR IGNORE INTO members VALUES(?,?,?)", sorted(members))
                if binaries:
                    gone = [r for r in binaries if r not in seen]
                    conn.executemany("DELETE FROM binaries WHERE real=?", [(r,) for r in gone])
                if skips != stored_skips:
                    conn.execute("DELETE FROM skips")
                    conn.executemany("INSERT INTO skips VALUES(?,?,?)", skips)
                now = time.time()
                conn.execute("DELETE FROM groups_state")
                conn.executemany("INSERT INTO groups_state VALUES(?,?,?,?)", [
                    (gid, fp, counts.get(gid, 0), now) for gid, fp in fingerprints.items()])
                conn.execute("INSERT OR REPLACE INTO meta VALUES('updated', ?)", (str(now),))

    def _delete(self, conn, file_id: int):
        with conn:
            conn.execute("DELETE FROM sec_fts WHERE rowid IN (SELECT id FROM sections WHERE file_id=?)", (file_id,))
            conn.execute("DELETE FROM sections WHERE file_id=?", (file_id,))
            conn.execute("DELETE FROM links WHERE file_id=?", (file_id,))
            conn.execute("DELETE FROM members WHERE file_id=?", (file_id,))
            conn.execute("DELETE FROM files WHERE id=?", (file_id,))

    def _known_vocab(self, conn) -> set:
        if self._vocab is None:
            self._vocab = {row[0] for row in conn.execute("SELECT term FROM vocab")}
        return self._vocab

    def _index_file(self, conn, real: str, info: dict, file_id):
        """Index one file. Returns None, or a reason it was skipped."""
        try:
            with open(real, "rb") as fh:
                head = fh.read(8192)
        except FileNotFoundError:
            if file_id:
                self._delete(conn, file_id)
            return "gone"
        except OSError as exc:
            return "cannot read: %s" % exc.strerror
        if b"\0" in head:
            if file_id:
                self._delete(conn, file_id)
            return "binary"
        gid, rel = info["members"][0]
        roles = self.cfg.groups[gid].roles
        stem = stem_of(rel)
        max_bytes = int(self.cfg.limits.max_file_mb * 1024 * 1024)
        state = {}
        vocab_new: set = set()
        known = self._known_vocab(conn)
        targets: set = set()
        path_text = " ".join(names.parts(rel.rsplit(".", 1)[0].replace("/", " ")) or [stem])

        def setup(note):
            fm = note.fm
            state["title"] = (fmlib.values(fm, roles.title) or [""])[0].strip()
            state["aliases"] = fmlib.values(fm, roles.aliases)
            state["entities"] = fmlib.values(fm, roles.entities)
            state["tags"] = fmlib.values(fm, roles.tags)
            state["meta"] = "\n".join("%s %s" % kv for kv in fmlib.flatten(fm))
            for t in fmlib.values(fm, roles.links):
                targets.add(stem_of(t.strip().strip("[]").split("|")[0].split("#")[0]))
            for text in [state["title"]] + state["aliases"] + state["entities"] + state["tags"]:
                vocab_new.update(names.vocabulary(text))
                vocab_new.update(t.lower() for t in names.identifiers(text))
            vocab_new.update(names.vocabulary(path_text))
            vocab_new.add(stem)

        def piece(p):
            title = state.get("title") or p.doc_title or stem
            hpath = " › ".join(p.path)
            cur = conn.execute(
                "INSERT INTO sections(file_id, ord, heading, hpath, line_start, line_end, chars, byte_start) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (file_id, p.ord, p.heading, hpath, p.line_start, p.line_end, len(p.text), p.byte_start))
            conn.execute(
                "INSERT INTO sec_fts(rowid, title, aliases, entities, headings, path, meta, tags, idents, body) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (cur.lastrowid, title, " ; ".join(state.get("aliases", [])), " ; ".join(state.get("entities", [])),
                 hpath, path_text, state.get("meta", ""), " ; ".join(state.get("tags", [])),
                 names.expansions(title + "\n" + hpath + "\n" + p.text), p.text))
            vocab_new.update(names.vocabulary(p.text))
            vocab_new.update(i.lower() for i in names.identifiers(p.text))
            vocab_new.update(names.vocabulary(hpath))
            for m in _WIKI.finditer(p.text):
                targets.add(stem_of(m.group(1).strip()))
            for m in _MDLINK.finditer(p.text):
                targets.add(stem_of(m.group(1)))

        try:
            with conn:
                if file_id:
                    conn.execute("DELETE FROM sec_fts WHERE rowid IN (SELECT id FROM sections WHERE file_id=?)", (file_id,))
                    conn.execute("DELETE FROM sections WHERE file_id=?", (file_id,))
                    conn.execute("DELETE FROM links WHERE file_id=?", (file_id,))
                    conn.execute("UPDATE files SET size=?, mtime_ns=? WHERE id=?", (info["size"], info["mtime_ns"], file_id))
                else:
                    file_id = conn.execute("INSERT INTO files(real, size, mtime_ns) VALUES(?,?,?)",
                                           (real, info["size"], info["mtime_ns"])).lastrowid
                note = read_note(real, max_bytes, sink=piece, on_frontmatter=setup)
                fm = note.fm
                title = state.get("title") or note.title_h1 or stem
                date_vals = fmlib.values(fm, roles.date)
                date, date_src = "", ""
                for v in date_vals:
                    m = _DATE.search(v)
                    if m:
                        date, date_src = m.group(1), "field"
                        break
                if not date:
                    date = time.strftime("%Y-%m-%d", time.localtime(info["mtime_ns"] / 1e9))
                    date_src = "modified"
                status = (fmlib.values(fm, roles.status) or [""])[0].strip().lower() if roles.status else ""
                stale = 1 if status and status in roles.stale else 0
                pieces = conn.execute("SELECT count(*) FROM sections WHERE file_id=?", (file_id,)).fetchone()[0]
                conn.execute(
                    "UPDATE files SET grp=?, rel=?, stem=?, title=?, fm=?, fm_error=?, fm_warnings=?, date=?, date_src=?, "
                    "status=?, stale=?, partial=?, entities=?, aliases=?, pieces=?, indexed_at=?, supersedes=? WHERE id=?",
                    (gid, rel, stem, title, json.dumps(fm, ensure_ascii=False), note.fm_error,
                     json.dumps(note.fm_warnings), date, date_src, status, stale, 1 if note.partial else 0,
                     json.dumps([e.lower() for e in state.get("entities", [])]),
                     json.dumps(state.get("aliases", [])), pieces, time.time(),
                     (fmlib.values(fm, [SUPERSEDES]) or [None])[0], file_id))
                targets.discard(stem)
                conn.executemany("INSERT INTO links VALUES(?,?)", [(file_id, t) for t in targets if t])
                fresh = vocab_new - known
                for term in fresh:
                    cur = conn.execute("INSERT OR IGNORE INTO vocab(term) VALUES(?)", (term,))
                    if cur.rowcount:
                        conn.execute("INSERT INTO vocab_tri(rowid, term) VALUES(?,?)", (cur.lastrowid, term))
                known.update(fresh)
        except FileNotFoundError:
            return "gone"
        except OSError as exc:
            return "cannot read: %s" % exc.strerror
        return None
