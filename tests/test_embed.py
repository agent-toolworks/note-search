"""The optional embeddings engine: search by meaning, merged with the words' search.

No model and no network: an HTTP provider talks to a toy server on
localhost, and the fastembed provider to a stand-in package
(tests/fake_fastembed) that gives the same toy vectors.
"""
import json
import os
import stat
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from tests.helpers import ROOT, Tree
from tests.toy_vectors import vector
from notesearch import config as cfglib
from notesearch import embed, searchlog, stats
from notesearch.index import Index
from notesearch.search import Searcher

FAKE = os.path.join(ROOT, "tests", "fake_fastembed")


class ToyServer:
    """OpenAI's POST /embeddings, answered with toy vectors."""

    def __init__(self):
        calls = self.calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(body)
                if self.path != "/v1/embeddings":
                    self.send_response(404)
                    self.end_headers()
                    return
                data = [{"index": i, "embedding": vector(t)} for i, t in enumerate(body["input"])]
                out = json.dumps({"data": data}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/v1" % self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Base(Tree):
    def setUp(self):
        super().setUp()
        self.server = ToyServer()
        self.addCleanup(self.server.stop)
        self.write("kb/payment-gateway.md", "# Payment gateway\nTakes card payments for checkout.\n\n"
                                            "## Pool\nThe connection pool ran out under retry storms.\n")
        self.write("kb/logout.md", "# Everyone signed out\nAll users were logged out at once.\n")
        self.write("kb/janitor.md", "# Outbox janitor\nDeletes old rows nightly.\n")
        self.write("mem/handoff.md", "# Handoff\nThe staging database was refreshed.\n")

    def setup(self, engine="embeddings", mem_engine="fts5", **settings):
        s = {"provider": "http", "url": self.server.url, "model": "toy"}
        s.update(settings)
        self.config({"kb": {"dirs": ["kb"], "engine": engine}, "mem": {"dirs": ["mem"], "engine": mem_engine}},
                    embeddings=s)

    def run_search(self, *wordings, groups=None, seconds=None):
        cfg = self.load()
        idx = Index(cfg)
        report = idx.update(embed_seconds=seconds)
        s = Searcher(idx.open(), cfg)
        res = s.search(list(wordings), cfg.group_ids(groups))
        idx.close()
        return res, report


class Config(Base):
    def problems(self, raw):
        self.write("x.json", json.dumps(raw))
        try:
            cfglib.load(self.path("x.json"))
        except cfglib.ConfigError as exc:
            return "; ".join(exc.problems)
        return ""

    def test_engine_and_settings(self):
        self.assertEqual(self.problems({"groups": {"g": {"dirs": ["x"], "engine": "embeddings"}}}), "")
        self.assertIn("engine", self.problems({"groups": {"g": {"dirs": ["x"], "engine": "qmd"}}}))
        g = {"groups": {"g": {"dirs": ["x"]}}}
        self.assertIn("unknown key", self.problems(dict(g, embeddings={"modle": "x"})))
        self.assertIn("provider", self.problems(dict(g, embeddings={"provider": "magic"})))
        self.assertIn("url", self.problems(dict(g, embeddings={"provider": "http"})))
        self.assertIn("timeout", self.problems(dict(g, embeddings={"timeout": -1})))

    def test_defaults_and_known_prefixes(self):
        s = embed.parse_settings({}, "/", [])
        self.assertEqual((s.provider, s.model, s.doc_prefix), ("fastembed", embed.DEFAULT_MODEL, ""))
        self.assertTrue(s.query_prefix.startswith("Represent this sentence"))
        s = embed.parse_settings({"provider": "http", "url": "http://x", "model": "nomic-embed-text"}, "/", [])
        self.assertEqual((s.query_prefix, s.doc_prefix), ("search_query: ", "search_document: "))
        s = embed.parse_settings({"model": "nomic-embed-text", "query_prefix": ""}, "/", [])
        self.assertEqual(s.query_prefix, "")

    def test_turning_the_engine_on_reads_the_group_again(self):
        self.setup(engine="fts5")
        cfg, idx, report = self.index()
        idx.close()
        self.assertEqual(report.added, 4)
        self.setup()
        cfg, idx, report = self.index()
        self.assertEqual(report.changed, 3)  # kb's notes, now with vectors
        self.assertEqual(report.unchanged, 1)
        self.assertEqual(idx.open().execute("SELECT count(*) FROM sec_embed").fetchone()[0], 4)
        idx.close()


class Meaning(Base):
    def test_finds_by_meaning_what_words_miss(self):
        self.setup()
        res, _ = self.run_search("customers were billed")
        self.assertEqual(res.notes[0].ref, "kb:payment-gateway.md")
        self.assertEqual(res.notes[0].stage, "semantic")
        self.assertFalse(res.strict_found)
        self.assertIn("semantic", res.stages)

    def test_words_alone_miss_it(self):
        self.setup(engine="fts5")
        res, _ = self.run_search("customers were billed")
        self.assertEqual(res.notes, [])

    def test_a_word_match_stays_strict_and_first(self):
        self.setup()
        res, _ = self.run_search("connection pool")
        self.assertEqual(res.notes[0].ref, "kb:payment-gateway.md")
        self.assertEqual(res.notes[0].stage, "strict")
        self.assertEqual(res.notes[0].heading, "Pool")
        self.assertTrue(res.strict_found)

    def test_only_embedded_groups(self):
        self.setup()
        res, _ = self.run_search("customers were billed", groups=["mem"])
        self.assertEqual(res.notes, [])
        self.assertNotIn("semantic", res.stages)

    def test_answer_says_meaning_only(self):
        self.setup()
        code, out, err = self.ns("search", "kicked off the site")
        self.assertEqual(code, 0, err)
        self.assertIn("kb:logout.md", out)
        self.assertIn("no strict match; matches by meaning only", out)
        self.assertIn("close in meaning, not in words", out)

    def test_wordings_are_embedded_as_queries(self):
        self.setup(query_prefix="Q: ", doc_prefix="D: ")
        self.run_search("billing")
        inputs = [t for c in self.server.calls for t in c["input"]]
        self.assertIn("Q: billing", inputs)
        self.assertTrue(any(t.startswith("D: Payment gateway") for t in inputs))

    def test_log_holds_the_stage_and_no_text(self):
        self.setup()
        self.ns("search", "customers were billed")
        with open(searchlog.path(self.load())) as fh:
            e = json.loads(fh.readline())
        self.assertEqual(e["stages"], ["semantic"])
        self.assertFalse(e["strict"])
        self.assertNotIn("card payments", json.dumps(e))


class Queue(Base):
    def test_budget_leaves_a_queue_that_ns_index_empties(self):
        self.setup()
        res, report = self.run_search("customers were billed", seconds=0)
        self.assertEqual(report.embedded, 0)
        self.assertEqual(report.embed_waiting, 4)
        self.assertTrue(any("not embedded yet" in w for w in report.warnings))
        self.assertNotIn("semantic", res.stages)
        code, data = self.ns_json("index")
        self.assertEqual((data["embedded"], data["embed_waiting"]), (4, 0))

    def test_only_changed_sections_are_embedded_again(self):
        self.setup()
        self.ns("index")
        before = len(self.server.calls)
        code, data = self.ns_json("index")
        self.assertEqual(data["embedded"], 0)
        self.assertEqual(len(self.server.calls), before)
        self.write("kb/logout.md", "# Everyone signed out\nAll users were logged out at once. Again.\n")
        code, data = self.ns_json("index")
        self.assertEqual(data["embedded"], 1)

    def test_ns_index_drops_vectors_no_note_uses(self):
        self.setup()
        self.ns("index")
        self.write("kb/janitor.md", "# Outbox janitor\nDeletes old rows every night.\n")
        self.ns("index")
        cfg, idx, _ = self.index()
        conn = idx.open()
        embed.attach(conn, cfg, create=False)
        self.assertEqual(conn.execute("SELECT count(*) FROM vdb.vec WHERE key NOT IN "
                                      "(SELECT key FROM sec_embed)").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT count(*) FROM vdb.vec").fetchone()[0], 4)
        idx.close()

    def test_vectors_outlive_a_rebuild(self):
        self.setup()
        self.ns("index")
        code, data = self.ns_json("index", "--rebuild")
        self.assertEqual(data["embedded"], 0)
        res, _ = self.run_search("customers were billed")
        self.assertEqual(res.notes[0].ref, "kb:payment-gateway.md")

    def test_a_deleted_note_leaves_no_hit(self):
        self.setup()
        self.ns("index")
        os.remove(self.path("kb/payment-gateway.md"))
        res, _ = self.run_search("customers were billed")
        self.assertNotIn("kb:payment-gateway.md", [n.ref for n in res.notes])

    def test_vector_file_is_private(self):
        self.setup()
        self.ns("index")
        mode = os.stat(os.path.join(self.cache, embed.VECTORS)).st_mode
        self.assertEqual(stat.S_IMODE(mode) & 0o077, 0)


class Unavailable(Base):
    def test_server_down_searches_by_words_with_one_warning(self):
        self.setup(url="http://127.0.0.1:9/v1")
        code, out, err = self.ns("search", "connection pool")
        self.assertEqual(code, 0, err)
        self.assertIn("kb:payment-gateway.md", out)
        warns = [line for line in out.splitlines() if "search by meaning is unavailable" in line]
        self.assertEqual(len(warns), 1, out)
        self.assertIn("cannot reach", warns[0])

    def test_wrong_path_says_so(self):
        self.setup(url=self.server.url + "/nope")
        res, report = self.run_search("connection pool")
        self.assertTrue(any("answered 404" in w for w in report.warnings))
        self.assertEqual(res.notes[0].ref, "kb:payment-gateway.md")

    def test_doctor_names_the_fix(self):
        self.setup(url="http://127.0.0.1:9/v1")
        code, data = self.ns_json("doctor")
        checks = [c for c in data["checks"] if "embeddings" in c["check"]]
        self.assertTrue(checks)
        self.assertEqual(checks[0]["status"], "FAIL")
        self.assertIn("ollama", checks[0]["fix"])

    def test_doctor_reports_a_working_engine(self):
        self.setup()
        code, data = self.ns_json("doctor")
        checks = {c["check"]: c["status"] for c in data["checks"] if "embedd" in c["check"]}
        self.assertTrue(checks and all(s == "OK" for s in checks.values()), checks)


class Fastembed(Base):
    def wrapper(self, *extra):
        """A Python that has the stand-in fastembed: the worker runs in it."""
        p = self.write("py/python", "#!/bin/sh\nPYTHONPATH=\"%s:$PYTHONPATH\" exec \"%s\" \"$@\"\n" % (
            FAKE, sys.executable))
        os.chmod(p, 0o755)
        return p

    def test_worker_in_another_python(self):
        self.setup(provider="fastembed", model="toy/model", python=self.wrapper())
        res, report = self.run_search("customers were billed")
        self.assertEqual(report.warnings, [])
        self.assertEqual(res.notes[0].ref, "kb:payment-gateway.md")
        self.assertEqual(res.notes[0].stage, "semantic")

    def test_worker_reports_a_missing_model(self):
        self.setup(provider="fastembed", model="missing/model", python=self.wrapper())
        res, report = self.run_search("connection pool")
        self.assertTrue(any("ns index --download" in w for w in report.warnings), report.warnings)
        self.assertEqual(res.notes[0].stage, "strict")

    def test_missing_python(self):
        self.setup(provider="fastembed", python=self.path("nope/python"))
        res, report = self.run_search("connection pool")
        self.assertTrue(any("does not exist" in w for w in report.warnings), report.warnings)

    @unittest.skipIf(any(os.path.isdir(os.path.join(p, "fastembed")) for p in sys.path if p), "fastembed is installed")
    def test_not_installed_here(self):
        self.setup(provider="fastembed")
        res, report = self.run_search("connection pool")
        w = " ".join(report.warnings)
        self.assertIn("fastembed is not installed", w)
        self.assertIn("pip install fastembed", w)


class Ranking(unittest.TestCase):
    def test_numpy_and_plain_python_agree(self):
        rows = [(i, i, embed.pack(v)) for i, v in enumerate([[1, 0, 0], [0.9, 0.1, 0], [0, 1, 0], [0.5, 0.5, 0]])]
        q = [[1, 0.05, 0]]
        plain = os.environ.get("NS_NO_NUMPY")
        os.environ["NS_NO_NUMPY"] = "1"
        try:
            a = embed.rank(rows, q, k=3, spread=1.0)
        finally:
            if plain is None:
                os.environ.pop("NS_NO_NUMPY")
        self.assertEqual([r[0] for r in a[0]], [0, 1, 3])
        if embed._numpy() is not None:
            b = embed.rank(rows, q, k=3, spread=1.0)
            self.assertEqual([r[0] for r in b[0]], [0, 1, 3])

    def test_spread_drops_the_far_ones(self):
        rows = [(i, i, embed.pack(v)) for i, v in enumerate([[1, 0], [0.95, 0.3], [0, 1]])]
        got = embed.rank(rows, [[1, 0]], k=10, spread=0.1)
        self.assertEqual([r[0] for r in got[0]], [0, 1])


class DefaultInstall(unittest.TestCase):
    def test_words_only_search_loads_no_engine(self):
        code = ("import sys; sys.path.insert(0, %r); from notesearch import cli, search, index; "
                "print(sorted(m for m in ('numpy', 'fastembed') if m in sys.modules))" % ROOT)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), "[]")


class Measures(unittest.TestCase):
    def test_semantic_searches_are_counted(self):
        import datetime as dt
        t = dt.datetime(2026, 11, 4, tzinfo=dt.timezone.utc)
        mk = lambda stages, strict: {"event": "search", "_t": t, "stages": stages, "strict": strict, "found": 2,
                                     "refs": ["kb:a.md"], "session": "s"}
        searches, _ = stats.link([mk(["strict", "semantic"], True), mk(["semantic"], False),
                                  mk(["relaxed"], False)])
        m = stats.measures(searches, [], t - dt.timedelta(days=1))
        self.assertEqual((m["semantic"], m["relaxed"]), (2, 1))


if __name__ == "__main__":
    unittest.main()
