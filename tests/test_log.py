"""Suite 7: the search log and the measures read from it."""
import datetime as dt
import json
import os
import stat
import subprocess
import sys

from tests.helpers import ROOT, Tree
from notesearch import searchlog, stats

SID = "11111111-2222-4333-8444-555555555555"
SID2 = "99999999-2222-4333-8444-555555555555"
_ENV = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "CLAUDECODE", "CLAUDE_CONFIG_DIR", "HOME")


class Env(Tree):
    """Tree with Claude Code's variables under the test's control."""

    def setUp(self):
        super().setUp()
        self._claude = {k: os.environ.get(k) for k in _ENV}
        for k in _ENV[:-1]:
            os.environ.pop(k, None)
        os.environ["HOME"] = self.path("home")
        os.makedirs(self.path("home"), exist_ok=True)

    def tearDown(self):
        for k, v in self._claude.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def notes(self, **extra):
        self.config({"kb": {"dirs": ["kb"]}}, **extra)
        self.write("kb/pool.md", "# Payment gateway\n\n## Pool\nThe connection pool ran out. zebrafinch marker.\n")
        self.write("kb/orders.md", "# Orders\nOrderService writes orders_v2.\n")

    def log(self):
        p = os.path.join(self.cache, searchlog.NAME)
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]


class Log(Env):
    def test_search_and_get_lines(self):
        self.notes()
        os.environ["CLAUDE_CODE_SESSION_ID"] = SID
        os.environ["CLAUDECODE"] = "1"
        code, out, _ = self.ns("search", "connection pool", "payment pool")
        self.assertEqual(code, 0)
        q = out.splitlines()[0].rsplit(" q ", 1)[1]
        self.assertRegex(q, r"^[0-9a-f]{8}$")
        self.assertEqual(self.ns("get", "kb:pool.md#Pool", "--q", q)[0], 0)
        s, g = self.log()
        self.assertEqual(s["event"], "search")
        self.assertEqual(s["q"], q)
        self.assertEqual(s["session"], SID)
        self.assertEqual(s["project"], os.getcwd())
        self.assertEqual(s["wordings"], ["connection pool", "payment pool"])
        self.assertEqual(s["words"], [2, 2])
        self.assertEqual(s["groups"], ["kb"])
        self.assertEqual(s["refs"][0], "kb:pool.md")
        self.assertTrue(s["strict"])
        self.assertGreater(s["tokens"], 0)
        self.assertEqual(g["event"], "get")
        self.assertEqual((g["q"], g["ref"], g["note"], g["session"]), (q, "kb:pool.md#Pool", "kb:pool.md", SID))
        self.assertFalse(g["cut"])

    def test_never_holds_note_text(self):
        self.notes()
        self.ns("search", "connection pool")
        self.ns("search", "connection pool", "--json")
        self.ns("get", "kb:pool.md")
        with open(os.path.join(self.cache, searchlog.NAME), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("zebrafinch", text)
        self.assertNotIn("ran out", text)

    def test_json_search_carries_the_query_id(self):
        self.notes()
        code, data = self.ns_json("search", "connection pool")
        self.assertEqual(data["q"], self.log()[0]["q"])
        self.assertEqual(self.log()[0]["refs"], [n["ref"] for n in data["notes"]])

    def test_counts_mode_drops_the_words(self):
        self.notes(log="counts")
        os.environ["CLAUDECODE"] = "1"
        self.ns("search", "connection pool", "service=payments")
        self.ns("get", "kb:pool.md")
        s, g = self.log()
        self.assertIsNone(s["wordings"])
        self.assertEqual(s["words"], [2])
        self.assertEqual(s["filters"], {"service": None})
        self.assertIsNone(s["project"])
        self.assertIsNone(g["project"])
        self.assertEqual(g["note"], "kb:pool.md")

    def test_off_writes_nothing(self):
        self.notes(log="off")
        self.ns("search", "connection pool")
        self.ns("get", "kb:pool.md")
        self.assertEqual(self.log(), [])

    def test_private_file(self):
        self.notes()
        self.ns("search", "pool")
        mode = stat.S_IMODE(os.stat(os.path.join(self.cache, searchlog.NAME)).st_mode)
        self.assertEqual(mode, 0o600)

    def test_bad_query_id_is_not_logged(self):
        self.notes()
        self.ns("get", "kb:pool.md", "--q", "../etc")
        self.assertIsNone(self.log()[0]["q"])

    def test_no_match_is_logged(self):
        self.notes()
        self.ns("search", "kubernetes")
        s = self.log()[0]
        self.assertEqual((s["found"], s["strict"], s["refs"]), (0, False, []))

    def test_unwritable_log_does_not_fail_the_search(self):
        self.notes()
        self.ns("index")
        os.makedirs(os.path.join(self.cache, searchlog.NAME))  # a folder where the file should be
        code, out, _ = self.ns("search", "pool")
        self.assertEqual(code, 0)
        self.assertIn("kb:pool.md", out)

    def test_rotation(self):
        self.notes()
        old = searchlog.MAX_BYTES
        searchlog.MAX_BYTES = 1500
        try:
            for _ in range(12):
                self.ns("search", "connection pool")
        finally:
            searchlog.MAX_BYTES = old
        self.assertTrue(os.path.exists(os.path.join(self.cache, searchlog.OLD_NAME)))
        self.assertLessEqual(os.path.getsize(os.path.join(self.cache, searchlog.NAME)), 1500)
        entries, bad = searchlog.read(self.cache)
        self.assertEqual(bad, 0)
        self.assertGreater(len(entries), 3)

    def test_processes_at_once_do_not_mix_lines(self):
        self.notes()
        self.ns("index")
        script = ("import sys; sys.path.insert(0, %r)\n"
                  "from notesearch import config, searchlog\n"
                  "cfg = config.load(%r)\n"
                  "for i in range(200):\n"
                  "    searchlog.write(cfg, {'v': 1, 'event': 'get', 't': searchlog.now(), 'ref': 'kb:x.md#' + 'y' * 300, 'i': i})\n"
                  % (ROOT, self.cfg_path))
        procs = [subprocess.Popen([sys.executable, "-c", script]) for _ in range(6)]
        for p in procs:
            self.assertEqual(p.wait(), 0)
        entries, bad = searchlog.read(self.cache)
        self.assertEqual((len(entries), bad), (1200, 0))


class Session(Env):
    def test_from_the_variable(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = SID.upper()
        self.assertEqual(searchlog.session(), SID)

    def test_from_claude_codes_session_file(self):
        d = self.path("home/.claude/sessions")
        os.makedirs(d)
        with open(os.path.join(d, "4242.json"), "w") as fh:
            json.dump({"pid": 4242, "sessionId": SID}, fh)
        os.environ["CLAUDE_PID"] = "4242"
        self.assertEqual(searchlog.session(), SID)

    def test_second_claude_folder(self):
        d = self.path("home/.claude-work/sessions")
        os.makedirs(d)
        with open(os.path.join(d, "77.json"), "w") as fh:
            json.dump({"sessionId": SID2}, fh)
        os.environ["CLAUDE_PID"] = "77"
        self.assertEqual(searchlog.session(), SID2)

    def test_unknown(self):
        self.assertIsNone(searchlog.session())
        os.environ["CLAUDE_CODE_SESSION_ID"] = "not-a-session"
        os.environ["CLAUDE_PID"] = "../../etc/passwd"
        self.assertIsNone(searchlog.session())

    def test_project_only_inside_claude_code(self):
        self.assertIsNone(searchlog.project())
        os.environ["CLAUDECODE"] = "1"
        self.assertEqual(searchlog.project(), os.getcwd())


T0 = dt.datetime(2026, 11, 1, 9, 0, tzinfo=dt.timezone.utc)


def at(minutes):
    return (T0 + dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def search(m, q, refs, session=SID, offset=0, strict=True, wordings=("pool",), stages=("strict",), groups=("kb",)):
    return {"v": 1, "event": "search", "t": at(m), "q": q, "session": session, "project": "/p",
            "groups": list(groups), "wordings": list(wordings), "words": [1], "filters": {}, "since": None,
            "stages": list(stages), "strict": strict, "found": len(refs) + offset, "offset": offset,
            "refs": list(refs), "tokens": 300, "ms": 5, "warnings": 0}


def get(m, note, q=None, session=SID):
    return {"v": 1, "event": "get", "t": at(m), "q": q, "session": session, "project": "/p",
            "ref": note, "note": note, "tokens": 200, "cut": False}


class Measures(Tree):
    def entries(self, *lines):
        os.makedirs(self.cache, exist_ok=True)
        with open(os.path.join(self.cache, searchlog.NAME), "w") as fh:
            for e in lines:
                fh.write(json.dumps(e) + "\n")
        entries, _ = searchlog.read(self.cache, now_dt=T0 + dt.timedelta(days=1))
        return stats.link(entries)

    def test_opened_by_query_id(self):
        s, g = self.entries(search(0, "aaaaaaaa", ["kb:a.md", "kb:b.md"]), get(1, "kb:b.md", q="aaaaaaaa", session=None))
        self.assertTrue(s[0].opened)
        self.assertEqual(s[0].ranks, [2])

    def test_opened_by_session(self):
        s, _ = self.entries(search(0, "aaaaaaaa", ["kb:a.md"]), search(5, "bbbbbbbb", ["kb:c.md"]), get(6, "kb:a.md"))
        self.assertTrue(s[0].opened)
        self.assertFalse(s[1].opened)

    def test_not_opened_from_another_session_or_too_late(self):
        s, _ = self.entries(search(0, "aaaaaaaa", ["kb:a.md"]), get(1, "kb:a.md", session=SID2),
                            get(30, "kb:a.md"))
        self.assertFalse(s[0].opened)

    def test_pages_are_one_search(self):
        s, _ = self.entries(search(0, "aaaaaaaa", ["kb:a.md"]),
                            search(1, "bbbbbbbb", ["kb:z.md"], offset=6),
                            get(2, "kb:z.md", q="bbbbbbbb"))
        self.assertEqual(len(s), 1)
        self.assertTrue(s[0].opened)
        self.assertEqual(s[0].ranks, [7])

    def test_searched_again(self):
        s, _ = self.entries(search(0, "aaaaaaaa", ["kb:a.md"], wordings=["pool"]),
                            search(1, "bbbbbbbb", ["kb:b.md"], wordings=["pool exhausted"]),
                            search(10, "cccccccc", ["kb:b.md"], wordings=["orders"]),
                            search(10.5, "dddddddd", ["kb:b.md"], session=None),
                            search(11, "eeeeeeee", ["kb:b.md"], session=None))
        self.assertEqual([x.again for x in s], [True, False, False, False, False])
        m = stats.measures(s, [], T0 - dt.timedelta(days=1))
        self.assertEqual(m["again_rate"], 33)  # unknown sessions don't count either way

    def test_measures(self):
        s, g = self.entries(
            search(0, "aaaaaaaa", ["kb:a.md", "kb:b.md"]), get(1, "kb:a.md"),
            search(5, "bbbbbbbb", [], strict=False, stages=["strict", "relaxed"]),
            search(9, "cccccccc", ["kb:c.md"], strict=False, stages=["strict", "relaxed"]), get(9.5, "kb:c.md"),
            search(20, "dddddddd", ["kb:d.md"], stages=["strict", "names"]))
        m = stats.measures(s, g, T0 - dt.timedelta(days=1))
        self.assertEqual(m["searches"], 4)
        self.assertEqual(m["found_nothing"], 1)
        self.assertEqual(m["no_strict"], 2)
        self.assertEqual(m["open_rate"], 67)
        self.assertEqual(m["first_opened"], 100)
        self.assertEqual((m["relaxed"], m["relaxed_open_rate"]), (1, 100))
        self.assertEqual((m["names"], m["names_open_rate"]), (1, 0))
        self.assertEqual(m["tokens_get"], 200)

    def test_empty_log(self):
        m = stats.measures([], [], T0)
        self.assertEqual((m["searches"], m["open_rate"], m["again_rate"]), (0, None, None))

    def test_reader_skips_bad_and_old_lines(self):
        os.makedirs(self.cache, exist_ok=True)
        with open(os.path.join(self.cache, searchlog.NAME), "w") as fh:
            fh.write(json.dumps(search(0, "aaaaaaaa", [])) + "\n")
            fh.write("{cut sho\n")
            fh.write(json.dumps({"v": 99, "event": "search", "t": at(0)}) + "\n")
            fh.write(json.dumps(dict(search(0, "bbbbbbbb", []), t="2020-01-01T00:00:00Z")) + "\n")
            fh.write("[1, 2]\n")
        entries, bad = searchlog.read(self.cache, now_dt=T0)
        self.assertEqual(([e["q"] for e in entries], bad), (["aaaaaaaa"], 3))
