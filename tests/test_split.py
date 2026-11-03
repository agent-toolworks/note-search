"""Suite 2: splitting notes into sections and pieces."""
import random
import tracemalloc

from tests.helpers import Tree
from notesearch.split import Splitter, read_note


def split(text, max_chars=2000, overlap=200):
    s = Splitter(max_chars, overlap)
    for i, line in enumerate(text.split("\n"), 1):
        s.feed(i, line)
    return s.close()


class Headings(Tree):
    def test_no_headings(self):
        ps = split("just\ntext\n\nmore")
        self.assertEqual(len(ps), 1)
        self.assertEqual(ps[0].path, ())
        self.assertEqual((ps[0].line_start, ps[0].line_end), (1, 4))

    def test_heading_path_stack(self):
        ps = split("# A\na\n## B\nb\n### C\nc\n## D\nd\n# E\ne")
        self.assertEqual([p.path for p in ps], [("A",), ("A", "B"), ("A", "B", "C"), ("A", "D"), ("E",)])
        self.assertEqual(ps[2].heading, "C")

    def test_hash_inside_code_blocks(self):
        ps = split("# A\n```\n# not a heading\n```\n~~~~\n## nor this\n~~~\n## still code\n~~~~\n## B\nx")
        self.assertEqual([p.path for p in ps], [("A",), ("A", "B")])

    def test_underlined_headings(self):
        ps = split("Title\n=====\ntext\nSub\n---\nmore\n\n---\nafter rule\n- item\n---\nend")
        self.assertEqual([p.path for p in ps], [("Title",), ("Title", "Sub")])
        self.assertIn("after rule", ps[1].text)

    def test_seven_hashes_and_closing_hashes(self):
        ps = split("# A #\n####### not a heading\n## B ##")
        self.assertEqual([p.path for p in ps], [("A",), ("A", "B")])
        self.assertIn("####### not a heading", ps[0].text)

    def test_empty_heading(self):
        ps = split("#\ntext")
        self.assertEqual(ps[0].path, ("(untitled)",))


class Pieces(Tree):
    def test_giant_section_is_cut_with_overlap(self):
        paras = ["Paragraph %d " % i + "word " * 60 for i in range(80)]
        text = "# Big\n" + "\n\n".join(paras)
        ps = split(text)
        self.assertGreater(len(ps), 5)
        for p in ps:
            self.assertLessEqual(len(p.text), 2000 + 400)
            self.assertEqual(p.path, ("Big",))
        for a, b in zip(ps, ps[1:]):
            self.assertLessEqual(b.line_start, a.line_end + 1)  # no gap
            self.assertTrue(b.line_start <= a.line_end or b.line_start == a.line_end + 1)
        overlapping = sum(1 for a, b in zip(ps, ps[1:]) if b.line_start <= a.line_end)
        self.assertGreater(overlapping, 0)

    def test_pieces_cover_the_text_exactly(self):
        """Generated documents: every piece is the exact lines it claims, and all lines are covered."""
        rnd = random.Random(42)
        kinds = ["# H{}", "## H{}", "### H{}", "", "", "text {} " * 3, "word " * 50, "```", "- item {}", "Setext {}", "===", "---"]
        for trial in range(300):
            lines = []
            for i in range(rnd.randint(1, 200)):
                lines.append(rnd.choice(kinds).replace("{}", str(i)))
            text = "\n".join(lines)
            ps = split(text, max_chars=rnd.choice([300, 800, 2000]), overlap=rnd.choice([0, 50, 200]))
            covered = set()
            for p in ps:
                self.assertEqual(p.text, "\n".join(lines[p.line_start - 1:p.line_end]), "trial %d" % trial)
                covered.update(range(p.line_start, p.line_end + 1))
            for n, line in enumerate(lines, 1):
                if line.strip():
                    self.assertIn(n, covered, "trial %d line %d" % (trial, n))


class Large(Tree):
    def test_large_file_streams_in_little_memory(self):
        p = self.path("big.md")
        with open(p, "w") as fh:
            fh.write("# Big\n")
            line = ("lorem ipsum dolor sit amet " * 4).strip() + "\n"
            for i in range(200000):  # about 21 MB
                if i % 500 == 0:
                    fh.write("\n## Part %d\n" % i)
                fh.write(line)
        count = [0]
        tracemalloc.start()
        note = read_note(p, 10 ** 9, sink=lambda piece: count.__setitem__(0, count[0] + 1))
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertFalse(note.partial)
        self.assertGreater(count[0], 10000)
        self.assertLess(peak, 5 * 1024 * 1024, "peak memory %d bytes" % peak)

    def test_size_limit_marks_partial(self):
        p = self.write("n.md", "# A\n" + "line of text\n" * 10000)
        note = read_note(p, 4096)
        self.assertTrue(note.partial)
        self.assertLess(max(x.line_end for x in note.pieces), 400)
