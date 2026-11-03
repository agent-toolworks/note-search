"""Suite 6: search quality on the committed question set gates every change."""
import json
import os
import unittest

from tests.helpers import ROOT
from notesearch import evaluate

QUESTIONS = os.path.join(ROOT, "fixtures", "eval", "questions.json")
BASELINE = os.path.join(ROOT, "fixtures", "eval-baseline.json")


class Quality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scores = evaluate.score(QUESTIONS)

    def test_gates(self):
        baseline = None
        if os.path.exists(BASELINE):
            with open(BASELINE) as fh:
                baseline = json.load(fh)
        failures = evaluate.gates(self.scores, baseline)
        misses = [(q["id"], q["kind"], q["rank"], q["top"][:3]) for q in self.scores["questions"] if not q["top5"]]
        self.assertEqual(failures, [], misses)

    def test_every_kind_is_covered(self):
        kinds = set(self.scores["kinds"])
        self.assertEqual(kinds, {"exact", "partial", "symptom", "conclusion", "other-group", "frontmatter",
                                 "large-file", "current", "none"})
        self.assertGreaterEqual(self.scores["overall"]["n"], 60)

    def test_large_file_was_generated_and_indexed(self):
        self.assertEqual(self.scores["index"]["skipped"], 0)
        large = self.scores["kinds"]["large-file"]
        self.assertEqual(large["top5"], 1.0)
