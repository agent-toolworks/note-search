"""Suite 1: reading files and their frontmatter."""
import os

from tests.helpers import Tree
from notesearch import frontmatter as fm
from notesearch.split import read_note
from notesearch.index import Index


class Frontmatter(Tree):
    def parse(self, text):
        return fm.parse_yaml(text.splitlines())

    def test_scalars_quotes_and_comments(self):
        d, w = self.parse('a: 1\nb: "two: three"\nc: \'it\'\'s\'\nd: plain # comment\ne: "# not a comment"')
        self.assertEqual(d, {"a": "1", "b": "two: three", "c": "it's", "d": "plain", "e": "# not a comment"})
        self.assertEqual(w, [])

    def test_lists_in_every_form(self):
        d, _ = self.parse("a: [x, 'y, z', \"w\"]\nb:\n  - one\n  - two\nc:\n- three\n- four\nd: []")
        self.assertEqual(d["a"], ["x", "y, z", "w"])
        self.assertEqual(d["b"], ["one", "two"])
        self.assertEqual(d["c"], ["three", "four"])
        self.assertEqual(d["d"], [])

    def test_nested_keys_are_flattened(self):
        d, _ = self.parse("metadata:\n  type: project\n  deep:\n    x: 1\nafter: yes")
        self.assertEqual(d, {"metadata.type": "project", "metadata.deep.x": "1", "after": "yes"})

    def test_list_of_maps_folds_into_items(self):
        d, _ = self.parse("links:\n  - title: A\n    url: http://a\n  - title: B")
        self.assertEqual(d["links"], ["title: A; url: http://a", "title: B"])

    def test_block_scalars(self):
        d, _ = self.parse("a: |\n  line one\n  line two\nb: >\n  folded\n  text\nc: 3")
        self.assertEqual(d, {"a": "line one\nline two", "b": "folded text", "c": "3"})

    def test_duplicate_keys_keep_the_last_and_warn(self):
        d, w = self.parse("a: 1\na: 2")
        self.assertEqual(d, {"a": "2"})
        self.assertEqual(len(w), 1)

    def test_empty_and_unknown_fields(self):
        d, _ = self.parse("")
        self.assertEqual(d, {})
        d, _ = self.parse("x-custom_field.v2: value\nempty:")
        self.assertEqual(d, {"x-custom_field.v2": "value", "empty": ""})

    def test_urls_and_times_stay_whole(self):
        d, _ = self.parse("url: https://example.com:8080/a\nat: 12:30")
        self.assertEqual(d, {"url": "https://example.com:8080/a", "at": "12:30"})

    def test_broken_raises(self):
        for bad in ("no colon here", "- item with no key", "a: 1\n  - stray"):
            with self.assertRaises(fm.FrontmatterError, msg=bad):
                self.parse(bad)

    def test_toml(self):
        d, _ = fm.parse_toml(['title = "T"', "tags = [\"a\", \"b\"]", "[extra]", "x = 1"])
        self.assertEqual(d, {"title": "T", "tags": ["a", "b"], "extra.x": "1"})

    def test_values_match_keys_case_insensitively(self):
        self.assertEqual(fm.values({"Service": ["a", "b"], "x": "c"}, ["service", "x"]), ["a", "b", "c"])


class Files(Tree):
    def read(self, text, mode="w", **kw):
        p = self.write("n.md", text, mode)
        return read_note(p, kw.pop("max_bytes", 10 ** 8), **kw)

    def test_no_frontmatter(self):
        n = self.read("# T\nbody")
        self.assertEqual(n.fm, {})
        self.assertEqual(n.title_h1, "T")

    def test_broken_frontmatter_is_kept_as_text(self):
        n = self.read("---\nbroken line\n---\n# T\nbody")
        self.assertTrue(n.fm_error)
        text = "\n".join(p.text for p in n.pieces)
        self.assertIn("broken line", text)
        self.assertIn("body", text)

    def test_unclosed_frontmatter_is_body(self):
        n = self.read("---\na: 1\n# Heading\ntext")
        self.assertEqual(n.fm, {})
        self.assertIn("a: 1", "\n".join(p.text for p in n.pieces))

    def test_toml_frontmatter(self):
        n = self.read('+++\ntitle = "T"\n+++\nbody')
        self.assertEqual(n.fm, {"title": "T"})

    def test_line_endings_bom_and_bad_bytes(self):
        n = self.read(b"\xef\xbb\xbf---\r\ntitle: T\r\n---\r\n# H\r\nbad \xff byte\r\n", mode="wb")
        self.assertEqual(n.fm, {"title": "T"})
        self.assertEqual(n.pieces[0].path, ("H",))
        self.assertIn("bad � byte", n.pieces[0].text)
        self.assertNotIn("\r", n.pieces[0].text)

    def test_hebrew_and_emoji(self):
        n = self.read("# כותרת\nשלום עולם 🌱\n")
        self.assertEqual(n.pieces[0].path, ("כותרת",))
        self.assertIn("🌱", n.pieces[0].text)

    def test_empty_file(self):
        n = self.read("")
        self.assertEqual(n.pieces, [])

    def test_very_long_line_is_cut_into_pieces(self):
        n = self.read("# H\n" + "x" * 9000 + "\n", max_chars=2000)
        self.assertGreaterEqual(len(n.pieces), 4)
        self.assertTrue(all(len(p.text) <= 2100 for p in n.pieces))
        self.assertTrue(all(p.line_start <= 2 <= p.line_end or p.line_start == 1 for p in n.pieces))


class Skipped(Tree):
    """Binary, unreadable, hidden and linked files during a scan."""

    def test_binary_hidden_and_other_extensions(self):
        self.config()
        self.write("notes/a.md", "# A\nalpha")
        self.write("notes/b.md", "PNG\x00\x01binary", mode="w")
        self.write("notes/.hidden.md", "# H\nhidden")
        self.write("notes/.git/x.md", "# G\ngit")
        self.write("notes/c.json", "{}")
        cfg, idx, report = self.index()
        conn = idx.open()
        rels = sorted(r[0] for r in conn.execute("SELECT rel FROM members"))
        self.assertEqual(rels, ["a.md"])
        self.assertIn(("notes", "b.md", "binary file"), report.skipped)
        # The binary file is remembered and not read again until it changes.
        report2 = Index(cfg).update()
        self.assertIn(("notes", "b.md", "binary file"), report2.skipped)

    def test_unreadable_file(self):
        if os.geteuid() == 0:
            self.skipTest("root can read everything")
        self.config()
        p = self.write("notes/secret.md", "# S\nx")
        os.chmod(p, 0)
        try:
            cfg, idx, report = self.index()
            self.assertTrue(any(s[1] == "secret.md" and "cannot read" in s[2] for s in report.skipped))
        finally:
            os.chmod(p, 0o600)

    def test_symlink_loops_and_links_outside(self):
        self.config()
        self.write("notes/a.md", "# A\nalpha")
        self.write("outside/o.md", "# O\noutside")
        os.symlink(self.path("notes"), self.path("notes/loop"))
        os.symlink(self.path("outside"), self.path("notes/out"))
        os.symlink(self.path("notes/a.md"), self.path("notes/again.md"))
        cfg, idx, report = self.index()
        rels = sorted(r[0] for r in idx.open().execute("SELECT rel FROM members"))
        self.assertEqual(rels, ["a.md"])  # the loop is walked once, the outside link skipped
        self.assertTrue(any("outside" in s[2] for s in report.skipped))
