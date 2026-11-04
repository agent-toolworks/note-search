"""Search behaviour, `ns get`, and the command line."""
import json
import os

from tests.helpers import Tree
from notesearch.refs import tokens


class Search(Tree):
    def setUp(self):
        super().setUp()
        self.config({"kb": {"dirs": ["kb"], "fields": {"title": "name", "entities": "service",
                                                       "status": {"field": "status", "stale": ["obsolete"]}}},
                     "mem": {"dirs": ["mem"]}})
        self.write("kb/payment-gateway.md",
                   "---\nname: Payment gateway\nservice: payment-gateway\nowner: payments-team\n---\n"
                   "# Payment gateway\nTakes card payments.\n\n## Pool\nThe connection pool ran out under retry storms.\n")
        self.write("kb/orders.md", "# Orders API\n`OrderService` writes `orders_v2` and retries payment calls.\n")
        self.write("kb/old.md", "---\nstatus: obsolete\n---\n# Old orders\nOrderService wrote the orders table.\n")
        self.write("mem/handoff.md", "# Handoff\nThe staging database was refreshed on 2026-10-26.\n")

    def test_strict_match_and_snippet(self):
        res = self.search("connection pool")
        self.assertEqual(res.notes[0].ref, "kb:payment-gateway.md")
        self.assertEqual(res.notes[0].heading, "Pool")
        self.assertEqual(res.notes[0].stage, "strict")
        self.assertIn("pool", res.notes[0].snippet.lower())

    def test_stopwords_do_not_block_a_question(self):
        res = self.search("what writes to orders_v2?")
        self.assertEqual(res.notes[0].ref, "kb:orders.md")

    def test_camel_case_parts(self):
        self.assertEqual(self.search("order service").notes[0].ref, "kb:orders.md")
        self.assertEqual(self.search("OrderService").notes[0].ref, "kb:orders.md")

    def test_misspelled_name_is_corrected(self):
        res = self.search("payment-gatway")
        self.assertEqual(res.notes[0].ref, "kb:payment-gateway.md")
        self.assertIn(res.notes[0].stage, ("names", "strict"))

    def test_relaxed_is_labelled(self):
        res = self.search("staging database zeppelin")
        self.assertEqual(res.notes[0].ref, "mem:handoff.md")
        self.assertEqual(res.notes[0].stage, "relaxed")
        self.assertFalse(res.strict_found)
        self.assertEqual(res.notes[0].coverage, (2, 3))

    def test_nothing_matches(self):
        res = self.search("kubernetes helm chart")
        self.assertEqual(res.notes, [])
        self.assertFalse(res.strict_found)

    def test_several_wordings_merge(self):
        res = self.search("zeppelin", "card payments")
        self.assertEqual(res.notes[0].ref, "kb:payment-gateway.md")
        self.assertEqual(len(res.wordings), 2)

    def test_stale_ranks_lower_and_is_labelled(self):
        res = self.search("OrderService")
        self.assertEqual([n.ref for n in res.notes], ["kb:orders.md", "kb:old.md"])
        self.assertTrue(res.notes[1].stale)

    def test_frontmatter_values_are_searchable_and_filterable(self):
        self.assertEqual(self.search("payments-team").notes[0].ref, "kb:payment-gateway.md")
        res = self.search("payment", filters={"owner": "payments-team"})
        self.assertEqual([n.ref for n in res.notes], ["kb:payment-gateway.md"])
        res = self.search("payment", filters={"owner": "nobody"})
        self.assertEqual(res.notes, [])

    def test_since(self):
        p = self.path("kb/orders.md")
        os.utime(p, (946684800, 946684800))  # 2000-01-01
        res = self.search("OrderService", since="2020-01-01")
        self.assertEqual([n.ref for n in res.notes], ["kb:old.md"])

    def test_wordings_limit_and_empty(self):
        res = self.search("a1", "b2", "c3", "d4", "e5", "f6")
        self.assertTrue(any("first 5" in w for w in res.warnings))
        res = self.search("???")
        self.assertTrue(any("no words" in w for w in res.warnings))

    def test_fts_syntax_in_queries_is_harmless(self):
        for q in ['"unbalanced', "NEAR(a b)", "a AND OR", "col:value", "*", "(", "-x", "^start"]:
            self.search(q)  # must not raise


class Budget(Tree):
    def test_answers_stay_within_budget_whatever_the_input(self):
        self.config(limits={"answer_tokens": 300})
        for i in range(60):
            self.write("notes/n%02d.md" % i, "# Very long title number %d %s\n%s\n" % (
                i, "word " * 40, ("ocelot " + "x" * 300 + " ") * 20))
        code, out, _ = self.ns("search", "ocelot")
        self.assertEqual(code, 0)
        self.assertLessEqual(tokens(out), 300)
        self.assertIn("more: repeat with --offset", out)
        offset = int(out.rsplit("--offset", 1)[1].split()[0])
        code, out2, _ = self.ns("search", "ocelot", "--offset", str(offset))
        self.assertTrue(any(line.startswith("%d notes:" % (offset + 1)) for line in out2.split("\n")), out2)
        self.assertLessEqual(tokens(out2), 300)

    def test_the_more_line_and_warnings_fit_in_the_budget(self):
        # The "N more" line and the warnings come after the notes; an answer
        # that fills the budget with notes must still leave room for them.
        # Every budget from 120 to 600 tokens, so no length can slip through.
        from notesearch import render
        self.config()
        for i in range(40):
            self.write("notes/n%02d.md" % i, "# Heron note %d\nheron %s\n" % (i, "z" * (37 * i % 200)))
        res = self.search("heron")
        res.warnings = ["folder /somewhere/that/is/missing does not exist", "3 files skipped; see ns doctor"]
        for budget in range(120, 601):
            text, shown = render.search_text(res, budget, 0, lambda n: [], q="3f9a1c2e")
            self.assertLessEqual(tokens(text), budget, "budget %d" % budget)
            self.assertGreaterEqual(shown, 1)

    def test_one_note_always_shows(self):
        self.config(limits={"answer_tokens": 10})
        self.write("notes/a.md", "# A\nquetzal " + "y" * 2000)
        code, out, _ = self.ns("search", "quetzal")
        self.assertIn("notes:a.md", out)


class Get(Tree):
    def setUp(self):
        super().setUp()
        self.config(limits={"get_tokens": 100})
        body = "\n".join("line %d of the incident text" % i for i in range(1, 200))
        self.write("notes/inc.md", "# Incident\nintro\n## Timeline\n%s\n## Cause\nThe pool ran out.\n### Detail\nmore detail\n## Fix\nbackoff\n" % body)
        self.write("notes/small.md", "# Small\nshort note\n")

    def test_heading_includes_subsections(self):
        code, out, _ = self.ns("get", "notes:inc.md#Cause")
        self.assertEqual(code, 0)
        self.assertIn("The pool ran out.", out)
        self.assertIn("more detail", out)
        self.assertNotIn("backoff", out)

    def test_heading_path(self):
        code, out, _ = self.ns("get", "notes:inc.md#Incident › Cause › Detail")
        self.assertIn("more detail", out)
        self.assertNotIn("pool ran out", out)

    def test_long_section_is_cut_with_a_continuation(self):
        code, out, _ = self.ns("get", "notes:inc.md#Timeline")
        self.assertLessEqual(tokens(out), 140)
        self.assertIn("[cut at line", out)
        cont = out.rsplit("ns get ", 1)[1].rstrip("]\n")
        code, out2, _ = self.ns("get", cont)
        self.assertEqual(code, 0)
        first_line = out2.split("\n")[1]
        self.assertTrue(first_line.startswith("line "))

    def test_range_and_whole_note(self):
        code, out, _ = self.ns("get", "notes:inc.md:L5-7")
        self.assertEqual(out.split("\n")[1:4], ["line 2 of the incident text", "line 3 of the incident text", "line 4 of the incident text"])
        code, out, _ = self.ns("get", "notes:small.md")
        self.assertIn("short note", out)
        self.assertNotIn("outline", out)
        code, out, _ = self.ns("get", "notes:inc.md")
        self.assertIn("outline", out)

    def test_errors(self):
        for ref, want in [("nocolon", "a reference is"), ("notes:missing.md", "no note"),
                          ("other:inc.md", "unknown group"), ("notes:inc.md#Nope", "no heading"),
                          ("notes:inc.md:L9-3", "not valid")]:
            code, out, err = self.ns("get", ref)
            self.assertEqual(code, 2, ref)
            self.assertIn(want, err, ref)

    def test_get_reads_the_current_file(self):
        self.write("notes/small.md", "# Small\nchanged text\n")
        code, out, _ = self.ns("get", "notes:small.md")
        self.assertIn("changed text", out)


class Commands(Tree):
    def setUp(self):
        super().setUp()
        self.config({"kb": {"dirs": ["kb"], "fields": {"entities": "service", "links": "related"}}})
        self.write("kb/a.md", "---\nservice: pay\nrelated: [b]\nowner: team-a\n---\n# A\nalpha see [[c]]\n")
        self.write("kb/b.md", "---\nservice: pay\n---\n# B\nbeta\n")
        self.write("kb/c.md", "# C\ngamma [link](a.md)\n")
        self.write("kb/bad.md", "---\nnot valid\n---\n# Bad\npassword=hunter2hunter2\n")

    def test_json_search_envelope(self):
        code, data = self.ns_json("search", "alpha")
        self.assertTrue(data["ok"])
        self.assertEqual(data["notes"][0]["ref"], "kb:a.md")
        self.assertEqual(data["notes"][0]["lines"], [6, 7])
        self.assertEqual(sorted(data["notes"][0]["related"]), ["kb:b.md", "kb:c.md"])

    def test_related(self):
        code, data = self.ns_json("related", "kb:a.md")
        self.assertEqual(sorted(data["linked"]), ["kb:b.md", "kb:c.md"])
        self.assertEqual(data["shared_entities"], [{"ref": "kb:b.md", "entities": ["pay"]}])

    def test_groups_and_fields(self):
        code, data = self.ns_json("groups")
        self.assertEqual(data["groups"][0]["notes"], 4)
        code, data = self.ns_json("fields", "kb")
        fields = {f["field"]: f["notes"] for f in data["fields"]}
        self.assertEqual(fields, {"service": 2, "related": 1, "owner": 1})

    def test_doctor(self):
        code, out, _ = self.ns("doctor", "--secrets")
        self.assertEqual(code, 0, out)
        self.assertIn("frontmatter that could not be read", out)
        self.assertIn("kb:bad.md", out)
        self.assertIn("look like credentials", out)
        self.assertNotIn("hunter2", out)

    def test_doctor_fails_on_a_missing_config(self):
        self.cfg_path = self.path("none.json")
        code, out, _ = self.ns("doctor")
        self.assertEqual(code, 1)
        self.assertIn("create it", out)

    def test_index_and_rebuild(self):
        code, out, _ = self.ns("index")
        self.assertIn("added 4", out)
        code, out, _ = self.ns("index", "--rebuild")
        self.assertIn("added 4", out)
        code, out, _ = self.ns("index")
        self.assertIn("unchanged 4", out)

    def test_no_update_without_an_index(self):
        code, out, err = self.ns("search", "alpha", "--no-update")
        self.assertEqual(code, 1)
        self.assertIn("no index yet", err)


class Arguments(Tree):
    def setUp(self):
        super().setUp()
        self.config({"kb": {"dirs": ["kb"]}, "mem": {"dirs": ["mem"]}})
        self.write("kb/pool.md", "---\nservice: payments\n---\n# Pool\nThe connection pool ran out.\n")
        self.write("mem/pool.md", "# Pool\nThe connection pool in memory.\n")

    def test_words_after_an_option(self):
        code, data = self.ns_json("search", "connection", "--group", "kb", "pool", "service=payments")
        self.assertEqual(code, 0)
        self.assertEqual(data["query"]["wordings"], ["connection", "pool"])
        self.assertEqual(data["query"]["filters"], {"service": "payments"})
        self.assertEqual([n["ref"] for n in data["notes"]], ["kb:pool.md"])

    def test_unknown_option_is_still_an_error(self):
        with self.assertRaises(SystemExit) as cm:
            self.ns("search", "pool", "--colour", "red")
        self.assertEqual(cm.exception.code, 2)
        with self.assertRaises(SystemExit):
            self.ns("get", "kb:pool.md", "extra")
