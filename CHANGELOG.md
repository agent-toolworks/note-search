# Changelog

## Unreleased

## 0.1.0 - 2026-11-02

- Phase 1, the read-only search: the `ns` command (`search`, `get`, `related`,
  `groups`, `fields`, `doctor`, `index`, `config check`, `eval`), with compact
  answers capped at a token budget and `--json` for programs.
- Groups of folders, wildcards with short stable names, excludes, includes, and
  frontmatter field roles per group; notes with unreadable frontmatter are indexed
  as plain text.
- A SQLite FTS5 index updated on every command from file sizes and times; one
  writer at a time, readers never blocked; an old or unreadable index is set
  aside and rebuilt. With SQLite 3.43 or newer it keeps no copy of the text, so
  it is about the size of the notes; snippets are read from the notes.
- Search: several wordings per call; strict, corrected-name and relaxed stages;
  merging by reciprocal rank; boosts for entities and titles, lower rank for
  stale notes; nearest names when nothing matches.
- Tests: reading files, splitting, keeping the index current, groups and config,
  scale (1,000 notes; 10,000 with NS_BENCH=1), and a 61-question set that gates
  every change. `scripts/bench` for the numbers.
- Repository created; design doc linked from the README.
