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
  A group can opt in to [search by meaning](#search-by-meaning-optional) with
  a small local model or an embeddings server.
- **Measured.** A scored question set gates every change, and every search is
  logged (words and references, never note text) so
  [toolworks-hub](https://github.com/agent-toolworks/toolworks-hub) can show
  whether searches found what agents needed.

Built the way [code-search](https://github.com/agent-toolworks/code-search-fleet)
is. [Design doc](https://claude.ai/artifact/G796JzMBsbj8Qc57Lhpf8y).

**Status:** phases 1 to 3 are built: the `ns` command and its index, the
question set, the Claude Code plugin and skill, the search log, the hub
source, and adding notes (`ns add`) to a group that allows it.

## Install

In Claude Code:

```
/plugin install note-search --marketplace agent-toolworks/note-search
```

The skill tells agents when to search and how. Then write the config below;
`ns doctor` says what is missing. Without Claude Code, clone the repo and run
`bin/ns`.

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

## The search log and the hub

Each `ns search` and `ns get` adds a line to
`~/.cache/agent-toolworks/ns/searches.jsonl` (mode 600): the words searched,
the groups, which stages ran, the references returned, tokens and time, and the
Claude Code session that ran it. Never note text. `"log": "counts"` in ns.json
keeps the counts but drops the words; `"log": "off"` writes nothing. The file is
rotated at 10 MB, and entries older than 90 days are ignored.

A search prints a query id at the end of its first line (`· q 3f9a1c2e`);
`ns get <ref> --q 3f9a1c2e` ties the get to it. Without it, a get is tied to the
latest search in the same session that returned that note.

With the plugin installed, toolworks-hub finds `hub-source/` and shows a
note-search tab with its own page (`hub-source/page/`), in three tabs:

- **Search**: a search box over the groups you tick (the default groups
  first; opt-in groups marked). Several wordings go in one search,
  separated by `|`. Searches made here are not logged: the log measures agents.
- **Measures**: searches per day by group; the share of searches whose hit
  was opened; how often a search was made again within 2 minutes in the same
  session (the first one failed; target under 20%); the rank of the opened
  hit; tokens per search and per get; searches with no strict match, with
  their words; groups not searched in 30 days.
- **Notes**: every indexed note in a table, filtered by text or group and
  sorted by any column, with STALE and superseded notes marked.

A note clicked in the search results or the table opens on the right,
rendered from its Markdown: frontmatter as a table of properties, a list of
its headings, and the hit's section scrolled into view with the searched
words marked. Wiki links (`[[note]]`) and relative links to other notes open
in the same pane. The page only ever reads notes the index holds, and builds
the page itself: HTML in a note is shown as text, never run.

Each Claude Code session's page in the hub gets a note-search section listing
its searches.

A wildcard folder is named in results by the part that varies, shortened: with
`~/.claude*/projects/*/memory`, the project folder `-Users-me-git-home-agent-teller`
reads `claude/agent-teller/` (the `claude` part appears only when there is more
than one `~/.claude*` folder).

## Commands

| Command | What it returns |
| --- | --- |
| `ns search <wording>... [--group g,...] [field=value] [--since YYYY-MM-DD] [--offset n]` | Ranked notes, one hit each, within the token budget. Up to 5 wordings in one call. |
| `ns get <ref> [--q id]` | One section (`group:path#Heading`, or a heading path) or line range (`group:path:L40-72`), capped; says where to continue |
| `ns related <ref>` | Notes linked to or from it, and notes sharing its entities |
| `ns groups` | Each group: folders, note count |
| `ns fields <group>` | Frontmatter fields found, with counts and examples, to help map roles |
| `ns doctor [--secrets]` | Setup, index health, unreadable frontmatter, skipped and partly indexed files; `--secrets` flags text that looks like credentials, without printing it |
| `ns index [--rebuild] [--download]` | Updates now, embedding every waiting section; normally not needed. `--download` lets the embeddings engine fetch its model |
| `ns config check` | Validates the config and shows each group's folders and field roles |
| `ns eval <questions.json> [--engine embeddings]` | Scores search quality; `--gate` exits 1 below the release gates |
| `ns add --group g --title T [--entities a,b] [--tags t] [--source S] [--new \| --supersedes ref] < text.md` | Creates a note in the group's write folder (below) |
| `ns add --append <ref> < text.md` | Adds text to a note `ns add` created, under a dated heading |

Every command takes `--json` for programs and `--config` for another config file
(or `$NS_CONFIG`). `$NS_CACHE` moves the index.

## Adding notes

Writing is off unless a group names the one folder ns may create notes in:

```json
{"groups": {"kb": {"dirs": ["~/kb"], "write": "~/kb/inbox",
                   "fields": {"title": "name", "entities": "service"}}}}
```

The folder must be inside one of the group's `dirs`, visible (not a dot
folder) and not excluded, so a new note is found at once; `ns doctor` checks
it. Then

```sh
ns add --group kb --title "Payment retry cap" --entities payments-api,retry_queue \
  --source INC-2026-002 < text.md
```

creates `kb:inbox/2026-11-04-payment-retry-cap.md`. The rules:

- **New files only.** ns never edits or deletes a note it did not create, and
  never commits. Committing the file is your choice.
- **The group's own field names.** The frontmatter uses the group's role
  mapping (`name:`, `service:`, `updated:` above), plus `source:` and
  `ns_created:` with the date, so the note looks like the rest of the group.
- **Observed apart from concluded.** The text needs an `Observed` or a
  `Concluded` heading (or both): what was seen, and what was worked out from
  it, never blended in one paragraph.
- **No credentials.** Text that looks like a key, a token or a password
  (`ns doctor --secrets`' patterns) is refused; say where the secret is kept
  instead.
- **Duplicates.** The group is searched first. A note with the same title, or
  one sharing an entity and at least half of the title's words, stops the
  write: ns lists it and exits 3. Pass `--new` if yours is a different note,
  or `--supersedes <ref>` if it replaces that one. The old note is left as it
  is; its hits then say `superseded by <new ref>`, and `ns related` links the
  two.
- **Appending.** `ns add --append <ref>` adds text under `## Added <date>`, but
  only to a note in the write folder that carries `ns_created:`.

Exit status: 0 written, 2 refused (the reason on stderr), 3 close matches and
nothing written.

## How a search works

Each wording is searched strictly: every word, ranked by BM25, with title, aliases,
entities and headings weighted above body text. A name that is not in the index
(`payment-gatway`) is replaced by the nearest one that is. If fewer than 3 notes
match, the search relaxes to sections holding at least half of the words, and
labels those hits as partial. The wordings' rankings are merged; an exact match on
a note's entities or title counts more, a stale note less. The design doc has the
details.

## Search by meaning (optional)

Words miss a note written in other words: "how do shoppers get billed?" finds
nothing in a note about "card payments". A group can also be searched by
meaning, with embeddings from a small model:

```json
{
  "embeddings": {"python": "~/.local/share/ns-embed/bin/python"},
  "groups": {"kb": {"dirs": ["~/work/kb"], "engine": "embeddings"}}
}
```

Each wording then also finds the sections closest to it in meaning. Those hits
are merged with the words' hits, never replace them, never count as a strict
match, and are labelled `close in meaning, not in words`. It is off by default,
and the default install never loads a model or a package.

Two providers:

- **`fastembed`** (the default): a local model, `BAAI/bge-small-en-v1.5`
  (67 MB, English), run by the [fastembed](https://github.com/qdrant/fastembed)
  package. Install it in a Python of its own and name that Python, so ns itself
  keeps to the standard library:

  ```sh
  python3 -m venv ~/.local/share/ns-embed
  ~/.local/share/ns-embed/bin/pip install fastembed
  ns index --download      # fetches the model once; nothing else downloads
  ```

  `"model"` picks another of fastembed's models.
- **`http`**: any server that answers OpenAI's `POST /embeddings`, such as
  Ollama, LM Studio or llama.cpp's server. Nothing to install in Python:

  ```json
  "embeddings": {"provider": "http", "url": "http://localhost:11434/v1", "model": "nomic-embed-text"}
  ```

  A hosted service works the same way. Its URL is in ns.json, and so is
  `"api_key_env"`, the environment variable holding its key. Section text then
  leaves the machine, so name one only if that is allowed.

Vectors are kept in `vectors.db` beside the index, keyed by model and text, so
a rebuilt index or a moved note is not embedded again. A search or other
command spends at most 2 seconds embedding new sections and says how many are
left; `ns index` embeds them all. `ns doctor` checks the engine and how much is
embedded. If the engine cannot run, searches use words only, with one warning
naming the fix. `ns eval --engine embeddings` scores a question set with the
engine on.

What it costs and what it finds, on the M2 below with the default model:

- **Embedding**, once per section: about 35 ms, so 1,000 notes of 5 sections
  each take about 3 minutes the first time. After that only changed sections
  are embedded.
- **A search** from the command line takes about 0.4 s, mostly starting the
  model. In one process (the hub, `ns eval`) it takes 26 ms at the median
  over 8,000 sections with numpy, 220 ms without.
- **Found**: on the reworded set (`fixtures/eval/reworded.json`, 24
  questions asked in other words than the notes use), the right note is in
  the top 5 for all 24, against 8 of 24 with words only, and first for 75%,
  against 25%. On the main set it stays in the top 5 for all, and is first
  for 98% instead of 100%. The one question it moves down is "which
  services does identity-team own?", where it puts the team's ownership map
  first.

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
top 5 for all of them and first for all of them. That set was written by the same hand as
the search, so it is easier than real use. The reworded set
(`fixtures/eval/reworded.json`) asks 24 questions in other words than the notes
use. With words only, the right note is in the top 5 for a third of them; that
is the gap search by meaning closes (above).

Keep a second set over your own notes outside the repo: a folder with a
`questions.json` and an `ns.json` naming your real folders by absolute path.
`ns eval` copies only that folder and reads the notes where they are, read-only,
with an index of its own. On 36 draft questions over the author's 76 Claude
Code memory notes: top 5 for all, first for 86%.

## Tests

```sh
python3 -m unittest discover -s tests -t .      # all suites, about 16 s
NS_BENCH=1 python3 -m unittest tests.test_scale  # also 10,000 notes against the design's limits
scripts/bench                                    # the numbers above
bin/ns eval fixtures/eval/questions.json --verbose --baseline fixtures/eval-baseline.json
bin/ns eval fixtures/eval/reworded.json --verbose --baseline fixtures/eval-reworded-baseline.json
# with the embeddings engine (needs fastembed and its model, see above):
<python with fastembed> bin/ns eval fixtures/eval/reworded.json --engine embeddings \
  --baseline fixtures/eval-reworded-embeddings-baseline.json
```

## License

Apache 2.0, see [LICENSE](LICENSE).
