"""Names of things: identifiers in engineering notes.

`orders_v2`, `payment-gateway` and `OrderService` must be found whole and by
their parts. SQLite's tokenizer already splits on `_` and `-`, so
`orders_v2` is found as "orders v2". It does not split `OrderService`, so
this module adds the parts ("order service") to a separate column.

It also lists the vocabulary of a text, used to correct a misspelled name and
to suggest the nearest names when nothing matches.
"""
from __future__ import annotations

import re

# snake_case, kebab-case and dotted names with at least one separator;
# camelCase; PascalCase with two or more humps.
_IDENT = re.compile(
    r"(?<![\w-])(?:"
    r"[A-Za-z][A-Za-z0-9]*(?:[_\-.][A-Za-z0-9]+)+"
    r"|[a-z]+[A-Z][A-Za-z0-9]*"
    r"|[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*"
    r")(?![\w-])"
)
_HUMPS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_WORD = re.compile(r"[^\W_][\w\-.]*[^\W_]|[^\W_]")
_SEP = re.compile(r"[_\-.]+")


def identifiers(text: str) -> list:
    return _IDENT.findall(text)


def parts(name: str) -> list:
    out = []
    for chunk in _SEP.split(name):
        out.extend(h.lower() for h in _HUMPS.findall(chunk))
    return [p for p in out if p]


def expansions(text: str, limit: int = 400) -> str:
    """Extra words for the identifier column: camelCase parts, joined forms."""
    seen = set()
    out = []
    for ident in identifiers(text):
        if ident in seen:
            continue
        seen.add(ident)
        ps = parts(ident)
        if len(ps) > 1:
            out.append(" ".join(ps))
            out.append("".join(ps))
        if len(seen) >= limit:
            break
    return " ".join(out)


def is_name(token: str) -> bool:
    """Does a query token look like a name rather than an English word?"""
    return bool(_IDENT.fullmatch(token)) or any(c.isdigit() for c in token)


def vocabulary(text: str) -> set:
    """Lowercased words and whole names of 3 or more characters."""
    out = set()
    for w in _WORD.findall(text):
        w = w.strip(".-").lower()
        if 3 <= len(w) <= 64:
            out.add(w)
            if _SEP.search(w):
                out.update(p for p in _SEP.split(w) if len(p) >= 3)
    return out


def trigrams(term: str) -> list:
    t = term.lower()
    return [t[i:i + 3] for i in range(len(t) - 2)]
