# Changelog

## Unreleased

## 0.4.0 - 2026-11-03

- toolworks-hub: note-search's tab is now its own page (`hub-source/page/`),
  in three tabs. **Search** searches the groups you tick, several wordings at
  once with `|`, unlogged. **Measures** shows what the widgets showed. **Notes**
  lists every indexed note in a table you can filter and sort.
- A note clicked in the results or the table opens on the right, rendered from
  its Markdown by `page/md.js`: frontmatter as properties, headings listed, the
  hit's section scrolled into view and the searched words marked; nested and
  task lists, tables, code, quotes and Obsidian callouts, `==highlights==`,
  `#tags`. Wiki links and relative links to other notes open in the same pane.
  The renderer builds every element itself: HTML in a note is shown as text,
  and a `javascript:` link is not a link.
- `run call <op> <json>` answers the page: `search`, `notes`, `note` (only a
  note the index holds) and `measures`. The widgets stay for `hub
  check-source`, and the session section is unchanged.

## 0.3.0 - 2026-11-03

- `ns add` (phase 3): creates a note in the one folder a group names as
  `"write"`, using the group's own frontmatter field names plus `source:` and
  `ns_created:`. New files only: no edits to notes ns did not create, no
  deletes, no commits; the note is indexed at once. The text needs an
  Observed or a Concluded heading, and text that looks like a credential is
  refused. The group is searched first; a note with the same title, or
  sharing an entity and half the title's words, stops the write (exit 3)
  unless `--new` or `--supersedes <ref>` is given. `--append <ref>` extends a
  note ns created, under a dated heading.
- A note superseded by an added one says `superseded by <ref>` on its hits
  (`superseded_by` in `--json`). The index schema is now 3; the first command
  after the update rebuilds the index.
- `ns groups`, `ns config check` and `ns doctor` show each group's write
  folder; doctor fails one that is hidden, excluded or outside the group.
- Fixed: a search answer could run a few tokens over its budget, because the
  "N more" line and the warnings after the notes were not counted. The test
  of 1,000 generated notes failed about one run in three for this reason.

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
