"""Suite 4: groups and configuration."""
import json
import os

from tests.helpers import Tree
from notesearch import config as cfglib


def refs(res):
    return sorted(n.ref for n in res.notes)


class Groups(Tree):
    def test_one_folder_in_two_groups(self):
        self.config({"a": {"dirs": ["shared"]}, "b": {"dirs": ["shared", "other"]}})
        self.write("shared/s.md", "# S\naxolotl")
        self.write("other/o.md", "# O\naxolotl too")
        self.assertEqual(refs(self.search("axolotl", groups=["a"])), ["a:s.md"])
        self.assertEqual(refs(self.search("axolotl", groups=["b"])), ["b:o.md", "b:s.md"])
        cfg, idx, _ = self.index()
        self.assertEqual(idx.open().execute("SELECT count(*) FROM files").fetchone()[0], 2)  # indexed once

    def test_same_path_in_two_folders_of_one_group(self):
        self.config({"g": {"dirs": ["one", "two"]}})
        self.write("one/README.md", "# R\ncapybara")
        self.write("two/README.md", "# R\ncapybara")
        self.assertEqual(refs(self.search("capybara")), ["g:README.md", "g:two/README.md"])

    def test_excludes(self):
        self.config({"g": {"dirs": ["n"], "exclude": ["**/archive/**", "*.draft.md", "build/", "docs/old-*.md"]}})
        for rel in ["keep.md", "archive/x.md", "deep/archive/y.md", "a.draft.md", "deep/b.draft.md",
                    "build/z.md", "docs/old-1.md", "docs/new.md", "sub/docs/old-2.md"]:
            self.write("n/" + rel, "# T\nmeerkat")
        self.assertEqual(refs(self.search("meerkat")),
                         ["g:docs/new.md", "g:keep.md", "g:sub/docs/old-2.md"])

    def test_home_and_wildcards(self):
        home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp
        try:
            self.config({"m": {"dirs": ["~/p/*/memory"]}})
            self.write("p/-Users-me-git-alpha/memory/x.md", "# X\nokapi")
            self.write("p/-Users-me-git-beta/memory/y.md", "# Y\nokapi")
            self.write("p/-Users-me-git-gamma/memory/z.md", "# Z\nokapi")
            self.write("p/-private-tmp-1/memory/w.md", "# W\nokapi")
            self.assertEqual(refs(self.search("okapi")),
                             ["m:alpha/x.md", "m:beta/y.md", "m:gamma/z.md", "m:private-tmp-1/w.md"])
        finally:
            os.environ["HOME"] = home

    def test_short_names(self):
        f = cfglib._short_names
        self.assertEqual(f(["-a-b-c-x", "-a-b-c-y", "-a-b-c-z", "-q"]), {"-a-b-c-x": "x", "-a-b-c-y": "y", "-a-b-c-z": "z", "-q": "q"})
        self.assertEqual(f(["one", "two"]), {"one": "one", "two": "two"})
        # Two values that would read alike keep their full names.
        self.assertEqual(f(["a-x", "b-x", "c-y"])["a-x"], "a-x")

    def test_missing_folder(self):
        self.config({"g": {"dirs": ["nope", "n"]}})
        self.write("n/a.md", "# A\nibis")
        cfg, idx, report = self.index()
        self.assertTrue(any("nope" in w for w in report.warnings))
        self.assertEqual(refs(self.search("ibis")), ["g:a.md"])

    def test_default_groups(self):
        self.config({"work": {"dirs": ["w"]}, "home": {"dirs": ["h"]}}, default_groups=["work"])
        self.write("w/a.md", "# A\nheron")
        self.write("h/b.md", "# B\nheron")
        self.assertEqual(refs(self.search("heron")), ["work:a.md"])
        self.assertEqual(refs(self.search("heron", groups=["home"])), ["home:b.md"])

    def test_unknown_group_is_an_error(self):
        self.config()
        with self.assertRaises(cfglib.ConfigError):
            self.search("x", groups=["nope"])


class Includes(Tree):
    def test_include_with_relative_dirs(self):
        self.write("team/groups.json", json.dumps({"groups": {"team": {"dirs": ["kb"]}}}))
        self.write("team/kb/t.md", "# T\nlemur")
        self.config({"mine": {"dirs": ["n"]}}, include=["team/groups.json"])
        self.write("n/m.md", "# M\nlemur")
        self.assertEqual(refs(self.search("lemur", groups=["mine", "team"])), ["mine:m.md", "team:t.md"])

    def test_duplicate_group_and_include_loops(self):
        self.write("inc.json", json.dumps({"groups": {"mine": {"dirs": ["x"]}}}))
        self.config({"mine": {"dirs": ["n"]}}, include=["inc.json", "ns.json"])
        with self.assertRaises(cfglib.ConfigError) as cm:
            self.load()
        text = " ".join(cm.exception.problems)
        self.assertIn("defined twice", text)
        self.assertIn("includes itself", text)


class Roles(Tree):
    def test_roles_in_one_group_not_another(self):
        self.config({
            "kb": {"dirs": ["kb"], "fields": {"entities": ["service", "services"], "title": "name"}},
            "raw": {"dirs": ["raw"]},
        })
        note = "---\nname: Gateway notes\nservices: [pay-gw]\n---\nbody mentions pay-gw once\n"
        self.write("kb/a.md", note)
        self.write("raw/a.md", note)
        cfg, idx, _ = self.index()
        conn = idx.open()
        rows = dict(conn.execute("SELECT m.grp, f.title FROM files f JOIN members m ON m.file_id=f.id"))
        self.assertEqual(rows, {"kb": "Gateway notes", "raw": "a"})
        ents = dict(conn.execute("SELECT m.grp, f.entities FROM files f JOIN members m ON m.file_id=f.id"))
        self.assertEqual(json.loads(ents["kb"]), ["pay-gw"])
        self.assertEqual(json.loads(ents["raw"]), [])

    def test_status_role_marks_stale(self):
        self.config({"g": {"dirs": ["n"], "fields": {"status": {"field": "state", "stale": ["Obsolete"]}}}})
        self.write("n/old.md", "---\nstate: obsolete\n---\n# Old\ntapir")
        self.write("n/new.md", "---\nstate: current\n---\n# New\ntapir")
        res = self.search("tapir")
        self.assertEqual([(n.ref, n.stale) for n in res.notes], [("g:new.md", False), ("g:old.md", True)])


class Invalid(Tree):
    def problems(self, raw):
        self.write("bad.json", raw if isinstance(raw, str) else json.dumps(raw))
        with self.assertRaises(cfglib.ConfigError) as cm:
            cfglib.load(self.path("bad.json"))
        return " | ".join(cm.exception.problems)

    def test_messages(self):
        self.assertIn("line 1", self.problems("{bad json"))
        self.assertIn("does not exist", self.problems_missing())
        self.assertIn("no groups", self.problems({}))
        self.assertIn("unknown key 'grups'", self.problems({"grups": {}}))
        self.assertIn("group id", self.problems({"groups": {"bad id!": {"dirs": ["x"]}}}))
        self.assertIn("dirs is empty", self.problems({"groups": {"g": {}}}))
        self.assertIn("not a role", self.problems({"groups": {"g": {"dirs": ["x"], "fields": {"owner": "o"}}}}))
        self.assertIn("positive number", self.problems({"groups": {"g": {"dirs": ["x"]}}, "limits": {"answer_tokens": 0}}))
        self.assertIn("not a group", self.problems({"groups": {"g": {"dirs": ["x"]}}, "default_groups": ["h"]}))
        self.assertIn("engine", self.problems({"groups": {"g": {"dirs": ["x"], "engine": "qmd"}}}))

    def problems_missing(self):
        with self.assertRaises(cfglib.ConfigError) as cm:
            cfglib.load(self.path("nothing.json"))
        return " | ".join(cm.exception.problems)

    def test_cli_reports_config_errors(self):
        self.write("ns.json", "{}")
        self.cfg_path = self.path("ns.json")
        code, out, err = self.ns("search", "x")
        self.assertEqual(code, 2)
        self.assertIn("no groups", err)
        code, data = self.ns_json("search", "x")
        self.assertEqual((code, data["ok"]), (2, False))
