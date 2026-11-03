"""Shared test helpers: a throwaway notes tree, config and index."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from notesearch import cli  # noqa: E402
from notesearch import config as cfglib  # noqa: E402
from notesearch.index import Index  # noqa: E402
from notesearch.search import Searcher  # noqa: E402


class Tree(unittest.TestCase):
    """A temporary folder with notes, a config and an index of its own."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-test-")
        self.cache = os.path.join(self.tmp, "cache")
        self._env = {k: os.environ.get(k) for k in ("NS_CONFIG", "NS_CACHE")}
        os.environ["NS_CACHE"] = self.cache
        os.environ.pop("NS_CONFIG", None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, rel):
        return os.path.join(self.tmp, rel)

    def write(self, rel, text, mode="w"):
        p = self.path(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, mode) as fh:
            fh.write(text)
        return p

    def config(self, groups=None, **extra):
        raw = {"groups": groups or {"notes": {"dirs": ["notes"]}}}
        raw.update(extra)
        p = self.write("ns.json", json.dumps(raw))
        self.cfg_path = p
        return p

    def load(self):
        return cfglib.load(self.cfg_path)

    def index(self):
        cfg = self.load()
        idx = Index(cfg)
        report = idx.update()
        return cfg, idx, report

    def search(self, *wordings, groups=None, **kw):
        cfg, idx, report = self.index()
        s = Searcher(idx.open(), cfg)
        res = s.search(list(wordings), cfg.group_ids(groups), **kw)
        idx.close()
        return res

    def ns(self, *argv):
        """Run the ns command; returns (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv) + ["--config", self.cfg_path])
        return code, out.getvalue(), err.getvalue()

    def ns_json(self, *argv):
        code, out, err = self.ns(*(list(argv) + ["--json"]))
        return code, json.loads(out)
