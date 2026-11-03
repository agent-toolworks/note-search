"""The `ns` command."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys

from . import __version__
from . import config as cfglib
from . import refs, render
from .index import Index, NotReady
from .search import Searcher

_FILTER = re.compile(r"^[A-Za-z_][\w.-]*=\S.*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class UsageError(Exception):
    pass


def _emit(args, text=None, data=None):
    if args.json:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=None)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(text + ("\n" if not text.endswith("\n") else ""))


def _open(args, update=True):
    cfg = cfglib.load(args.config)
    idx = Index(cfg)
    report = None
    if update and not args.no_update:
        report = idx.update()
    conn = idx.open()
    return cfg, idx, conn, report


def _warnings(report) -> list:
    if not report:
        return []
    out = list(report.warnings)
    if report.skipped:
        out.append("%d file%s skipped; see ns doctor" % (len(report.skipped), "" if len(report.skipped) == 1 else "s"))
    return out


def cmd_search(args):
    wordings, filters = [], {}
    for a in args.words:
        if _FILTER.match(a):
            k, v = a.split("=", 1)
            filters[k] = v
        else:
            wordings.append(a)
    if not wordings:
        raise UsageError("give at least one wording to search for")
    if args.since and not _DATE.match(args.since):
        raise UsageError("--since takes a date like 2026-06-01")
    cfg, idx, conn, report = _open(args)
    groups = cfg.group_ids([g for g in (args.group or "").split(",") if g])
    s = Searcher(conn, cfg)
    res = s.search(wordings, groups, filters, args.since)
    res.warnings = _warnings(report) + res.warnings
    related = lambda n: s.related(n.file_id, groups, 2)
    if args.json:
        query = {"wordings": res.wordings, "groups": groups, "filters": filters, "since": args.since}
        _emit(args, data=render.search_json(res, args.offset, args.limit, related, query))
    else:
        text, _ = render.search_text(res, cfg.limits.answer_tokens, args.offset, related)
        _emit(args, text)


def cmd_get(args):
    cfg, idx, conn, report = _open(args)
    got = refs.get(conn, cfg, args.ref)
    warns = _warnings(report)
    if args.json:
        d = got.as_dict()
        d["ok"] = True
        d["warnings"] = warns
        _emit(args, data=d)
    else:
        _emit(args, render.get_text(got, warns))


def cmd_related(args):
    cfg, idx, conn, report = _open(args)
    ref = refs.parse(args.ref)
    row = conn.execute("SELECT file_id FROM members WHERE grp=? AND rel=?", (ref.grp, ref.rel)).fetchone()
    if not row:
        raise refs.RefError("no note %s:%s in the index" % (ref.grp, ref.rel))
    fid = row[0]
    groups = list(cfg.groups)
    s = Searcher(conn, cfg)
    linked = s.related(fid, groups, 50)
    ents = json.loads(conn.execute("SELECT entities FROM files WHERE id=?", (fid,)).fetchone()[0] or "[]")
    sharing = []
    if ents:
        for other_id, other_ents in conn.execute("SELECT id, entities FROM files WHERE id != ?", (fid,)):
            common = set(ents) & set(json.loads(other_ents or "[]"))
            if common:
                m = conn.execute("SELECT grp, rel FROM members WHERE file_id=? LIMIT 1", (other_id,)).fetchone()
                if m:
                    sharing.append(("%s:%s" % m, sorted(common)))
    if args.json:
        _emit(args, data={"ok": True, "ref": "%s:%s" % (ref.grp, ref.rel), "linked": linked,
                          "shared_entities": [{"ref": r, "entities": e} for r, e in sharing[:50]]})
        return
    out = ["%s:%s" % (ref.grp, ref.rel)]
    out.append("linked: " + (", ".join(linked) if linked else "none"))
    if sharing:
        out.append("sharing entities:")
        out.extend("  %s (%s)" % (r, ", ".join(e)) for r, e in sharing[:20])
    _emit(args, "\n".join(out))


def cmd_groups(args):
    cfg, idx, conn, report = _open(args)
    counts = dict(conn.execute("SELECT grp, files FROM groups_state"))
    updated = conn.execute("SELECT value FROM meta WHERE key='updated'").fetchone()
    data = []
    for gid, g in cfg.groups.items():
        dirs, warns = g.resolve()
        data.append({"id": gid, "dirs": [d.path for d in dirs], "patterns": g.dirs, "notes": counts.get(gid, 0),
                     "default": gid in (cfg.default_groups or cfg.groups), "warnings": warns})
    if args.json:
        _emit(args, data={"ok": True, "groups": data, "updated": float(updated[0]) if updated else None})
        return
    out = []
    for d in data:
        out.append("%s%s  %d notes  %s" % (d["id"], "" if d["default"] else " (not searched by default)",
                                           d["notes"], ", ".join(d["patterns"])))
        if len(d["dirs"]) > 1 or d["dirs"] != d["patterns"]:
            out.append("  %d folders" % len(d["dirs"]))
        out.extend("  warning: " + w for w in d["warnings"])
    _emit(args, "\n".join(out))


def cmd_fields(args):
    cfg, idx, conn, report = _open(args)
    cfg.group_ids([args.group])
    counts, examples = {}, {}
    total = 0
    for (fm,) in conn.execute(
            "SELECT f.fm FROM files f JOIN members m ON m.file_id=f.id WHERE m.grp=?", (args.group,)):
        total += 1
        for key, value in json.loads(fm or "{}").items():
            counts[key] = counts.get(key, 0) + 1
            ex = examples.setdefault(key, [])
            vals = value if isinstance(value, list) else [value]
            for v in vals:
                v = str(v)[:40]
                if v and v not in ex and len(ex) < 3:
                    ex.append(v)
    rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    if args.json:
        _emit(args, data={"ok": True, "group": args.group, "notes": total,
                          "fields": [{"field": k, "notes": n, "examples": examples[k]} for k, n in rows]})
        return
    out = ["%s (%d notes)" % (args.group, total)]
    width = max([len(k) for k, _ in rows] + [5])
    for k, n in rows:
        out.append("  %-*s %5d  [%s]" % (width, k, n, ", ".join(examples[k])))
    if not rows:
        out.append("  no frontmatter fields")
    _emit(args, "\n".join(out))


def cmd_index(args):
    cfg = cfglib.load(args.config)
    idx = Index(cfg)
    if args.rebuild and not idx.rebuild():
        raise NotReady("another process is updating the index; try the rebuild again in a moment")
    report = idx.update()
    data = {"ok": True, "added": report.added, "changed": report.changed, "removed": report.removed,
            "unchanged": report.unchanged, "skipped": len(report.skipped), "busy": report.busy,
            "ms": report.ms, "warnings": report.warnings}
    _emit(args, "added %(added)d, changed %(changed)d, removed %(removed)d, unchanged %(unchanged)d, "
                "skipped %(skipped)d in %(ms)d ms" % data + "".join("\nwarning: " + w for w in report.warnings),
          data)


def cmd_config(args):
    cfg = cfglib.load(args.config)
    data = {"ok": True, "path": cfg.path, "cache_dir": cfg.cache_dir, "default_groups": cfg.default_groups,
            "limits": cfg.limits.__dict__, "groups": {}}
    out = ["config %s is valid" % cfg.path]
    for gid, g in cfg.groups.items():
        dirs, warns = g.resolve()
        data["groups"][gid] = {"dirs": [{"path": d.path, "prefix": d.prefix} for d in dirs], "warnings": warns,
                               "roles": {k: v for k, v in g.roles.__dict__.items() if v}}
        out.append("%s: %d folder%s" % (gid, len(dirs), "" if len(dirs) == 1 else "s"))
        for d in dirs[:10]:
            out.append("  %s%s" % (d.path, ("  as " + d.prefix) if d.prefix else ""))
        if len(dirs) > 10:
            out.append("  … %d more" % (len(dirs) - 10))
        out.extend("  warning: " + w for w in warns)
        roles = ", ".join("%s=%s" % (k, "/".join(v)) for k, v in g.roles.__dict__.items() if v)
        out.append("  fields: " + roles)
    _emit(args, "\n".join(out), data)


def cmd_doctor(args):
    from . import doctor
    code, text, data = doctor.run(args)
    _emit(args, text, data)
    return code


def cmd_eval(args):
    from . import evaluate
    code, text, data = evaluate.run(args)
    _emit(args, text, data)
    return code


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="config file (default $NS_CONFIG or %s)" % cfglib.DEFAULT_PATH)
    common.add_argument("--json", action="store_true", help="answer in JSON")
    common.add_argument("--no-update", action="store_true", help="search the index as it is, without updating it")
    p = argparse.ArgumentParser(prog="ns", description="Search folders of Markdown notes across projects.")
    p.add_argument("--version", action="version", version="ns " + __version__)
    sub = p.add_subparsers(dest="cmd", metavar="command")

    s = sub.add_parser("search", parents=[common], help="search notes; give several wordings at once")
    s.add_argument("words", nargs="+", metavar="WORDING", help='a wording ("checkout slow"), or a filter field=value')
    s.add_argument("--group", help="groups to search, comma-separated (default: default_groups, else all)")
    s.add_argument("--since", help="only notes dated on or after this day (YYYY-MM-DD)")
    s.add_argument("--offset", type=int, default=0, help="skip this many notes (to see more)")
    s.add_argument("--limit", type=int, default=20, help="notes in a --json answer")
    s.set_defaults(func=cmd_search)

    g = sub.add_parser("get", parents=[common], help="one section or line range of a note")
    g.add_argument("ref", help="group:path, group:path#Heading or group:path:L10-40")
    g.set_defaults(func=cmd_get)

    r = sub.add_parser("related", parents=[common], help="notes linked to or sharing entities with a note")
    r.add_argument("ref")
    r.set_defaults(func=cmd_related)

    gr = sub.add_parser("groups", parents=[common], help="the groups, their folders and note counts")
    gr.set_defaults(func=cmd_groups)

    f = sub.add_parser("fields", parents=[common], help="frontmatter fields used in a group")
    f.add_argument("group")
    f.set_defaults(func=cmd_fields)

    d = sub.add_parser("doctor", parents=[common], help="check the setup and the index; each problem with its fix")
    d.add_argument("--secrets", action="store_true", help="also look for text that looks like credentials")
    d.set_defaults(func=cmd_doctor)

    i = sub.add_parser("index", parents=[common], help="update the index now (normally automatic)")
    i.add_argument("--rebuild", action="store_true", help="delete the index and build it again")
    i.set_defaults(func=cmd_index)

    c = sub.add_parser("config", parents=[common], help="check the config file and show what each group covers")
    c.add_argument("action", nargs="?", default="check", choices=["check"])
    c.set_defaults(func=cmd_config)

    e = sub.add_parser("eval", parents=[common], help="score search quality against a question set")
    e.add_argument("questions", help="questions JSON file")
    e.add_argument("--gate", action="store_true", help="exit 1 if the scores miss the release gates")
    e.add_argument("--baseline", help="scores of the last release, to compare with")
    e.add_argument("--write-baseline", help="write these scores to this file")
    e.add_argument("--verbose", action="store_true", help="list each question's outcome")
    e.set_defaults(func=cmd_eval)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 2
    try:
        code = args.func(args)
        return code or 0
    except (cfglib.ConfigError, UsageError, refs.RefError) as exc:
        problems = exc.problems if isinstance(exc, cfglib.ConfigError) else [str(exc)]
        if args.json:
            json.dump({"ok": False, "error": problems}, sys.stdout)
            sys.stdout.write("\n")
        else:
            for p in problems:
                sys.stderr.write("ns: %s\n" % p)
            if isinstance(exc, cfglib.ConfigError):
                sys.stderr.write("ns: check the file with: ns config check\n")
        return 2
    except (sqlite3.DatabaseError, NotReady) as exc:
        msg = str(exc) if isinstance(exc, NotReady) else "the index could not be read (%s); run: ns index --rebuild" % exc
        if args.json:
            json.dump({"ok": False, "error": [msg]}, sys.stdout)
            sys.stdout.write("\n")
        else:
            sys.stderr.write("ns: %s\n" % msg)
        return 1
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
