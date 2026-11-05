"""The embeddings engine: search by meaning, optional, per group.

Off unless a group says "engine": "embeddings" in ns.json. It adds a stage
to every search of that group, "semantic": the sections whose meaning is
closest to each wording. Its hits are merged with the words' hits, never
replace them, and never count as a strict match.

No model ships with ns, and the default install never loads this module's
providers. Two are built in, named in an "embeddings" block in ns.json:

- "fastembed" (the default): a small local model (BAAI/bge-small-en-v1.5,
  67 MB) run by the fastembed package, in the Python named by "python"
  (default: the one running ns), so ns itself keeps to the standard library.
  The model is downloaded once, by `ns index --download`; searches and
  updates never download.
- "http": a server that answers OpenAI's POST /embeddings: Ollama, LM
  Studio, llama.cpp's server, or a hosted service. Its "url" is named
  explicitly, which is the only way an engine reaches a network service.

Vectors live in vectors.db beside the index, keyed by a hash of the model
and the text, so a rebuilt index or a moved note is not embedded again.
For each section of an embedded group the index keeps that key; a section
whose vector is missing waits in a queue, with its text, until it is
embedded. A normal command spends at most EMBED_SECONDS on the queue; `ns
index` empties it.
"""
from __future__ import annotations

import array
import atexit
import base64
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_MODELS_DIR = "~/.cache/agent-toolworks/ns/models"
PROVIDERS = ("fastembed", "http")
SETTING_KEYS = {"provider", "model", "python", "url", "api_key_env", "query_prefix", "doc_prefix",
                "models_dir", "timeout"}
# Prefixes some models were trained with; a setting in ns.json wins. The bge
# instruction moved the reworded set's first place from 71% to 79%, nothing else.
_BGE = ("Represent this sentence for searching relevant passages: ", "")
KNOWN_PREFIXES = {
    "BAAI/bge-small-en-v1.5": _BGE,
    "BAAI/bge-base-en-v1.5": _BGE,
    "BAAI/bge-large-en-v1.5": _BGE,
    "nomic-embed-text": ("search_query: ", "search_document: "),
    "nomic-ai/nomic-embed-text-v1.5": ("search_query: ", "search_document: "),
}

EMBED_SECONDS = 2.0  # spent on the queue by a command other than `ns index`
BATCH = 32
TEXT_CHARS = 2000  # a section's text beyond this is not embedded (small models read about 512 tokens)
TOP_K = 10  # sections per wording
SPREAD = 0.12  # keep sections within this similarity of a wording's best

VECTORS = "vectors.db"


class Unavailable(Exception):
    """The engine cannot run now; `fix` says what would make it run."""

    def __init__(self, why: str, fix: str = ""):
        super().__init__(why)
        self.fix = fix


@dataclass
class Settings:
    provider: str = "fastembed"
    model: str = DEFAULT_MODEL
    python: str = ""  # fastembed: the Python that has it; "" = the one running ns
    url: str = ""  # http: the base URL; /embeddings is added
    api_key_env: str = ""  # http: the environment variable holding a key, if the server needs one
    query_prefix: str = None
    doc_prefix: str = None
    models_dir: str = DEFAULT_MODELS_DIR
    timeout: float = 60.0

    def __post_init__(self):
        q, d = KNOWN_PREFIXES.get(self.model, ("", ""))
        if self.query_prefix is None:
            self.query_prefix = q
        if self.doc_prefix is None:
            self.doc_prefix = d

    @property
    def model_id(self) -> str:
        return "%s:%s" % (self.provider, self.model)

    def fingerprint(self) -> str:
        """What a section's vector depends on besides its text."""
        return json.dumps([self.model_id, self.doc_prefix])

    def describe(self) -> str:
        where = self.url if self.provider == "http" else (self.python or sys.executable)
        return "%s %s (%s)" % (self.provider, self.model, where)


def parse_settings(raw, base: str, problems: list) -> Settings:
    if raw is None:
        return Settings()
    if not isinstance(raw, dict):
        problems.append("embeddings must be an object")
        return Settings()
    for key in raw:
        if key not in SETTING_KEYS:
            problems.append("embeddings: unknown key %r" % key)
    s = Settings(**{k: v for k, v in raw.items() if k in SETTING_KEYS and k not in ("timeout",)})
    if s.provider not in PROVIDERS:
        problems.append("embeddings.provider must be one of %s" % ", ".join(PROVIDERS))
    for key in ("model", "python", "url", "api_key_env", "models_dir"):
        if not isinstance(getattr(s, key), str):
            problems.append("embeddings.%s must be a string" % key)
    for key in ("query_prefix", "doc_prefix"):
        if not isinstance(getattr(s, key), str):
            problems.append("embeddings.%s must be a string" % key)
    if s.provider == "http" and not s.url:
        problems.append("embeddings: provider http needs a url, such as http://localhost:11434/v1 for Ollama")
    if "timeout" in raw:
        t = raw["timeout"]
        if not isinstance(t, (int, float)) or isinstance(t, bool) or t <= 0:
            problems.append("embeddings.timeout must be a positive number of seconds")
        else:
            s.timeout = float(t)
    if s.python:
        s.python = _abs(s.python, base)
    s.models_dir = os.path.expanduser(os.path.expandvars(s.models_dir))
    return s


def _abs(path: str, base: str) -> str:
    path = os.path.expanduser(os.path.expandvars(path))
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(base, path))


# -- vectors ---------------------------------------------------------------
def normalize(vec) -> array.array:
    a = array.array("f", vec)
    n = math.sqrt(sum(x * x for x in a)) or 1.0
    return array.array("f", (x / n for x in a))


def pack(vec) -> bytes:
    return normalize(vec).tobytes()


def unpack(blob: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(blob)
    return a


def has_text(text: str) -> bool:
    """A section with more than its heading: one with only a heading would match anything a little."""
    return any(line.strip() and not line.lstrip().startswith("#") for line in text.splitlines())


def doc_text(title: str, hpath: str, text: str) -> str:
    head = title if not hpath or hpath == title else "%s › %s" % (title, hpath)
    return (head + "\n" + text.strip())[:TEXT_CHARS]


def key_of(settings: Settings, text: str) -> str:
    return hashlib.sha1((settings.fingerprint() + "\n" + text).encode("utf-8")).hexdigest()


# -- providers -------------------------------------------------------------
class Http:
    def __init__(self, s: Settings):
        self.s = s

    def embed(self, texts: list, query: bool) -> list:
        prefix = self.s.query_prefix if query else self.s.doc_prefix
        body = json.dumps({"model": self.s.model, "input": [prefix + t for t in texts]}).encode()
        headers = {"Content-Type": "application/json"}
        if self.s.api_key_env:
            key = os.environ.get(self.s.api_key_env)
            if not key:
                raise Unavailable("%s is not set" % self.s.api_key_env,
                                  "set %s, which embeddings.api_key_env names" % self.s.api_key_env)
            headers["Authorization"] = "Bearer " + key
        url = self.s.url.rstrip("/") + "/embeddings"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.s.timeout) as r:
                data = json.load(r)
        except urllib.error.HTTPError as exc:
            detail = exc.read(300).decode("utf-8", "replace").strip()
            raise Unavailable("%s answered %d: %s" % (url, exc.code, detail),
                              "check embeddings.model (%s) and url in ns.json" % self.s.model)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            raise Unavailable("cannot reach %s: %s" % (url, reason),
                              "start the server (for Ollama: ollama serve, then ollama pull %s)" % self.s.model)
        try:
            items = sorted(data["data"], key=lambda d: d.get("index", 0))
            out = [it["embedding"] for it in items]
        except (KeyError, TypeError):
            raise Unavailable("%s did not answer with embeddings" % url, "check that it serves OpenAI's /embeddings")
        if len(out) != len(texts):
            raise Unavailable("%s returned %d vectors for %d texts" % (url, len(out), len(texts)))
        return out

    def close(self):
        pass


_LOADED = {}  # models loaded in this process, so the hub or ns eval loads each once


class Fastembed:
    """fastembed in this Python, or in a worker process of the Python named in the settings."""

    def __init__(self, s: Settings, download: bool = False):
        self.s = s
        self.download = download
        self.model = None
        self.proc = None
        self.inline = not s.python or os.path.realpath(s.python) == os.path.realpath(sys.executable)

    def _fix_install(self) -> str:
        if self.inline:
            return ("install fastembed in a Python of its own and name it in ns.json: "
                    "python3 -m venv ~/.local/share/ns-embed && ~/.local/share/ns-embed/bin/pip install fastembed, "
                    "then \"embeddings\": {\"python\": \"~/.local/share/ns-embed/bin/python\"}")
        return "%s -m pip install fastembed" % self.s.python

    def _load(self):
        if self.model is not None:
            return
        key = (self.s.model, self.s.models_dir)
        if key not in _LOADED:
            try:
                from fastembed import TextEmbedding
            except ImportError:
                raise Unavailable("fastembed is not installed for %s" % sys.executable, self._fix_install())
            _LOADED[key] = load_model(TextEmbedding, self.s, self.download)
        self.model = _LOADED[key]

    def _worker(self):
        if self.proc is not None:
            return
        if not os.path.exists(self.s.python):
            raise Unavailable("embeddings.python %s does not exist" % self.s.python, self._fix_install())
        env = dict(os.environ)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        try:
            self.proc = subprocess.Popen(
                [self.s.python, "-m", "notesearch.embed", "worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=None if self.download else subprocess.DEVNULL, env=env, cwd=root, text=True, bufsize=1)
        except OSError as exc:
            raise Unavailable("cannot run %s: %s" % (self.s.python, exc.strerror), self._fix_install())
        atexit.register(self.close)

    def embed(self, texts: list, query: bool) -> list:
        prefix = self.s.query_prefix if query else self.s.doc_prefix
        texts = [prefix + t for t in texts]
        if self.inline:
            self._load()
            return run_model(self.model, texts, query)
        self._worker()
        req = {"model": self.s.model, "models_dir": self.s.models_dir, "download": self.download,
               "query": query, "texts": texts}
        try:
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
        except OSError:
            line = ""
        if not line:
            self.close()
            raise Unavailable("the fastembed worker in %s stopped" % self.s.python, self._fix_install())
        ans = json.loads(line)
        if "error" in ans:
            raise Unavailable(ans["error"], ans.get("fix", ""))
        return [list(unpack(base64.b64decode(v))) for v in ans["vectors"]]

    def close(self):
        if self.proc is not None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
            finally:
                self.proc.stdout.close()
            self.proc = None


def load_model(TextEmbedding, s: Settings, download: bool):
    try:
        return TextEmbedding(s.model, cache_dir=s.models_dir, local_files_only=not download)
    except Exception as exc:  # fastembed raises several kinds for a model it cannot find
        if download:
            raise Unavailable("cannot load %s: %s" % (s.model, str(exc).splitlines()[0][:200]),
                              "check the model name against fastembed's list")
        raise Unavailable("model %s is not in %s" % (s.model, s.models_dir),
                          "ns index --download (fetches it once, about 70 MB for the default model)")


def run_model(model, texts: list, query: bool) -> list:
    fn = model.query_embed if query else model.passage_embed
    return [list(map(float, v)) for v in fn(texts)]


def worker():
    """`python -m notesearch.embed worker`: embed requests read from stdin, one JSON line each."""
    models = {}
    for line in sys.stdin:
        req = json.loads(line)
        try:
            try:
                from fastembed import TextEmbedding
            except ImportError:
                raise Unavailable("fastembed is not installed for %s" % sys.executable,
                                  "%s -m pip install fastembed" % sys.executable)
            s = Settings(model=req["model"], models_dir=req["models_dir"])
            if s.model not in models:
                models[s.model] = load_model(TextEmbedding, s, req.get("download", False))
            vecs = run_model(models[s.model], req["texts"], req["query"])
            out = {"vectors": [base64.b64encode(array.array("f", v).tobytes()).decode() for v in vecs]}
        except Unavailable as exc:
            out = {"error": str(exc), "fix": exc.fix}
        except Exception as exc:
            out = {"error": "%s: %s" % (type(exc).__name__, exc)}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


def provider(s: Settings, download: bool = False):
    return Http(s) if s.provider == "http" else Fastembed(s, download)


# -- the store and the queue -----------------------------------------------
def attach(conn, cfg, create: bool) -> bool:
    """Attach vectors.db to an index connection as `vdb`. False if there is none yet and `create` is off."""
    if any(row[1] == "vdb" for row in conn.execute("PRAGMA database_list")):
        return True
    path = cfg.vectors or os.path.join(cfg.cache_dir, VECTORS)
    if not create and not os.path.exists(path):
        return False
    old = os.umask(0o077)
    try:
        conn.execute("ATTACH DATABASE ? AS vdb", (path,))
    finally:
        os.umask(old)
    if create:
        conn.execute("PRAGMA vdb.journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS vdb.vec(key TEXT PRIMARY KEY, v BLOB)")
        conn.commit()
    return True


@dataclass
class Fill:
    embedded: int = 0
    waiting: int = 0
    error: Unavailable = None


def fill(conn, cfg, seconds: float = EMBED_SECONDS, download: bool = False) -> Fill:
    """Embed sections waiting in the queue, for at most `seconds` (None: all of them)."""
    out = Fill()
    if not conn.execute("SELECT 1 FROM embed_queue LIMIT 1").fetchone():
        return out
    attach(conn, cfg, create=True)
    with conn:
        conn.execute("DELETE FROM embed_queue WHERE key IN (SELECT key FROM vdb.vec)")
    start = time.time()
    p = None
    try:
        while seconds is None or time.time() - start < seconds:
            rows = conn.execute("SELECT key, text FROM embed_queue LIMIT ?", (BATCH,)).fetchall()
            if not rows:
                break
            if p is None:
                p = provider(cfg.embeddings, download)
            try:
                vecs = p.embed([r[1] for r in rows], query=False)
            except Unavailable as exc:
                out.error = exc
                break
            with conn:
                conn.executemany("INSERT OR REPLACE INTO vdb.vec(key, v) VALUES(?,?)",
                                 [(r[0], pack(v)) for r, v in zip(rows, vecs)])
                conn.executemany("DELETE FROM embed_queue WHERE key=?", [(r[0],) for r in rows])
            out.embedded += len(rows)
    finally:
        if p is not None:
            p.close()
    out.waiting = conn.execute("SELECT count(*) FROM embed_queue").fetchone()[0]
    return out


def prune(conn) -> int:
    """Drop vectors no section uses any more (after a model change, or notes deleted)."""
    with conn:
        cur = conn.execute("DELETE FROM vdb.vec WHERE key NOT IN (SELECT key FROM sec_embed)")
    return cur.rowcount


# -- searching -------------------------------------------------------------
def _numpy():
    """numpy, when this Python has it: it scores 10,000 notes in milliseconds. Imported
    only by a search of an embedded group, so other commands never pay for it."""
    if os.environ.get("NS_NO_NUMPY"):
        return None
    try:
        import numpy
        return numpy
    except ImportError:
        return None


def rank(rows: list, queries: list, k: int = TOP_K, spread: float = SPREAD) -> list:
    """rows: (section_id, file_id, vector blob). For each query vector, the
    [(section_id, file_id, similarity)] closest to it, best first."""
    if not rows:
        return [[] for _ in queries]
    qs = [normalize(q) for q in queries]
    _np = _numpy()
    if _np is not None:
        m = _np.frombuffer(b"".join(r[2] for r in rows), dtype=_np.float32).reshape(len(rows), -1)
        q = _np.array([list(x) for x in qs], dtype=_np.float32)
        if m.shape[1] != q.shape[1]:
            raise Unavailable("the stored vectors and the model's differ in size", "ns index --rebuild")
        scores = (m @ q.T).T.tolist()
    else:
        import operator
        vecs = [unpack(r[2]) for r in rows]
        if len(vecs[0]) != len(qs[0]):
            raise Unavailable("the stored vectors and the model's differ in size", "ns index --rebuild")
        scores = [[sum(map(operator.mul, q, v)) for v in vecs] for q in qs]
    out = []
    for sc in scores:
        order = sorted(range(len(sc)), key=lambda i: -sc[i])[:k]
        best = sc[order[0]] if order else 0.0
        out.append([(rows[i][0], rows[i][1], sc[i]) for i in order if sc[i] >= best - spread])
    return out


def search(conn, cfg, wordings: list, scope: tuple) -> list:
    """The sections closest in meaning to each wording; scope as Searcher._scope gives it."""
    if not attach(conn, cfg, create=False):
        return [[] for _ in wordings]
    where, params = "", []
    if scope[0]:
        where = " WHERE se.file_id IN (%s)" % scope[0]
        params = scope[1]
    rows = conn.execute("SELECT se.section_id, se.file_id, v.v FROM sec_embed se JOIN vdb.vec v ON v.key = se.key"
                        + where, params).fetchall()
    if not rows:
        return [[] for _ in wordings]
    p = provider(cfg.embeddings)
    try:
        queries = p.embed(wordings, query=True)
    finally:
        p.close()
    return rank(rows, queries)


if __name__ == "__main__":
    if sys.argv[1:] == ["worker"]:
        worker()
    else:
        sys.stderr.write("usage: python -m notesearch.embed worker\n")
        sys.exit(2)
