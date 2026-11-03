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

`VERSION` and `CHANGELOG.md`: one version for the project; add a line under
Unreleased with every change.
