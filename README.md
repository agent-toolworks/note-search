# note-search

Cross-project search over folders of Markdown notes, for AI agents. An agent in
any project finds what another project already learned, in one search, for a few
hundred tokens.

- **Groups.** You name groups of folders (`kb`, `memory`, ...). Results refer to a
  section by group and path: `kb:services/payment-gateway.md#Timeouts`.
- **Notes as they are.** Any Markdown, any frontmatter or none. You can declare
  which frontmatter fields matter, per group; nothing depends on it.
- **Cheap.** Answers are capped (about 600 tokens), one hit per note, best section
  only. No answer ever returns a whole large file.
- **No AI model, no network.** `python3` and SQLite's full-text search (FTS5) only.
- **Measured.** Every search is logged locally and shown in
  [toolworks-hub](https://github.com/agent-toolworks/toolworks-hub); a scored
  question set gates each release.

Built the way [code-search](https://github.com/agent-toolworks/code-search-fleet)
is: a command-line tool (`ns`), a Claude Code skill, an optional MCP server and a
hub source.

**Status:** design. Nothing is built yet.
[Design doc](https://claude.ai/artifact/G796JzMBsbj8Qc57Lhpf8y).

## License

Apache 2.0, see [LICENSE](LICENSE).
