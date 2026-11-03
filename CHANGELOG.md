# Changelog

## Unreleased

- Ranking: a note named after what was asked for (its title, an alias or its
  file name) now gets 1.6x, up from 1.3x, so it outranks a note that only lists
  the thing as an entity (1.5x). A superseded decision record ranked above the
  one that replaced it, and an incident above the page about the table it
  named. Measured on four question sets (fixtures/eval, the Copperleaf Coffee
  docs, the same with 5,000 distractor notes, the real-notes set): three
  questions moved to first place, none moved down; 2.0x and above cost
  real-notes questions.

## 0.2.0 - 2026-11-03

- Claude Code plugin: the `note-search` skill tells agents when to search, to
  send several wordings at once and to open sections with `ns get`. Install it
  from this repository (`.claude-plugin/marketplace.json`).
- The search log, `searches.jsonl` beside the index: one line per search and
  per `ns get`, with the Claude Code session that ran it
  (`CLAUDE_CODE_SESSION_ID`). Queries and references only, never note text;
  `"log": "counts"` drops the words, `"off"` writes nothing. Mode 600, rotated
  at 10 MB, read back 90 days.
- A search prints its query id (`· q 3f9a1c2e`); `ns get --q <id>` ties the
  get to it. `--json` answers carry it as `q`.
- `hub-source/` for toolworks-hub (contract 4): searches per day by group, the
  share of searches whose hit was opened, searches made again within 2
  minutes, the rank of the opened hit, tokens, searches with no strict match
  and their words, groups not searched in 30 days, a search box, and a section
  on each Claude Code session's page.

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
