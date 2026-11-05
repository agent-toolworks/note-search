# note-search

Cross-project search over folders of Markdown notes for AI agents. The design is in
the design doc linked from `README.md`; read it before changing behaviour.

Non-negotiables:

- Python standard library and SQLite only. No AI model, no network call, no
  third-party package in the default install.
- Notes are read-only. Writing (phase 3) creates new files only, in a folder a
  group names; existing notes are never edited, nothing is deleted or committed.
- Every answer stays within its token budget. A search that did not run is an
  error, never an empty result.
- The search log holds queries and references, never note text.
- The search-quality question set gates every change: no kind may drop more
  than 5 points from the last release.

Layout: `bin/ns` runs `notesearch/cli.py`; `notesearch/` holds the config,
frontmatter reader, splitter, index, search, `ns get`, rendering, doctor and eval,
`embed.py` (the optional search by meaning, off unless a group asks for it),
plus `searchlog.py` (the search log), `stats.py` (measures read from it) and
`add.py` (`ns add`, the only code that writes into a group's folders).
`hub-source/` is the toolworks-hub source (contract 4); `skills/note-search/` and
`.claude-plugin/` make the Claude Code plugin. The skill's description is loaded
in every session: keep it near 100 tokens. Tests in `tests/` (unittest, standard
library only):

    python3 -m unittest discover -s tests -t .
    NS_BENCH=1 python3 -m unittest tests.test_scale   # 10,000 notes, design limits
    bin/ns eval fixtures/eval/questions.json --verbose --baseline fixtures/eval-baseline.json
    bin/ns eval fixtures/eval/reworded.json --verbose --baseline fixtures/eval-reworded-baseline.json

A change to ranking reruns the eval; refresh `fixtures/eval-baseline.json` with
`--write-baseline` only when the change is meant to move the scores, and say so
in the pull request. The reworded set (questions in other words than the notes)
is held to its own baseline, not to the release floors. The optional
embeddings engine (`embed.py`) needs fastembed or an embeddings server, so the
tests use stand-ins; a change to it, or to how its hits are merged, reruns both
sets with `--engine embeddings` in a Python that has fastembed, against
`fixtures/eval-embeddings-baseline.json` and
`fixtures/eval-reworded-embeddings-baseline.json`.

`VERSION` and `CHANGELOG.md`: one version for the project; add a line under
Unreleased with every change. A release sets the same version in `VERSION`,
`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` and
`hub-source/source.json` (a test checks they agree).

A change to the log's fields changes `searchlog.VERSION` and the hub source in
the same change; `tests/test_hub.py` runs the hub's `check-source` when a
toolworks-hub clone is found (`TOOLWORKS_HUB_REPO`, else `~/git/home/toolworks-hub`).
