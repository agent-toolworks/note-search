"""Read a note as a stream and cut it into sections.

A note is split at its headings. A section longer than `max_chars` is cut at
paragraph boundaries into pieces that overlap by about `overlap` characters,
so a sentence across a cut is still found. Every piece knows its heading path
and its line range in the file.

The file is read line by line and never held whole, so a 100 MB note costs no
more memory than a piece. Reading stops at `max_bytes`; the note is then
marked partial.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import frontmatter as fmlib

MAX_CHARS = 2000
OVERLAP = 200

_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_SETEXT1 = re.compile(r"^ {0,3}=+[ \t]*$")
_SETEXT2 = re.compile(r"^ {0,3}-+[ \t]*$")
_LIST_OR_QUOTE = re.compile(r"^\s*([-*+]\s|\d+[.)]\s|>)")


@dataclass
class Piece:
    ord: int
    path: tuple  # heading titles, outermost first
    line_start: int
    line_end: int
    text: str
    doc_title: str = ""  # the note's first H1, when one was seen by this piece's end
    byte_start: int = 0  # where the piece's first line starts in the file

    @property
    def heading(self) -> str:
        return self.path[-1] if self.path else ""


class Splitter:
    """Feed (line number, text) pairs; collect Pieces from `out`."""

    def __init__(self, max_chars: int = MAX_CHARS, overlap: int = OVERLAP, sink=None):
        self.sink = sink
        self.max_chars = max_chars
        self.overlap = overlap
        self.stack: list = []  # (level, title)
        self.lines: list = []  # (lineno, text)
        self.chars = 0
        self.fence = None
        self.ord = 0
        self.out: list = []
        self.first_h1 = None
        self.offsets: dict = {}  # line number -> byte offset, for lines still in the buffer

    # -- helpers ---------------------------------------------------------
    def _path(self) -> tuple:
        return tuple(t for _, t in self.stack)

    def _emit(self, lines: list):
        if not lines:
            return
        text = "\n".join(t for _, t in lines)
        if not text.strip():
            return
        piece = Piece(self.ord, self._path(), lines[0][0], lines[-1][0], text, self.first_h1 or "",
                      self.offsets.get(lines[0][0], 0))
        if len(self.offsets) > 4096:
            low = min((ln for ln, _ in self.lines), default=lines[-1][0])
            self.offsets = {k: v for k, v in self.offsets.items() if k >= min(low, lines[-1][0])}
        self.ord += 1
        if self.sink is not None:
            self.sink(piece)
        else:
            self.out.append(piece)

    def _cut(self):
        """Emit pieces while the buffer is over the limit."""
        while self.chars >= self.max_chars and len(self.lines) > 1:
            total = 0
            cut = None
            for i, (_, t) in enumerate(self.lines):
                total += len(t) + 1
                if total >= self.max_chars:
                    cut = i + 1
                    break
            if cut is None:
                return
            # Prefer the last blank line in the back half of the piece.
            best = None
            run = 0
            for i in range(cut):
                run += len(self.lines[i][1]) + 1
                if not self.lines[i][1].strip() and run >= self.max_chars // 2:
                    best = i + 1
            if best is not None and best < len(self.lines):
                cut = best
            piece = self.lines[:cut]
            rest = self.lines[cut:]
            if not rest:
                return
            keep = []
            kept = 0
            for ln in reversed(piece):
                if kept + len(ln[1]) + 1 > self.overlap:
                    break
                keep.insert(0, ln)
                kept += len(ln[1]) + 1
            if len(keep) == len(piece):
                keep = []
            self._emit(piece)
            self.lines = keep + rest
            self.chars = sum(len(t) + 1 for _, t in self.lines)

    def _flush(self):
        self._emit(self.lines)
        self.lines = []
        self.chars = 0

    def _heading(self, level: int, title: str, lines: list):
        title = title.strip() or "(untitled)"
        if level == 1 and self.first_h1 is None:
            self.first_h1 = title  # set first, so the text above the H1 carries it too
        self._flush()
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        self.stack.append((level, title))
        self.lines = list(lines)
        self.chars = sum(len(t) + 1 for _, t in self.lines)

    def _add(self, lineno: int, text: str):
        # A single line longer than a piece is cut into piece-sized parts.
        if len(text) > self.max_chars:
            for k in range(0, len(text), self.max_chars):
                self._add(lineno, text[k:k + self.max_chars])
            return
        self.lines.append((lineno, text))
        self.chars += len(text) + 1
        if self.chars >= self.max_chars:
            self._cut()

    # -- input -----------------------------------------------------------
    def feed(self, lineno: int, text: str, offset: int = None):
        if offset is not None and lineno not in self.offsets:
            self.offsets[lineno] = offset
        fence = _FENCE.match(text)
        if self.fence:
            if fence and fence.group(1)[0] == self.fence[0] and len(fence.group(1)) >= len(self.fence):
                self.fence = None
            self._add(lineno, text)
            return
        if fence:
            self.fence = fence.group(1)
            self._add(lineno, text)
            return
        m = _ATX.match(text)
        if m:
            self._heading(len(m.group(1)), m.group(2) or "", [(lineno, text)])
            return
        if (_SETEXT1.match(text) or _SETEXT2.match(text)) and self.lines:
            prev_no, prev = self.lines[-1]
            is_heading_line = len(self.lines) == 1 and _ATX.match(prev)
            if prev.strip() and not is_heading_line and not _LIST_OR_QUOTE.match(prev) and prev_no == lineno - 1:
                self.lines.pop()
                self.chars -= len(prev) + 1
                level = 1 if text.strip().startswith("=") else 2
                self._heading(level, prev, [(prev_no, prev), (lineno, text)])
                return
        self._add(lineno, text)

    def close(self) -> list:
        self._flush()
        return self.out


@dataclass
class Note:
    fm: dict = field(default_factory=dict)
    fm_error: str = ""
    fm_warnings: list = field(default_factory=list)
    pieces: list = field(default_factory=list)
    title_h1: str = ""
    partial: bool = False
    lines: int = 0


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").rstrip("\r\n")


def read_note(path: str, max_bytes: int, max_chars: int = MAX_CHARS, overlap: int = OVERLAP,
              sink=None, on_frontmatter=None) -> Note:
    """Read one note: frontmatter, pieces, and whether it was cut short.

    With `sink`, each piece is handed over as it is cut and none are kept, so
    memory stays at one piece whatever the file's size. `on_frontmatter(note)`
    is called once the frontmatter is known, before the first piece.
    """
    note = Note()
    splitter = Splitter(max_chars, overlap, sink)
    read = 0
    lineno = 0
    with open(path, "rb") as fh:

        def lines():
            nonlocal read, lineno
            for raw in fh:
                offset = read
                read += len(raw)
                if read > max_bytes:
                    note.partial = True
                    return
                lineno += 1
                text = _decode(raw)
                if lineno == 1 and text.startswith("\ufeff"):
                    text = text[1:]
                yield lineno, text, offset

        it = lines()
        first = next(it, None)
        if first is None:
            return note
        body_start = [first]
        marker = first[1].strip()
        if marker in ("---", "+++"):
            block = []
            closing = None
            for item in it:
                text = item[1].strip()
                if (marker == "---" and text in ("---", "...")) or (marker == "+++" and text == "+++"):
                    closing = item
                    body_start = []
                    break
                block.append(item)
                if len(block) >= fmlib.MAX_LINES:
                    break
            if closing:
                try:
                    parse = fmlib.parse_yaml if marker == "---" else fmlib.parse_toml
                    note.fm, note.fm_warnings = parse([t for _, t, _ in block])
                except fmlib.FrontmatterError as exc:
                    note.fm_error = str(exc)
                    note.fm = {}
                    body_start = [first] + block + [closing]
            else:
                body_start = [first] + block
        if on_frontmatter is not None:
            on_frontmatter(note)
        for ln, text, off in body_start:
            splitter.feed(ln, text, off)
        for ln, text, off in it:
            splitter.feed(ln, text, off)
    note.pieces = splitter.close()
    note.title_h1 = splitter.first_h1 or ""
    note.lines = lineno
    return note
