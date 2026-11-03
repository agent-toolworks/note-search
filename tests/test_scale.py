"""Suite 5: scale and cost.

1,000 generated notes run with every change, under limits loose enough for a
slow machine. NS_BENCH=1 also runs 10,000 notes against the design's limits
(scripts/bench prints the numbers).
"""
import os
import random
import time
import unittest

from tests.helpers import Tree
from notesearch import render
from notesearch.gen import generate
from notesearch.index import Index
from notesearch.refs import tokens
from notesearch.search import Searcher


class Scale(Tree):
    COUNT = 1000
    # Loose enough for a slow machine. The random searches are a stress test:
    # up to three wordings of up to five unrelated words; 95% of them are held
    # to the search limit, and the slowest to three times it.
    LIMITS = {"build_s": 30, "search_ms": 300, "update10_ms": 2000, "noop_ms": 1000}

    def setUp(self):
        super().setUp()
        self.paths = generate(self.path("notes"), self.COUNT, big_every=500)
        self.config({"notes": {"dirs": ["notes/*"], "fields": {"title": "name", "entities": "service"}}},
                    limits={"answer_tokens": 600, "get_tokens": 1500})

    def test_limits_and_budget(self):
        cfg = self.load()
        t = time.time()
        Index(cfg).update()
        build = time.time() - t
        self.assertLess(build, self.LIMITS["build_s"])
        t = time.time()
        Index(cfg).update()
        self.assertLess((time.time() - t) * 1000, self.LIMITS["noop_ms"])
        for p in self.paths[:10]:
            with open(p, "a") as fh:
                fh.write("\nAppended.\n")
            st = os.stat(p)
            os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
        t = time.time()
        report = Index(cfg).update()
        self.assertLess((time.time() - t) * 1000, self.LIMITS["update10_ms"])
        self.assertEqual(report.changed, 10)

        idx = Index(cfg)
        s = Searcher(idx.open(), cfg)
        rnd = random.Random(3)
        words = [w for (w,) in idx.open().execute("SELECT term FROM vocab ORDER BY id LIMIT 3000")]
        times = []
        for i in range(150):
            wordings = [" ".join(rnd.choice(words) for _ in range(rnd.randint(1, 5))) for _ in range(rnd.randint(1, 3))]
            t = time.time()
            res = s.search(wordings, ["notes"])
            times.append((time.time() - t) * 1000)
            text, shown = render.search_text(res, cfg.limits.answer_tokens, 0, lambda n: s.related(n.file_id, ["notes"], 2))
            self.assertLessEqual(tokens(text), cfg.limits.answer_tokens, wordings)
            if res.notes:
                self.assertGreaterEqual(shown, 1)
        times.sort()
        p95 = times[int(len(times) * 0.95) - 1]
        self.assertLess(p95, self.LIMITS["search_ms"], "95th percentile %.0f ms" % p95)
        self.assertLess(times[-1], 3 * self.LIMITS["search_ms"], "slowest %.0f ms" % times[-1])


@unittest.skipUnless(os.environ.get("NS_BENCH"), "set NS_BENCH=1 to run 10,000 notes")
class Bench(Scale):
    COUNT = 10000
    LIMITS = {"build_s": 60, "search_ms": 110, "update10_ms": 1000, "noop_ms": 300}

    def setUp(self):
        Tree.setUp(self)
        self.paths = generate(self.path("notes"), self.COUNT)
        self.config({"notes": {"dirs": ["notes/*"], "fields": {"title": "name", "entities": "service"}}})
