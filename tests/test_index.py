"""Suite 3: keeping the index current."""
import fcntl
import os
import shutil
import subprocess
import sys
import time
from unittest import mock

from tests.helpers import ROOT, Tree
from notesearch import index as indexmod
from notesearch.index import Index


def refs(res):
    return [n.ref for n in res.notes]


class Current(Tree):
    def setUp(self):
        super().setUp()
        self.config({"a": {"dirs": ["a"]}, "b": {"dirs": ["b"]}})
        self.write("a/one.md", "# One\nThe zebrafish tank is cleaned weekly.")
        self.write("a/two.md", "# Two\nThe aquarium pump is replaced yearly.")

    def bump(self, rel, text):
        """Write and move the modification time on, so a fast rewrite is still seen as a change."""
        p = self.write(rel, text)
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))

    def test_add_edit_rename_delete(self):
        self.assertEqual(refs(self.search("zebrafish")), ["a:one.md"])
        self.write("a/three.md", "# Three\nzebrafish food schedule")
        self.assertEqual(sorted(refs(self.search("zebrafish"))), ["a:one.md", "a:three.md"])
        self.bump("a/one.md", "# One\nNothing about fish now.")
        self.assertEqual(refs(self.search("zebrafish")), ["a:three.md"])
        os.rename(self.path("a/three.md"), self.path("a/renamed.md"))
        self.assertEqual(refs(self.search("zebrafish")), ["a:renamed.md"])
        os.remove(self.path("a/renamed.md"))
        self.assertEqual(refs(self.search("zebrafish")), [])

    def test_unchanged_files_are_not_read_again(self):
        self.index()
        cfg, idx, report = self.index()
        self.assertEqual((report.added, report.changed, report.unchanged), (0, 0, 2))

    def test_move_between_groups(self):
        self.assertEqual(refs(self.search("zebrafish", groups=["a", "b"])), ["a:one.md"])
        os.makedirs(self.path("b"), exist_ok=True)
        shutil.move(self.path("a/one.md"), self.path("b/one.md"))
        self.assertEqual(refs(self.search("zebrafish", groups=["a", "b"])), ["b:one.md"])
        self.assertEqual(refs(self.search("zebrafish", groups=["a"])), [])

    def test_folder_appearing_and_disappearing_under_a_wildcard(self):
        self.config({"m": {"dirs": ["projects/*/memory"]}})
        self.write("projects/alpha/memory/x.md", "# X\nquokka sightings")
        self.write("projects/beta/memory/y.md", "# Y\nwombat burrows")
        self.assertEqual(refs(self.search("quokka")), ["m:alpha/x.md"])
        self.write("projects/gamma/memory/z.md", "# Z\nquokka again")
        self.assertEqual(sorted(refs(self.search("quokka"))), ["m:alpha/x.md", "m:gamma/z.md"])
        shutil.rmtree(self.path("projects/alpha"))
        self.assertEqual(refs(self.search("quokka")), ["m:gamma/z.md"])

    def test_missing_folder_is_a_warning(self):
        shutil.rmtree(self.path("a"))
        cfg, idx, report = self.index()
        self.assertTrue(any("does not exist" in w for w in report.warnings))

    def test_field_roles_change_reindexes_the_group(self):
        self.write("a/fm.md", "---\nheadline: Custom Title\n---\nbody text")
        cfg, idx, _ = self.index()
        title = idx.open().execute("SELECT title FROM files WHERE rel='fm.md'").fetchone()[0]
        self.assertEqual(title, "fm")
        idx.close()
        self.config({"a": {"dirs": ["a"], "fields": {"title": "headline"}}, "b": {"dirs": ["b"]}})
        cfg, idx, report = self.index()
        title = idx.open().execute("SELECT title FROM files WHERE rel='fm.md'").fetchone()[0]
        self.assertEqual(title, "Custom Title")
        self.assertEqual(report.changed, 3)


class Writers(Tree):
    def setUp(self):
        super().setUp()
        self.config()
        for i in range(30):
            self.write("notes/n%02d.md" % i, "# Note %d\nshared word platypus %d\n" % (i, i))

    def test_second_writer_skips_and_says_so(self):
        cfg, idx, _ = self.index()
        fd = os.open(idx.lock_path, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            self.write("notes/new.md", "# New\nplatypus")
            other = Index(cfg)
            other.wait = 0.2
            report = other.update()
            self.assertTrue(report.busy)
            self.assertTrue(any("another process" in w for w in report.warnings))
            # Searching still works on the index as it was.
            self.assertEqual(len(self.search_no_update("platypus")), 30)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertEqual(len(self.search("platypus").notes), 31)

    def search_no_update(self, word):
        from notesearch.search import Searcher
        cfg = self.load()
        idx = Index(cfg)
        s = Searcher(idx.open(), cfg)
        return s.search([word], ["notes"], limit=100).notes

    def test_two_processes_at_once(self):
        cmd = [sys.executable, os.path.join(ROOT, "bin", "ns"), "index", "--config", self.cfg_path]
        procs = [subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy())
                 for _ in range(4)]
        outs = [p.communicate(timeout=60) for p in procs]
        self.assertEqual([p.returncode for p in procs], [0, 0, 0, 0], outs)
        self.assertEqual(len(self.search("platypus").notes), 30)
        cfg, idx, _ = self.index()
        self.assertEqual(idx.open().execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_crash_mid_update_leaves_a_consistent_index(self):
        real = indexmod.read_note
        calls = [0]

        def flaky(*a, **kw):
            calls[0] += 1
            if calls[0] == 10:
                # Feed a piece, then fail: the file's transaction must roll back.
                kw["sink"](_Piece())
                raise RuntimeError("simulated crash")
            return real(*a, **kw)

        with mock.patch.object(indexmod, "read_note", flaky):
            with self.assertRaises(RuntimeError):
                self.index()
        cfg, idx, _ = self.index()
        conn = idx.open()
        orphans = conn.execute("SELECT count(*) FROM sections WHERE file_id NOT IN (SELECT id FROM files)").fetchone()[0]
        empty = conn.execute("SELECT count(*) FROM files WHERE title IS NULL").fetchone()[0]
        self.assertEqual((orphans, empty), (0, 0))
        self.assertEqual(len(self.search("platypus").notes), 30)


class _Piece:
    ord = 0
    path = ("X",)
    line_start = 1
    line_end = 1
    text = "half written"
    doc_title = ""
    heading = "X"
    byte_start = 0


class Damage(Tree):
    def setUp(self):
        super().setUp()
        self.config()
        self.write("notes/a.md", "# A\nnarwhal")

    def test_corrupt_index_is_set_aside_and_rebuilt(self):
        cfg, idx, _ = self.index()
        idx.close()
        for suffix in ("-wal", "-shm"):
            if os.path.exists(idx.path + suffix):
                os.remove(idx.path + suffix)
        with open(idx.path, "wb") as fh:
            fh.write(b"this is not a database" * 100)
        cfg, idx, report = self.index()
        self.assertTrue(any("unreadable" in w for w in report.warnings), report.warnings)
        self.assertEqual(len(self.search("narwhal").notes), 1)
        self.assertTrue(any("corrupt" in f for f in os.listdir(self.cache)))

    def test_schema_change_rebuilds(self):
        cfg, idx, _ = self.index()
        conn = idx.open()
        conn.execute("UPDATE meta SET value='0' WHERE key='schema'")
        conn.commit()
        idx.close()
        cfg, idx, report = self.index()
        self.assertTrue(any("older version" in w for w in report.warnings), report.warnings)
        self.assertEqual(len(self.search("narwhal").notes), 1)

    def test_index_is_private(self):
        os.makedirs(self.cache, mode=0o755)
        os.chmod(self.cache, 0o755)
        cfg, idx, _ = self.index()
        self.assertEqual(os.stat(idx.path).st_mode & 0o077, 0)
        self.assertEqual(os.stat(self.cache).st_mode & 0o077, 0)
