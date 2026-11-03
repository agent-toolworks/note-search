# note-search

Cross-project search over folders of Markdown notes, for AI agents. An agent in
any project finds what another project already learned, in one search, for a few
hundred tokens.

- **Groups.** You name groups of folders (`kb`, `memory`, ...). Results refer to a
  section by group and path: `kb:services/payment-gateway.md#Timeouts`.
- **Notes as they are.** Any Markdown, any frontmatter or none. You can declare
  which frontmatter fields matter, per group; nothing depends on it.
- **Cheap.** Answers are capped (600 tokens by default), one hit per note, best
  section only. No answer ever returns a whole large file.
- **No AI model, no network.** `python3` and SQLite's full-text search (FTS5) only.
- **Measured.** A scored question set gates every change; search statistics in
  [toolworks-hub](https://github.com/agent-toolworks/toolworks-hub) come in phase 2.

Built the way [code-search](https://github.com/agent-toolworks/code-search-fleet)
is. [Design doc](https://claude.ai/artifact/G796JzMBsbj8Qc57Lhpf8y).

**Status:** phase 1, the read-only search, is built: the `ns` command, its index,
five test suites and the question set. The Claude Code skill and plugin, the hub
source and the search log are phase 2.

## Try it

Needs `python3` 3.8 or newer whose SQLite has FTS5 with trigrams (3.34 or newer);
the macOS system `python3` has it. Nothing to install.

Write `~/.config/agent-toolworks/ns.json`:

```json
{
  "default_groups": ["memory"],
  "groups": {
    "memory": { "dirs": ["~/.claude*/projects/*/memory"] },
    "kb": {
      "dirs": ["~/work/kb"],
      "exclude": ["**/archive/**"],
      "fields": {
        "title": "name",
        "entities": ["service", "tables"],
        "date": "updated",
        "status": { "field": "status", "stale": ["obsolete", "superseded"] }
      }
    }
  }
}
```

Then:

```sh
bin/ns config check                       # what each group covers
bin/ns doctor                             # setup and index, each problem with its fix
bin/ns search "checkout slow" "payment-gateway timeout" "504 /pay"
bin/ns get kb:services/payment-gateway.md#Timeouts
```

The index lives in `~/.cache/agent-toolworks/ns/` and updates itself on every
command; it can be deleted at any time.

A wildcard folder is named in results by the part that varies, shortened: with
`~/.claude*/projects/*/memory`, the project folder `-Users-me-git-home-agent-teller`
reads `claude/agent-teller/` (the `claude` part appears only when there is more
than one `~/.claude*` folder).

## Commands

| Command | What it returns |
| --- | --- |
| `ns search <wording>... [--group g,...] [field=value] [--since YYYY-MM-DD] [--offset n]` | Ranked notes, one hit each, within the token budget. Up to 5 wordings in one call. |
| `ns get <ref>` | One section (`group:path#Heading`, or a heading path) or line range (`group:path:L40-72`), capped; says where to continue |
| `ns related <ref>` | Notes linked to or from it, and notes sharing its entities |
| `ns groups` | Each group: folders, note count |
| `ns fields <group>` | Frontmatter fields found, with counts and examples, to help map roles |
| `ns doctor [--secrets]` | Setup, index health, unreadable frontmatter, skipped and partly indexed files; `--secrets` flags text that looks like credentials, without printing it |
| `ns index [--rebuild]` | Updates now; normally not needed |
| `ns config check` | Validates the config and shows each group's folders and field roles |
| `ns eval <questions.json>` | Scores search quality; `--gate` exits 1 below the release gates |

Every command takes `--json` for programs and `--config` for another config file
(or `$NS_CONFIG`). `$NS_CACHE` moves the index.

## How a search works

Each wording is searched strictly: every word, ranked by BM25, with title, aliases,
entities and headings weighted above body text. A name that is not in the index
(`payment-gatway`) is replaced by the nearest one that is. If fewer than 3 notes
match, the search relaxes to sections holding at least half of the words, and
labels those hits as partial. The wordings' rankings are merged; an exact match on
a note's entities or title counts more, a stale note less. The design doc has the
details.

## Measured

On an M2 MacBook Air with 16 GB, with generated engineering-style notes
(`scripts/bench 1000 10000`):

| Notes | Size | Index | Full build | Update, nothing changed | Update, 10 changed | Search, median / slowest |
| --- | --- | --- | --- | --- | --- | --- |
| 1,000 | 9 MB | 11 MB | 3.2 s | 12 ms | 47 ms | 7 / 10 ms |
| 10,000 | 102 MB | 104 MB | 36 s | 106 ms | 159 ms | 20 / 45 ms |

A harder test, 150 searches of up to three wordings of up to five unrelated words
each, takes 23 ms at the median at 10,000 notes, about 100 ms at the 95th
percentile and up to 140 ms at the slowest.

With SQLite 3.43 or newer the index keeps no copy of the notes' text (snippets are
read from the notes), so it is about the size of the notes; with older SQLite it is
about three times that.

Search quality on the committed question set (`fixtures/eval`: 61 questions over
made-up services, tables, incidents and agent memory): the right note is in the
top 5 for all of them and first for 98%. That set was written by the same hand as
the search, so it is easier than real use; phase 2 adds a private set over real
notes.

## Tests

```sh
python3 -m unittest discover -s tests -t .      # all suites, about 15 s
NS_BENCH=1 python3 -m unittest tests.test_scale  # also 10,000 notes against the design's limits
scripts/bench                                    # the numbers above
bin/ns eval fixtures/eval/questions.json --verbose --baseline fixtures/eval-baseline.json
```

## License

Apache 2.0, see [LICENSE](LICENSE).
