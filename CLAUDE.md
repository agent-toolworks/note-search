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
frontmatter reader, splitter, index, search, `ns get`, rendering, doctor and eval.
Tests in `tests/` (unittest, standard library only):

    python3 -m unittest discover -s tests -t .
    NS_BENCH=1 python3 -m unittest tests.test_scale   # 10,000 notes, design limits
    bin/ns eval fixtures/eval/questions.json --verbose --baseline fixtures/eval-baseline.json

A change to ranking reruns the eval; refresh `fixtures/eval-baseline.json` with
`--write-baseline` only when the change is meant to move the scores, and say so
in the pull request.

`VERSION` and `CHANGELOG.md`: one version for the project; add a line under
Unreleased with every change.
