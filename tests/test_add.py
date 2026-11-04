"""Suite 9: adding notes (phase 3).

ns writes only new files, only in the one folder a group names, and changes
nothing it did not create. Each refusal is checked to leave every file as it
was.
"""
import contextlib
import hashlib
import io
import os
import sys
import unittest
from unittest import mock

from tests.helpers import Tree
from notesearch import add as addlib
from notesearch import cli

BODY = "## Observed\n\nThe retry job ran 5 times, then stopped.\n\n## Concluded\n\nThe cap is 5 attempts.\n"


class Add(Tree):
    def setUp(self):
        super().setUp()
        self.write("kb/services/orders.md", "---\nname: Orders service\nservice: orders-api\n---\n\n# Orders\n\nTakes orders.\n")
        self.write("kb/runbooks/retries.md", "---\nname: Renewal retries\nservice: [renewals, billing]\n---\n\nRetries happen.\n")
        self.config({"kb": {"dirs": ["kb"], "write": "kb/inbox",
                            "fields": {"title": "name", "entities": "service", "date": "updated"}},
                     "ro": {"dirs": ["ro"]}})
        self.write("ro/x.md", "# X\n")
        self.today = mock.patch.object(addlib, "today", return_value="2026-11-04")
        self.today.start()

    def tearDown(self):
        self.today.stop()
        super().tearDown()

    def add(self, *argv, body=BODY, json_out=False):
        """Run `ns add` with the text on stdin; (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        argv = ["add"] + list(argv) + ["--config", self.cfg_path] + (["--json"] if json_out else [])
        with mock.patch.object(sys, "stdin", io.StringIO(body)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def snapshot(self):
        files = {}
        for root, _, names in os.walk(self.tmp):
            if os.path.join(self.tmp, "cache") in root:
                continue
            for n in names:
                p = os.path.join(root, n)
                with open(p, "rb") as fh:
                    files[os.path.relpath(p, self.tmp)] = hashlib.sha1(fh.read()).hexdigest()
        return files

    def test_adds_a_note_in_the_groups_own_field_names(self):
        code, out, err = self.add("--group", "kb", "--title", "Payment retry cap",
                                  "--entities", "payments-api,retry_queue", "--source", "INC-2026-002")
        self.assertEqual(code, 0, err)
        self.assertIn("added kb:inbox/2026-11-04-payment-retry-cap.md", out)
        with open(self.path("kb/inbox/2026-11-04-payment-retry-cap.md")) as fh:
            text = fh.read()
        self.assertTrue(text.startswith('---\nname: "Payment retry cap"\nservice: ["payments-api", "retry_queue"]\n'
                                        'source: "INC-2026-002"\nupdated: 2026-11-04\nns_created: 2026-11-04\n---\n'), text)
        self.assertIn("---\n\n## Observed\n", text)
        self.assertNotIn("# Payment retry cap", text)
        # Indexed at once, and its entities are entities.
        res = self.search("retry_queue")
        self.assertEqual(res.notes[0].ref, "kb:inbox/2026-11-04-payment-retry-cap.md")
        code, data = self.ns_json("fields", "kb")
        self.assertIn("ns_created", [f["field"] for f in data["fields"]])

    def test_a_group_without_a_write_folder_is_read_only(self):
        before = self.snapshot()
        code, out, err = self.add("--group", "ro", "--title", "Anything")
        self.assertEqual(code, 2)
        self.assertIn("does not allow adding notes", err)
        self.assertEqual(self.snapshot(), before)

    def test_write_folder_outside_the_group_is_a_config_error(self):
        self.config({"kb": {"dirs": ["kb"], "write": "elsewhere"}})
        code, out, err = self.add("--group", "kb", "--title", "X")
        self.assertEqual(code, 2)
        self.assertIn("not inside any of its dirs", err)

    def test_a_write_folder_that_links_out_of_the_group_is_refused(self):
        os.makedirs(self.path("outside"))
        os.symlink(self.path("outside"), self.path("kb/inbox"))
        code, out, err = self.add("--group", "kb", "--title", "Linked out")
        self.assertEqual(code, 2)
        self.assertIn("not inside any of its folders", err)
        self.assertEqual(os.listdir(self.path("outside")), [])

    def test_a_hidden_write_folder_is_refused(self):
        self.config({"kb": {"dirs": ["kb"], "write": "kb/.inbox"}})
        code, out, err = self.add("--group", "kb", "--title", "Hidden")
        self.assertEqual(code, 2)
        self.assertIn("hidden", err)

    def test_doctor_and_config_show_the_write_folder(self):
        code, data = self.ns_json("doctor")
        self.assertIn("group kb: ns add writes to " + self.path("kb/inbox"), [c["check"] for c in data["checks"]])
        code, text, _ = self.ns("config", "check")
        self.assertIn("ns add writes to " + self.path("kb/inbox"), text)
        self.assertIn("read-only (no write folder)", text)
        self.config({"kb": {"dirs": ["kb"], "write": "kb/.inbox"}})
        code, data = self.ns_json("doctor")
        self.assertEqual(code, 1)
        self.assertTrue(any(c["status"] == "FAIL" and "hidden" in c["check"] for c in data["checks"]), data)

    def test_observed_and_concluded_are_kept_apart(self):
        before = self.snapshot()
        code, out, err = self.add("--group", "kb", "--title", "Blended", body="It failed, so the cap is 5.\n")
        self.assertEqual(code, 2)
        self.assertIn("## Observed", err)
        self.assertEqual(self.snapshot(), before)
        code, _, _ = self.add("--group", "kb", "--title", "Only a conclusion", body="# Concluded\n\nThe cap is 5.\n")
        self.assertEqual(code, 0)

    def test_text_that_looks_like_a_credential_is_refused(self):
        body = BODY + "\napi_key = sk_live_abcdefghijklmnopqrstuvwxyz\n"
        before = self.snapshot()
        code, out, err = self.add("--group", "kb", "--title", "Keys", body=body)
        self.assertEqual(code, 2)
        self.assertIn("looks like a credential", err)
        self.assertNotIn("sk_live", err)
        self.assertEqual(self.snapshot(), before)

    def test_same_title_stops_the_write(self):
        before = self.snapshot()
        code, out, err = self.add("--group", "kb", "--title", "orders SERVICE", json_out=True)
        self.assertEqual(code, 3)
        self.assertIn('"duplicates": [{"ref": "kb:services/orders.md"', out)
        self.assertIn('"written": false', out)
        self.assertEqual(self.snapshot(), before)
        code, out, err = self.add("--group", "kb", "--title", "Orders service")
        self.assertEqual(code, 3)
        self.assertIn("kb:services/orders.md", err)
        self.assertIn("--new", err)
        code, out, err = self.add("--group", "kb", "--title", "Orders service", "--new")
        self.assertEqual(code, 0, err)

    def test_shared_entity_and_half_the_title_is_a_close_match(self):
        code, out, err = self.add("--group", "kb", "--title", "Renewal retries runbook", "--entities", "billing")
        self.assertEqual(code, 3, err)
        self.assertIn("shares billing", err)
        # Same entity, a different subject: not a match.
        code, out, err = self.add("--group", "kb", "--title", "Invoice numbering", "--entities", "billing")
        self.assertEqual(code, 0, err)

    def test_supersedes_links_the_old_note_and_leaves_it_alone(self):
        old = self.path("kb/runbooks/retries.md")
        with open(old, "rb") as fh:
            old_bytes = fh.read()
        code, out, err = self.add("--group", "kb", "--title", "Renewal retries",
                                  "--supersedes", "kb:runbooks/retries.md#Retries")
        self.assertEqual(code, 0, err)
        self.assertIn("supersedes kb:runbooks/retries.md", out)
        with open(old, "rb") as fh:
            self.assertEqual(fh.read(), old_bytes)
        res = self.search("retries happen")
        hit = [n for n in res.notes if n.ref == "kb:runbooks/retries.md"][0]
        self.assertEqual(hit.superseded_by, ["kb:inbox/2026-11-04-renewal-retries.md"])
        code, text, _ = self.ns("search", "retries happen")
        self.assertIn("superseded by kb:inbox/2026-11-04-renewal-retries.md", text)
        code, data = self.ns_json("related", "kb:inbox/2026-11-04-renewal-retries.md")
        self.assertIn("kb:runbooks/retries.md", data["linked"])

    def test_supersedes_needs_a_note_that_exists(self):
        code, out, err = self.add("--group", "kb", "--title", "X", "--supersedes", "kb:nope.md")
        self.assertEqual(code, 2)
        self.assertIn("no such note", err)

    def test_append_extends_only_notes_ns_created(self):
        self.add("--group", "kb", "--title", "Payment retry cap")
        ref = "kb:inbox/2026-11-04-payment-retry-cap.md"
        code, out, err = self.add("--append", ref, body="# Observed\n\nAlso on staging.\n")
        self.assertEqual(code, 0, err)
        self.assertIn("appended to " + ref, out)
        with open(self.path("kb/inbox/2026-11-04-payment-retry-cap.md")) as fh:
            text = fh.read()
        self.assertTrue(text.endswith("\n## Added 2026-11-04\n\n### Observed\n\nAlso on staging.\n"), text)
        self.assertEqual(text.count("ns_created"), 1)

        before = self.snapshot()
        code, out, err = self.add("--append", "kb:services/orders.md")
        self.assertEqual(code, 2)
        self.assertIn("never changes a note it did not create", err)
        # A hand-written note in the write folder has no ns_created: left alone too.
        self.write("kb/inbox/by-hand.md", "# By hand\n\nWritten by a person.\n")
        before = self.snapshot()
        code, out, err = self.add("--append", "kb:inbox/by-hand.md")
        self.assertEqual(code, 2)
        self.assertIn("no ns_created: field", err)
        self.assertEqual(self.snapshot(), before)

    def test_append_takes_no_other_options(self):
        code, out, err = self.add("--append", "kb:x.md", "--title", "T")
        self.assertEqual(code, 2)
        self.assertIn("--append takes only", err)

    def test_same_name_on_the_same_day_gets_a_number(self):
        self.add("--group", "kb", "--title", "Cache warmup")
        code, out, err = self.add("--group", "kb", "--title", "Cache warmup", "--new")
        self.assertEqual(code, 0, err)
        self.assertIn("2026-11-04-cache-warmup-2.md", out)

    def test_headings_move_under_the_title_and_code_blocks_stay(self):
        body = "# Observed\n\n```sh\n# not a heading\n```\n\n# Concluded\n\nDone.\n"
        self.add("--group", "kb", "--title", "Shifted", body=body)
        with open(self.path("kb/inbox/2026-11-04-shifted.md")) as fh:
            text = fh.read()
        self.assertIn("\n## Observed\n", text)
        self.assertIn("\n# not a heading\n", text)
        self.assertIn("\n## Concluded\n", text)

    def test_no_existing_note_changes_whatever_is_added(self):
        before = {k: v for k, v in self.snapshot().items() if not k.startswith("kb/inbox")}
        self.add("--group", "kb", "--title", "One")
        self.add("--group", "kb", "--title", "Two", "--supersedes", "kb:services/orders.md")
        self.add("--group", "kb", "--title", "Orders service")  # refused: same title
        self.add("--append", "kb:services/orders.md")  # refused: not created by ns
        after = {k: v for k, v in self.snapshot().items() if not k.startswith("kb/inbox")}
        self.assertEqual(after, before)
        self.assertEqual(sorted(os.listdir(self.path("kb/inbox"))),
                         ["2026-11-04-one.md", "2026-11-04-two.md"])


class Helpers(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(addlib.slug("Payment retry: cap (5x)!"), "payment-retry-cap-5x")
        self.assertEqual(addlib.slug("!!!"), "note")
        self.assertLessEqual(len(addlib.slug("word " * 40)), 60)

    def test_quoted_values_read_back(self):
        from notesearch import frontmatter
        line = addlib._field("name", 'A "quoted" back\\slash')
        data, _ = frontmatter.parse_yaml([line])
        self.assertEqual(data["name"], 'A "quoted" back\\slash')
        line = addlib._field("service", ["a, b", 'c"d'])
        data, _ = frontmatter.parse_yaml([line])
        self.assertEqual(data["service"], ["a, b", 'c"d'])


if __name__ == "__main__":
    unittest.main()
