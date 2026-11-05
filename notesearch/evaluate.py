"""`ns eval`: score search quality against a question set.

The question file names a config (relative to the file) and lists questions:

    {"config": "ns.json",
     "generate": [{"group": "kb", "path": "journal/big.md", "mb": 5, "seed": 7,
                   "inject": [{"at_mb": 3.2, "text": "## A heading\\nThe answer."}]}],
     "questions": [{"id": "q01", "kind": "exact", "ask": "what writes to orders_v2?",
                    "wordings": ["orders_v2 writes", "OrderService orders_v2"],
                    "expect": ["kb:tables/orders_v2.md"], "groups": ["kb"]}]}

The folder holding the file is copied to a temporary folder, generated files
are written there, and the copy is indexed from nothing, so a run never
touches real notes or the real index.

Scores, per kind and overall: the right note first; in the top 5; asked as
written only (`ask` alone, no other wordings); tokens per answer. For kind
"none", the right answer is no strict hit. Release gates: top 5 at least
90%, first at least 70%, no kind down more than 5 points from the baseline,
no answer over its token budget. A set that says `"floors": false` (one made
to measure what search misses) is held only to the last two.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import tempfile

from . import config as cfglib
from . import render
from .index import Index
from .refs import tokens
from .search import Searcher

GATE_TOP5 = 0.90
GATE_FIRST = 0.70
GATE_DROP = 0.05

_WORDS = ("deploy service queue latency request database replica cache config error retry timeout worker job "
          "batch metric alert dashboard incident change owner team schedule cron backup restore migration "
          "index partition shard network host region cluster node pod container image build release rollback "
          "ticket review approval note follow-up action summary log trace span sample").split()


def generate(path: str, mb: float, seed: int, inject: list):
    """A large dated journal of filler entries, with given sections at given depths."""
    rnd = random.Random(seed)
    target = int(mb * 1024 * 1024)
    marks = sorted(((int(i["at_mb"] * 1024 * 1024), i["text"]) for i in inject), key=lambda x: x[0])
    written = 0
    day = 0
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Operations journal\n\nDaily notes from the on-call rotation.\n\n")
        while written < target:
            while marks and written >= marks[0][0]:
                block = "\n" + marks.pop(0)[1].strip() + "\n\n"
                fh.write(block)
                written += len(block)
            day += 1
            entry = ["## Entry %d" % day, ""]
            for _ in range(rnd.randint(2, 5)):
                entry.append(" ".join(rnd.choice(_WORDS) for _ in range(rnd.randint(12, 40))) + ".")
                entry.append("")
            block = "\n".join(entry) + "\n"
            fh.write(block)
            written += len(block)
        for _, text in marks:
            fh.write("\n" + text.strip() + "\n")


def _prepare(qpath: str, work: str, engine: str = None) -> tuple:
    with open(qpath, encoding="utf-8") as fh:
        spec = json.load(fh)
    src = os.path.dirname(os.path.abspath(qpath))
    dst = os.path.join(work, "notes")
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.pyc", "__pycache__", "baseline*.json"))
    cfg_path = os.path.join(dst, spec.get("config", "ns.json"))
    with open(cfg_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    for gen in spec.get("generate", []):
        group = raw["groups"][gen["group"]]
        dirs = group["dirs"] if isinstance(group["dirs"], list) else [group["dirs"]]
        base = os.path.normpath(os.path.join(os.path.dirname(cfg_path), dirs[0]))
        out = os.path.join(base, gen["path"])
        os.makedirs(os.path.dirname(out), exist_ok=True)
        generate(out, gen["mb"], gen.get("seed", 1), gen.get("inject", []))
    raw["cache_dir"] = os.path.join(work, "cache")
    if engine:
        for g in raw["groups"].values():
            g["engine"] = engine
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(raw, fh)
    return spec, cfg_path


def _rank(notes: list, expect: list) -> int:
    for i, n in enumerate(notes, 1):
        if n.ref in expect:
            return i
    return 0


def score(qpath: str, verbose: bool = False, engine: str = None) -> dict:
    work = tempfile.mkdtemp(prefix="ns-eval-")
    saved_cache = os.environ.pop("NS_CACHE", None)
    try:
        spec, cfg_path = _prepare(qpath, work, engine)
        cfg = cfglib.load(cfg_path)
        if cfg.embedded():
            # Vectors depend only on the model and the text: keep them across runs.
            os.makedirs(os.path.expanduser(cfglib.DEFAULT_CACHE), mode=0o700, exist_ok=True)
            cfg.vectors = os.path.join(os.path.expanduser(cfglib.DEFAULT_CACHE), "eval-vectors.db")
        idx = Index(cfg)
        report = idx.update(embed_seconds=None)
        if cfg.embedded() and report.embed_waiting:
            raise RuntimeError("; ".join(report.warnings))
        conn = idx.open()
        s = Searcher(conn, cfg)
        results = []
        for q in spec["questions"]:
            groups = cfg.group_ids(q.get("groups"))
            wordings = q.get("wordings") or [q["ask"]]
            if q["ask"] not in wordings:
                wordings = [q["ask"]] + wordings
            res = s.search(wordings, groups)
            text, _ = render.search_text(res, cfg.limits.answer_tokens, 0, lambda n: s.related(n.file_id, groups, 2))
            ask_only = s.search([q["ask"]], groups)
            expect = q.get("expect", [])
            if q["kind"] == "none":
                ok_first = ok_top5 = not res.strict_found
                ask_top5 = not ask_only.strict_found
                rank = 0
            else:
                rank = _rank(res.notes, expect)
                ok_first, ok_top5 = rank == 1, 0 < rank <= 5
                r2 = _rank(ask_only.notes, expect)
                ask_top5 = 0 < r2 <= 5
            results.append({
                "id": q["id"], "kind": q["kind"], "first": ok_first, "top5": ok_top5, "ask_top5": ask_top5,
                "rank": rank, "tokens": tokens(text), "stages": res.stages,
                "top": [n.ref for n in res.notes[:5]], "expect": expect,
            })
        budget = cfg.limits.answer_tokens
        kinds = {}
        for r in results:
            kinds.setdefault(r["kind"], []).append(r)

        def agg(rs):
            n = len(rs)
            return {"n": n, "first": sum(r["first"] for r in rs) / n, "top5": sum(r["top5"] for r in rs) / n,
                    "ask_top5": sum(r["ask_top5"] for r in rs) / n,
                    "tokens_avg": round(sum(r["tokens"] for r in rs) / n), "tokens_max": max(r["tokens"] for r in rs)}

        return {
            "floors": spec.get("floors", True),
            "overall": agg(results), "kinds": {k: agg(v) for k, v in sorted(kinds.items())},
            "over_budget": [r["id"] for r in results if r["tokens"] > budget], "budget": budget,
            "index": {"notes": report.added, "ms": report.ms, "skipped": len(report.skipped)},
            "questions": results,
        }
    finally:
        if saved_cache is not None:
            os.environ["NS_CACHE"] = saved_cache
        shutil.rmtree(work, ignore_errors=True)


def gates(scores: dict, baseline: dict = None) -> list:
    failures = []
    o = scores["overall"]
    floors = scores.get("floors", True)
    if floors and o["top5"] < GATE_TOP5:
        failures.append("top 5 is %.0f%%, under %.0f%%" % (o["top5"] * 100, GATE_TOP5 * 100))
    if floors and o["first"] < GATE_FIRST:
        failures.append("first is %.0f%%, under %.0f%%" % (o["first"] * 100, GATE_FIRST * 100))
    if scores["over_budget"]:
        failures.append("answers over the token budget: " + ", ".join(scores["over_budget"]))
    if baseline:
        for kind, b in baseline.get("kinds", {}).items():
            k = scores["kinds"].get(kind)
            if k and k["top5"] < b["top5"] - GATE_DROP - 1e-9:
                failures.append("kind %s: top 5 fell from %.0f%% to %.0f%%" % (kind, b["top5"] * 100, k["top5"] * 100))
    return failures


def run(args) -> tuple:
    scores = score(args.questions, args.verbose, getattr(args, "engine", None))
    baseline = None
    if args.baseline and os.path.exists(args.baseline):
        with open(args.baseline) as fh:
            baseline = json.load(fh)
    failures = gates(scores, baseline)
    if args.write_baseline:
        keep = {k: v for k, v in scores.items() if k != "questions"}
        with open(args.write_baseline, "w") as fh:
            json.dump(keep, fh, indent=2, sort_keys=True)
            fh.write("\n")
    out = ["%-12s %4s %7s %7s %9s %8s %8s" % ("kind", "n", "first", "top 5", "ask only", "tokens", "max")]

    def row(name, a):
        return "%-12s %4d %6.0f%% %6.0f%% %8.0f%% %8d %8d" % (
            name, a["n"], a["first"] * 100, a["top5"] * 100, a["ask_top5"] * 100, a["tokens_avg"], a["tokens_max"])

    for kind, a in scores["kinds"].items():
        out.append(row(kind, a))
    out.append(row("overall", scores["overall"]))
    out.append("index: %d notes in %d ms; budget %d tokens" % (
        scores["index"]["notes"], scores["index"]["ms"], scores["budget"]))
    if args.verbose:
        for r in scores["questions"]:
            if not r["top5"] or not r["first"]:
                out.append("%s %s %s rank %d expect %s got %s" % (
                    "MISS" if not r["top5"] else "LOW ", r["id"], r["kind"], r["rank"], r["expect"], r["top"]))
    for f in failures:
        out.append("GATE FAILED: " + f)
    if not failures:
        out.append("gates passed")
    data = dict(scores)
    data["ok"] = not failures
    data["gate_failures"] = failures
    code = 1 if (failures and args.gate) else 0
    return code, "\n".join(out), data
