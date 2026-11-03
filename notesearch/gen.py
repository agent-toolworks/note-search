"""Generate a repeatable set of engineering-style notes, for scale tests and benchmarks."""
from __future__ import annotations

import os
import random

_WORDS = ("the a service table request retry timeout pool connection queue worker cron job deploy release "
          "rollback replica primary lag index partition migration schema column row lock transaction cache "
          "latency error alert dashboard owner team incident cause fix conclusion runbook step check config "
          "flag feature customer order payment refund invoice stock warehouse shipment email token session "
          "key rotation certificate network region cluster node memory disk cpu log trace metric budget "
          "because after before when then still never always only every daily weekly monthly").split()


_SYL = ("ka ri to mu sen la vor pel tin dra gos ple mar qui zan fo lu nek bar sti on al ep ur ix "
        "cor dem fin hal jor kel mon nar pos rud sal tev vin wol yar").split()


def _lexicon(rnd, n=6000) -> list:
    """Made-up words, so text has a long tail like real notes (Zipf-like use below)."""
    out, seen = [], set()
    while len(out) < n:
        w = "".join(rnd.choice(_SYL) for _ in range(rnd.randint(2, 4)))
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def _word(rnd, lex):
    """Domain words a third of the time; otherwise a lexicon word, frequent ones far more often."""
    if rnd.random() < 0.33:
        return rnd.choice(_WORDS)
    i = int(len(lex) * (rnd.random() ** 3))  # skewed towards the start: a few common words, a long tail
    return lex[i]


def _ident(rnd, n):
    kind = rnd.random()
    if kind < 0.4:
        return "svc-%s-%d" % (rnd.choice(_WORDS), n)
    if kind < 0.8:
        return "tbl_%s_%d" % (rnd.choice(_WORDS), n)
    return "%sService%d" % (rnd.choice(_WORDS).capitalize(), n)


def _para(rnd, idents, n, lex):
    out = []
    for _ in range(n):
        if rnd.random() < 0.05:
            out.append("`%s`" % rnd.choice(idents))
        else:
            out.append(_word(rnd, lex))
    return " ".join(out).capitalize() + "."


def note(rnd, i, idents, lex, big=False):
    lines = ["---", "name: Note %d %s" % (i, rnd.choice(idents)),
             "service: %s" % rnd.choice(idents), "updated: 2026-%02d-%02d" % (rnd.randint(1, 12), rnd.randint(1, 28)),
             "tags: [%s, %s]" % (rnd.choice(_WORDS), rnd.choice(_WORDS)), "---",
             "# Note %d about %s" % (i, rnd.choice(idents)), ""]
    sections = rnd.randint(200, 400) if big else rnd.randint(2, 8)
    for s in range(sections):
        lines.append("## %s %s %d" % (_word(rnd, lex).capitalize(), _word(rnd, lex), s))
        lines.append("")
        for _ in range(rnd.randint(1, 4) * (10 if big else 1)):
            lines.append(_para(rnd, idents, rnd.randint(20, 90), lex))
            lines.append("")
    return "\n".join(lines) + "\n"


def generate(root: str, count: int, seed: int = 1, projects: int = 40, big_every: int = 1000) -> list:
    """Write `count` notes under root/<project>/; returns their paths."""
    rnd = random.Random(seed)
    lex = _lexicon(rnd)
    idents = [_ident(rnd, n) for n in range(max(50, count // 5))]
    paths = []
    for i in range(count):
        d = os.path.join(root, "proj%02d" % (i % projects))
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "note%05d.md" % i)
        with open(p, "w") as fh:
            fh.write(note(rnd, i, idents, lex, big=(big_every and i % big_every == big_every - 1)))
        paths.append(p)
    return paths
