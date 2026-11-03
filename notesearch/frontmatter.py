"""Read the frontmatter notes actually use, without a YAML library.

The Mac's system Python has no YAML reader, and note-search must not need a
third-party package. Notes we don't control use a small part of YAML: single
values, quoted strings, `- item` lists, `[a, b]` lists, nested keys and the
odd block scalar. This reads that part. Anything else raises
FrontmatterError, and the caller indexes the block as plain text, so a bad
header never hides a note.

Values come back as strings or lists of strings. Nested keys are flattened to
`parent.child`. Dates stay strings.
"""
from __future__ import annotations

import re

MAX_LINES = 500

_KEY = re.compile(r"^([A-Za-z0-9_\"'][^:]*?)\s*:(?:[ \t]+(.*)|[ \t]*)$")
_TOML_KEY = re.compile(r"^([A-Za-z0-9_\"'.-][^=]*?)\s*=\s*(.*)$")
_TOML_TABLE = re.compile(r"^\[\s*([^\]]+?)\s*\]$")
_BLOCK = ("|", ">", "|-", ">-", "|+", ">+")


class FrontmatterError(Exception):
    pass


def _strip_comment(value: str) -> str:
    """Drop a trailing ` # comment` that is not inside quotes."""
    quote = None
    for i, ch in enumerate(value):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or value[i - 1] in " \t"):
            return value[:i].rstrip()
    return value


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == "'":
            return inner.replace("''", "'")
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    return value


def _split_inline(inner: str) -> list:
    items, buf, quote, depth = [], [], None, 0
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        items.append("".join(buf))
    return [_unquote(x) for x in items if x.strip()]


def _scalar(value: str):
    value = _strip_comment(value.strip())
    if value.startswith("[") and value.endswith("]"):
        return _split_inline(value[1:-1])
    return _unquote(value)


def _key(raw: str) -> str:
    return _unquote(raw.strip())


def parse_yaml(lines: list) -> tuple:
    """Parse YAML-subset lines into (dict, warnings)."""
    data: dict = {}
    warnings: list = []
    stack = [(-1, "")]  # (indent, key prefix) for nested maps
    list_key = None
    list_indent = -1
    i = 0
    n = len(lines)

    def put(key, value):
        if key in data:
            warnings.append("duplicate key %r; the last value is kept" % key)
        data[key] = value

    while i < n:
        raw = lines[i].replace("\t", "  ")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))

        if stripped == "-" or stripped.startswith("- "):
            if list_key is None or indent < list_indent:
                raise FrontmatterError("line %d: list item with no key above it" % (i + 1))
            data[list_key].append(_scalar(stripped[1:].strip()) if stripped != "-" else "")
            item_indent = indent
            i += 1
            # A list of maps: fold the item's further keys into the item text.
            while i < n:
                nxt = lines[i].replace("\t", "  ")
                ns = nxt.strip()
                nind = len(nxt) - len(nxt.lstrip(" "))
                if ns and nind > item_indent and not ns.startswith("- ") and not ns.startswith("#"):
                    last = data[list_key][-1]
                    data[list_key][-1] = (str(last) + "; " if last else "") + ns
                    i += 1
                else:
                    break
            continue

        while indent <= stack[-1][0]:
            stack.pop()
        m = _KEY.match(stripped)
        if not m:
            raise FrontmatterError("line %d: cannot read %r" % (i + 1, stripped[:60]))
        list_key = None
        key = stack[-1][1] + _key(m.group(1))
        value = m.group(2)

        if value is None or not _strip_comment(value).strip():
            # Look ahead: a list, a nested map, or an empty value.
            j = i + 1
            while j < n and (not lines[j].strip() or lines[j].strip().startswith("#")):
                j += 1
            if j < n:
                nxt = lines[j].replace("\t", "  ")
                nind = len(nxt) - len(nxt.lstrip(" "))
                ns = nxt.strip()
                if (ns == "-" or ns.startswith("- ")) and nind >= indent:
                    put(key, [])
                    list_key = key
                    list_indent = nind
                    i += 1
                    continue
                if nind > indent:
                    stack.append((indent, key + "."))
                    i += 1
                    continue
            put(key, "")
            i += 1
            continue

        value = _strip_comment(value).strip()
        if value in _BLOCK:
            block = []
            i += 1
            while i < n:
                nxt = lines[i].replace("\t", "  ")
                if nxt.strip() and len(nxt) - len(nxt.lstrip(" ")) <= indent:
                    break
                block.append(nxt.strip())
                i += 1
            joiner = "\n" if value.startswith("|") else " "
            put(key, joiner.join(block).strip())
            continue

        put(key, _scalar(value))
        i += 1
    return data, warnings


def parse_toml(lines: list) -> tuple:
    data: dict = {}
    warnings: list = []
    prefix = ""
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        t = _TOML_TABLE.match(s)
        if t:
            prefix = _key(t.group(1)) + "."
            continue
        m = _TOML_KEY.match(s)
        if not m:
            raise FrontmatterError("line %d: cannot read %r" % (i + 1, s[:60]))
        key = prefix + _key(m.group(1))
        if key in data:
            warnings.append("duplicate key %r; the last value is kept" % key)
        data[key] = _scalar(m.group(2))
    return data, warnings


def flatten(data: dict) -> list:
    """(key, value-string) pairs, one per value, for the metadata column."""
    out = []
    for key, value in data.items():
        if isinstance(value, list):
            for v in value:
                out.append((key, str(v)))
        else:
            out.append((key, str(value)))
    return out


def values(data: dict, names: list) -> list:
    """All string values of the first fields in `names` present in `data`.

    Keys match case-insensitively, so `Service` and `service` are the same
    field in notes we don't control.
    """
    lower = {k.lower(): v for k, v in data.items()}
    out = []
    for name in names:
        v = lower.get(name.lower())
        if v is None:
            continue
        if isinstance(v, list):
            out.extend(str(x) for x in v if str(x).strip())
        elif str(v).strip():
            out.append(str(v))
    return out
