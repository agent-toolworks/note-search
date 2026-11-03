"""The configuration: groups of folders, field roles and limits.

One JSON file, `~/.config/agent-toolworks/ns.json` (or $NS_CONFIG). Relative
paths in it are relative to the file. A team can keep groups in a shared file
and list it under "include".
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
from dataclasses import dataclass, field

DEFAULT_PATH = "~/.config/agent-toolworks/ns.json"
DEFAULT_CACHE = "~/.cache/agent-toolworks/ns"
EXTENSIONS = (".md", ".markdown", ".txt")

ROLE_NAMES = ("title", "aliases", "entities", "tags", "date", "status", "links")
# Used when a group maps no field to a role. Common names only; a group's own
# mapping replaces the default for that role.
DEFAULT_ROLES = {
    "title": ["title"],
    "aliases": ["aliases", "aka"],
    "entities": [],
    "tags": ["tags"],
    "date": ["updated", "date"],
    "links": ["related"],
}

_GROUP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,31}$")
_TOP_KEYS = {"include", "default_groups", "groups", "limits", "log", "cache_dir"}
_GROUP_KEYS = {"dirs", "exclude", "fields", "engine", "write", "description"}
_LIMIT_KEYS = {"max_file_mb", "answer_tokens", "get_tokens"}


class ConfigError(Exception):
    def __init__(self, problems):
        self.problems = problems if isinstance(problems, list) else [problems]
        super().__init__("; ".join(self.problems))


@dataclass
class Roles:
    title: list = field(default_factory=list)
    aliases: list = field(default_factory=list)
    entities: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    date: list = field(default_factory=list)
    links: list = field(default_factory=list)
    status: list = field(default_factory=list)
    stale: list = field(default_factory=list)


@dataclass
class Dir:
    path: str  # absolute folder
    prefix: str  # prepended to paths inside it, "" or "name/"


@dataclass
class Group:
    id: str
    dirs: list  # patterns as written, made absolute
    exclude: list
    roles: Roles
    engine: str = "fts5"
    description: str = ""
    _matchers: list = field(default_factory=list, repr=False)

    def resolve(self) -> tuple:
        """([Dir], [warning]) for the folders this group covers now."""
        out, warnings = [], []
        plain = []
        for pattern in self.dirs:
            if glob.has_magic(pattern):
                found, warns = _expand(pattern)
                out.extend(found)
                warnings.extend(warns)
            elif os.path.isdir(pattern):
                plain.append(pattern)
            else:
                warnings.append("group %s: folder %s does not exist" % (self.id, pattern))
        for p in plain:
            out.append(Dir(p, ""))
        return out, warnings

    def excluded(self, rel: str, is_dir: bool = False) -> bool:
        if not self._matchers:
            self._matchers = [_glob_regex(p) for p in self.exclude]
        candidate = rel + "/" if is_dir else rel
        base = rel.rsplit("/", 1)[-1]
        for pattern, (rx, anchored) in zip(self.exclude, self._matchers):
            target = candidate if anchored else (base + ("/" if is_dir else ""))
            if rx.fullmatch(target):
                return True
        return False

    def fingerprint(self) -> str:
        blob = json.dumps([self.dirs, self.exclude, self.roles.__dict__], sort_keys=True)
        return hashlib.sha1(blob.encode()).hexdigest()[:12]


@dataclass
class Limits:
    max_file_mb: float = 20
    answer_tokens: int = 600
    get_tokens: int = 1500


@dataclass
class Config:
    path: str
    groups: dict
    default_groups: list
    limits: Limits
    log: str
    cache_dir: str
    warnings: list = field(default_factory=list)

    def group_ids(self, wanted=None) -> list:
        if not wanted:
            return list(self.default_groups or self.groups)
        unknown = [g for g in wanted if g not in self.groups]
        if unknown:
            raise ConfigError("unknown group%s %s; groups are: %s" % (
                "s" if len(unknown) > 1 else "", ", ".join(unknown), ", ".join(self.groups)))
        return list(wanted)


def _glob_regex(pattern: str):
    """Compile a gitignore-style pattern. Returns (regex, anchored)."""
    anchored = "/" in pattern.rstrip("/")
    p = pattern.lstrip("/")
    out = []
    i = 0
    while i < len(p):
        c = p[i]
        if p.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = p.find("]", i)
            if j == -1:
                out.append(re.escape(c))
                i += 1
            else:
                out.append("[" + p[i + 1:j].replace("\\", "\\\\") + "]")
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    if not anchored:
        return re.compile("".join(out)), False
    return re.compile("".join(out)), True


def _short_names(values: list) -> dict:
    """Short names for the values a wildcard matched.

    Each value loses the longest start, cut on a - _ . or space, that it
    shares with at least a quarter of the values (and at least 3), so the
    project folder "-Users-zvi-git-home-agent-teller" reads "agent-teller"
    when many projects live under ~/git/home. If two values would read the
    same, both keep their full names.
    """
    distinct = sorted(set(values))
    need = max(3, (len(distinct) + 3) // 4)
    out = {}
    for v in distinct:
        best = ""
        for m in re.finditer(r"[-_.\s]+", v):
            cand = v[: m.end()]
            if len(cand) >= len(v):
                continue
            if sum(1 for o in distinct if o.startswith(cand) and len(o) > len(cand)) >= need:
                best = cand
        out[v] = v[len(best):].strip("-_. ") or v.strip("-_. ") or v
    names = list(out.values())
    for v in distinct:
        if names.count(out[v]) > 1:
            out[v] = v.strip("-_. ") or v
    return out


def _expand(pattern: str) -> tuple:
    """Folders matching a wildcard pattern, each with a short prefix.

    The prefix is made of the parts the wildcards matched, leaving out a part
    that is the same in every match, each shortened to the ending that tells
    it apart. So `~/.claude*/projects/*/memory` gives `agent-teller/` for the
    project `-Users-zvi-git-home-agent-teller` when only `~/.claude` exists.
    """
    comps = pattern.split(os.sep)
    wild = [i for i, c in enumerate(comps) if glob.has_magic(c)]
    matches = sorted(m for m in glob.glob(pattern) if os.path.isdir(m))
    if not matches:
        return [], ["pattern %s matches no folder" % pattern]
    split = [m.split(os.sep) for m in matches]
    keep = []
    for i in wild:
        vals = [s[i] for s in split if len(s) > i]
        if len(set(vals)) > 1:
            keep.append((i, _short_names(vals)))
    out = []
    seen = set()
    for m, s in zip(matches, split):
        prefix = "/".join(short[s[i]] for i, short in keep if len(s) > i)
        if prefix in seen:  # two matches shortened alike: fall back to the full names
            prefix = "/".join(s[i] for i, _ in keep if len(s) > i)
        seen.add(prefix)
        out.append(Dir(m, prefix + "/" if prefix else ""))
    return out, []


def _str_list(value, where, problems) -> list:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    problems.append("%s must be a string or a list of strings" % where)
    return []


def _roles(raw, gid, problems) -> Roles:
    raw = raw or {}
    if not isinstance(raw, dict):
        problems.append("group %s: fields must be an object" % gid)
        raw = {}
    roles = Roles(**{k: list(v) for k, v in DEFAULT_ROLES.items()})
    for key, value in raw.items():
        if key not in ROLE_NAMES:
            problems.append("group %s: fields.%s is not a role; roles are %s" % (gid, key, ", ".join(ROLE_NAMES)))
            continue
        if key == "status":
            if isinstance(value, dict):
                roles.status = _str_list(value.get("field", []), "group %s: fields.status.field" % gid, problems)
                roles.stale = [s.lower() for s in _str_list(value.get("stale", []), "group %s: fields.status.stale" % gid, problems)]
            else:
                roles.status = _str_list(value, "group %s: fields.status" % gid, problems)
            continue
        setattr(roles, key, _str_list(value, "group %s: fields.%s" % (gid, key), problems))
    return roles


def _abs(path: str, base: str) -> str:
    path = os.path.expanduser(os.path.expandvars(path))
    if not os.path.isabs(path):
        path = os.path.join(base, path)
    return os.path.normpath(path)


def _read_json(path: str, problems: list):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        problems.append("config file %s does not exist" % path)
    except json.JSONDecodeError as exc:
        problems.append("config file %s is not valid JSON: line %d column %d: %s" % (path, exc.lineno, exc.colno, exc.msg))
    except OSError as exc:
        problems.append("config file %s cannot be read: %s" % (path, exc.strerror))
    return None


def _groups_from(raw: dict, base: str, problems: list, groups: dict, source: str):
    rg = raw.get("groups", {})
    if not isinstance(rg, dict):
        problems.append("%s: groups must be an object" % source)
        return
    for gid, g in rg.items():
        if not _GROUP_ID.match(gid):
            problems.append("group id %r: use letters, digits and -, at most 32 characters" % gid)
            continue
        if gid in groups:
            problems.append("group %s is defined twice (again in %s)" % (gid, source))
            continue
        if not isinstance(g, dict):
            problems.append("group %s must be an object" % gid)
            continue
        for key in g:
            if key not in _GROUP_KEYS:
                problems.append("group %s: unknown key %r" % (gid, key))
        dirs = _str_list(g.get("dirs", []), "group %s: dirs" % gid, problems)
        if not dirs:
            problems.append("group %s: dirs is empty" % gid)
        exclude = _str_list(g.get("exclude", []), "group %s: exclude" % gid, problems)
        groups[gid] = Group(
            id=gid,
            dirs=[_abs(d, base) for d in dirs],
            exclude=exclude,
            roles=_roles(g.get("fields"), gid, problems),
            engine=g.get("engine", "fts5"),
            description=str(g.get("description", "")),
        )
        if groups[gid].engine != "fts5":
            problems.append("group %s: engine %r is not available; only fts5 is built in" % (gid, groups[gid].engine))


def load(path: str = None) -> Config:
    path = os.path.abspath(os.path.expanduser(path or os.environ.get("NS_CONFIG") or DEFAULT_PATH))
    problems: list = []
    raw = _read_json(path, problems)
    if raw is None:
        raise ConfigError(problems)
    if not isinstance(raw, dict):
        raise ConfigError("%s: the top level must be an object" % path)
    for key in raw:
        if key not in _TOP_KEYS:
            problems.append("unknown key %r at the top level" % key)
    base = os.path.dirname(path)
    groups: dict = {}
    _groups_from(raw, base, problems, groups, path)
    seen = {os.path.realpath(path)}
    for inc in _str_list(raw.get("include", []), "include", problems):
        inc_path = _abs(inc, base)
        if os.path.realpath(inc_path) in seen:
            problems.append("include %s is listed twice or includes itself" % inc_path)
            continue
        seen.add(os.path.realpath(inc_path))
        inc_raw = _read_json(inc_path, problems)
        if isinstance(inc_raw, dict):
            _groups_from(inc_raw, os.path.dirname(inc_path), problems, groups, inc_path)
    if not groups and not problems:
        problems.append("no groups are defined")
    default = _str_list(raw.get("default_groups", []), "default_groups", problems)
    for g in default:
        if g not in groups:
            problems.append("default_groups names %s, which is not a group" % g)
    lim_raw = raw.get("limits", {}) or {}
    limits = Limits()
    if not isinstance(lim_raw, dict):
        problems.append("limits must be an object")
        lim_raw = {}
    for key, value in lim_raw.items():
        if key not in _LIMIT_KEYS:
            problems.append("limits: unknown key %r" % key)
        elif not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            problems.append("limits.%s must be a positive number" % key)
        else:
            setattr(limits, key, value if key == "max_file_mb" else int(value))
    log = raw.get("log", "full")
    if log not in ("full", "counts", "off"):
        problems.append("log must be \"full\", \"counts\" or \"off\"")
    cache = os.environ.get("NS_CACHE") or (
        _abs(raw["cache_dir"], base) if isinstance(raw.get("cache_dir"), str) else os.path.expanduser(DEFAULT_CACHE))
    if problems:
        raise ConfigError(problems)
    return Config(path=path, groups=groups, default_groups=default, limits=limits, log=log, cache_dir=cache)
