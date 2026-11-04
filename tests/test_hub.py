"""Suite 8: the toolworks-hub source and the plugin's files."""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

from tests.helpers import ROOT
from tests.test_log import SID, Env

RUN = os.path.join(ROOT, "hub-source", "run")
KINDS = {"stat", "timeseries", "table", "list", "search"}
TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})$")


def _hub():
    for d in (os.environ.get("TOOLWORKS_HUB_REPO"), os.path.expanduser("~/git/home/toolworks-hub")):
        if d and os.path.exists(os.path.join(d, "scripts", "hub")):
            return os.path.join(d, "scripts", "hub")
    return None


HUB = _hub()  # found before a test moves HOME


def check_shape(test, kind, data):
    """The contract-4 shapes, as far as this source uses them."""
    test.assertIn(kind, KINDS)
    if kind == "stat":
        test.assertIsInstance(data["value"], (int, float, str))
        test.assertLessEqual(set(data), {"value", "unit", "label", "resets_at"})
    elif kind == "timeseries":
        for s in data["series"]:
            test.assertTrue(s["name"])
            for p in s["points"]:
                test.assertRegex(p["t"], TIME)
                test.assertTrue(p["v"] is None or isinstance(p["v"], (int, float)))
    elif kind == "table":
        ids = [c["id"] for c in data["columns"]]
        for c in ids:
            test.assertRegex(c, r"^[a-z0-9][a-z0-9-]{0,63}$")
        for row in data["rows"]:
            test.assertLessEqual(set(row), set(ids))
            for v in row.values():
                if isinstance(v, dict):
                    test.assertEqual(set(v) - {"label"}, {"ref", "id"})
                else:
                    test.assertTrue(v is None or isinstance(v, (str, int, float, bool)))
    elif kind == "search":
        for r in data["results"]:
            test.assertTrue(r["title"])
            test.assertLessEqual(set(r), {"title", "detail", "location", "evidence", "ref"})
            for v in r.values():
                test.assertTrue(v)


class Source(Env):
    def setUp(self):
        super().setUp()
        self.notes()
        os.environ["NS_CONFIG"] = self.cfg_path
        os.environ["CLAUDE_CODE_SESSION_ID"] = SID
        with open(os.path.join(ROOT, "hub-source", "source.json")) as fh:
            self.manifest = json.load(fh)

    def run_source(self, *argv, ok=True):
        p = subprocess.run([RUN] + list(argv), capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL, cwd=os.path.dirname(RUN))
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr)
            return json.loads(p.stdout)
        return p

    def test_manifest(self):
        m = self.manifest
        self.assertEqual(m["contract"], 4)
        self.assertEqual(m["name"], "note-search")
        self.assertFalse(m["network"])
        self.assertEqual(m["shows"], ["session"])
        with open(os.path.join(ROOT, "VERSION")) as fh:
            self.assertEqual(m["version"], fh.read().strip())
        self.assertLessEqual(set(m), {"contract", "name", "title", "version", "network", "widgets", "shows",
                                      "refresh_seconds", "timeout_seconds", "page"})

    def test_detect(self):
        self.assertEqual(self.run_source("detect")["state"], "working")
        os.environ["NS_CONFIG"] = self.path("missing.json")
        d = self.run_source("detect")
        self.assertEqual(d["state"], "not_set_up")
        self.assertIn("missing.json", d["reason"])
        self.assertTrue(d["fix"])
        self.write("bad.json", '{"groups": {"x": {"dirs": ["nowhere"], "colour": 1}}}')
        os.environ["NS_CONFIG"] = self.path("bad.json")
        d = self.run_source("detect")
        self.assertEqual((d["state"], d["fix"]), ("broken", "ns config check"))

    def test_every_widget_with_an_empty_log(self):
        for w in self.manifest["widgets"]:
            argv = ["fetch", w["id"]] + (["--query", w["example_query"]] if w["kind"] == "search" else [])
            d = self.run_source(*argv)
            self.assertEqual(d["kind"], w["kind"], w["id"])
            check_shape(self, d["kind"], d["data"])

    def test_widgets_after_searches(self):
        code, out, _ = self.ns("search", "connection pool")
        q = out.splitlines()[0].rsplit(" q ", 1)[1]
        self.ns("get", "kb:pool.md#Pool", "--q", q)
        self.ns("search", "kubernetes")
        self.assertEqual(self.run_source("fetch", "searches")["data"]["value"], 2)
        self.assertEqual(self.run_source("fetch", "open-rate")["data"]["value"], 100)
        self.assertEqual(self.run_source("fetch", "again-rate")["data"]["value"], 50)
        rows = self.run_source("fetch", "no-match")["data"]["rows"]
        self.assertEqual([r["words"] for r in rows], ["kubernetes"])
        self.assertEqual(rows[0]["session"], {"ref": "session", "id": SID})
        groups = self.run_source("fetch", "groups")["data"]["rows"]
        self.assertEqual((groups[0]["group"], groups[0]["notes"], groups[0]["searches"]), ("kb", 2, 2))
        per_day = self.run_source("fetch", "per-day")["data"]["series"]
        self.assertEqual([s["name"] for s in per_day], ["kb"])
        self.assertEqual(sum(p["v"] for p in per_day[0]["points"]), 2)
        self.assertEqual(len(per_day[0]["points"]), 90)
        for w in self.manifest["widgets"]:
            if w["kind"] != "search":
                d = self.run_source("fetch", w["id"])
                check_shape(self, d["kind"], d["data"])

    def test_search_widget_does_not_log(self):
        d = self.run_source("fetch", "search", "--query", "connection pool")
        self.assertEqual(d["data"]["results"][0]["location"], "kb:pool.md#Pool")
        self.assertEqual(d["data"]["results"][0]["evidence"], "strict")
        self.assertEqual(self.log(), [])
        self.assertEqual(self.run_source("fetch", "search")["data"]["results"], [])

    # ---- the view's calls (page/) --------------------------------------------
    def test_view_page_is_declared_and_shipped(self):
        self.assertEqual(self.manifest["page"], "page/index.html")
        page = os.path.join(ROOT, "hub-source", "page")
        for f in ("index.html", "view.js", "view.css", "md.js"):
            self.assertTrue(os.path.isfile(os.path.join(page, f)), f)
        with open(os.path.join(page, "index.html")) as fh:
            html = fh.read()
        # The hub's Content-Security-Policy is default-src 'self': nothing inline, nothing remote.
        self.assertNotRegex(html, r"<script>|<style>| style=|https?://")

    def test_call_search(self):
        d = self.run_source("call", "search", json.dumps({"q": "connection pool | zebrafinch"}))
        self.assertEqual(d["notes"][0]["ref"], "kb:pool.md")
        self.assertEqual(d["notes"][0]["section"], "kb:pool.md#Pool")
        self.assertEqual(d["searched"], ["kb"])
        self.assertEqual(d["groups"], ["kb"])
        self.assertEqual(self.log(), [], "a person searching on the page is not an agent's search")
        empty = self.run_source("call", "search", "{}")
        self.assertEqual((empty["notes"], empty["groups"]), ([], ["kb"]))

    def test_call_notes_and_note(self):
        d = self.run_source("call", "notes", "{}")
        self.assertEqual(sorted(n["ref"] for n in d["notes"]), ["kb:orders.md", "kb:pool.md"])
        pool = [n for n in d["notes"] if n["ref"] == "kb:pool.md"][0]
        self.assertEqual((pool["title"], pool["stem"], pool["sections"]), ("Payment gateway", "pool", 2))
        self.write("kb/fm.md", "---\ntitle: With header\ntags: [a, b]\n---\n# Body\ntext <script>x</script>\n")
        n = self.run_source("call", "note", json.dumps({"ref": "kb:fm.md#Body"}))
        self.assertTrue(n["ok"])
        self.assertEqual(n["heading"], "Body")
        self.assertEqual(n["frontmatter"], {"title": "With header", "tags": ["a", "b"]})
        self.assertEqual(n["body"], "# Body\ntext <script>x</script>\n")
        self.assertEqual(n["body_line"], 5)
        self.assertEqual(n["path"], os.path.realpath(self.path("kb/fm.md")))

    def test_call_note_reads_only_indexed_notes(self):
        self.write("secret.md", "not in any group\n")
        for ref in ("kb:../secret.md", "kb:nope.md", "other:pool.md"):
            d = self.run_source("call", "note", json.dumps({"ref": ref}))
            self.assertFalse(d["ok"], ref)
            self.assertNotIn("not in any group", json.dumps(d))

    def test_call_measures_and_bad_input(self):
        d = self.run_source("call", "measures", "{}")
        self.assertEqual([w["id"] for w in d["widgets"]],
                         [w["id"] for w in self.manifest["widgets"] if w["kind"] != "search"])
        for w in d["widgets"]:
            check_shape(self, w["kind"], w["data"])
        p = self.run_source("call", "search", "{not json", ok=False)
        self.assertEqual(p.returncode, 2)
        p = self.run_source("call", "nope", "{}", ok=False)
        self.assertEqual(p.returncode, 2)

    def test_markdown_renderer(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed; the reader's renderer is tested with it")
        p = subprocess.run([node, os.path.join(ROOT, "tests", "md_test.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)

    def test_show_session(self):
        self.assertEqual(self.run_source("show", "session", SID), {"widgets": []})
        self.ns("search", "connection pool")
        d = self.run_source("show", "session", SID)
        self.assertEqual([w["kind"] for w in d["widgets"]], ["stat", "table"])
        for w in d["widgets"]:
            check_shape(self, w["kind"], w["data"])
        self.assertEqual(d["widgets"][1]["data"]["rows"][0]["words"], "connection pool")

    def test_counts_log_is_labelled(self):
        self.notes(log="counts")
        self.ns("search", "kubernetes")
        d = self.run_source("fetch", "no-match")
        self.assertIn("counts only", d["notice"])
        self.assertEqual(d["data"]["rows"][0]["words"], "(1 words, not logged)")

    def test_unknown_widget_fails(self):
        p = self.run_source("fetch", "nope", ok=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("unknown widget", p.stderr)

    @unittest.skipUnless(HUB, "toolworks-hub not found (set TOOLWORKS_HUB_REPO)")
    def test_hub_check_source(self):
        self.ns("search", "connection pool")
        p = subprocess.run([HUB, "check-source", os.path.dirname(RUN), "--entity", "session=" + SID],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("0 failed", p.stdout)


class Plugin(unittest.TestCase):
    def load(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_versions_agree(self):
        v = self.load("VERSION").strip()
        self.assertEqual(json.loads(self.load(".claude-plugin", "plugin.json"))["version"], v)
        market = json.loads(self.load(".claude-plugin", "marketplace.json"))
        self.assertEqual([p["version"] for p in market["plugins"]], [v])
        self.assertEqual(json.loads(self.load("hub-source", "source.json"))["version"], v)
        self.assertIn("## %s" % v, self.load("CHANGELOG.md"))

    def test_skill(self):
        text = self.load("skills", "note-search", "SKILL.md")
        m = re.match(r"^---\nname: note-search\ndescription: (.+?)\n---\n", text, re.S)
        self.assertIsNotNone(m)
        self.assertLessEqual(len(m.group(1)) / 4, 120, "the description is loaded in every session; keep it short")
        for cmd in re.findall(r'"\$NS" (\w+)', text):
            self.assertIn(cmd, ("search", "get", "add", "doctor", "config", "fields"))
        self.assertIn('${CLAUDE_PLUGIN_ROOT:-$PWD}/bin/ns', text)
        self.assertTrue(os.access(os.path.join(ROOT, "bin", "ns"), os.X_OK))

    def test_skill_examples_run(self):
        """The search and get lines the skill shows are valid commands."""
        from notesearch import cli
        seen = []
        real = cli.cmd_search, cli.cmd_get
        cli.cmd_search = cli.cmd_get = lambda args: seen.append(args) or 0
        try:
            self.assertEqual(cli.main(["search", "checkout slow", "payment-gateway timeout", "PAYGW_POOL_SIZE"]), 0)
            self.assertEqual(cli.main(["get", "kb:services/payment-gateway.md#Timeouts", "--q", "3f9a1c2e"]), 0)
            self.assertEqual(cli.main(["search", "x", "--group", "kb,memory", "service=orders-api",
                                       "--since", "2026-06-01", "--offset", "6"]), 0)
        finally:
            cli.cmd_search, cli.cmd_get = real
        self.assertEqual(seen[2].words, ["x", "service=orders-api"])
        self.assertEqual((seen[2].group, seen[2].offset), ("kb,memory", 6))

    @unittest.skipUnless(shutil.which("claude"), "claude not installed")
    def test_claude_validates_the_plugin(self):
        p = subprocess.run(["claude", "plugin", "validate", ROOT], capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Validation passed", p.stdout)


if __name__ == "__main__":
    unittest.main()
