# Release Notes — v1.2.0-beta

**Pearl v1.2.0-beta** is a beta-hardening release: no new features, focused
on reliability, performance, packaging, and documentation ahead of a
public beta. Full detail in [CHANGELOG.md](CHANGELOG.md).

## Highlights

- **~7s faster startup** — removed a dead `import torch` left over from an
  abandoned local-model design.
- **~99% faster repository indexing** (19.4s → 0.2s on this project) —
  fixed `RepositoryIndex` to prune `.venv`/`node_modules`/etc. *during*
  the directory walk instead of after globbing everything.
- **Installable via `pip install -e .`** — a real `pyproject.toml` with a
  `pearl` console entry point, optional provider extras
  (`claude`/`gemini`/`dev`/`all`), and a populated `requirements.txt`.
- **VS Code extension is packageable** — `npm run package` produces a
  distributable `.vsix`; extension metadata (license, repository,
  version) now matches the release.
- **CI on every push/PR** — Python tests + lint + format check across
  3.10–3.12, and the VS Code extension's type check + test suite.
- **New documentation** — README rewritten end-to-end to match the
  current system, plus a new `docs/architecture.md` walking through every
  module and the request lifecycle.
- **Dead code removed** — several unused `Settings` fields left over from
  earlier design iterations; `pyflakes` reports zero unused
  imports/undefined names across `src/`.

## Upgrading

No breaking changes to the MCP protocol, tool APIs, or configuration
variable names. If you have a local `.venv`, re-run
`pip install -e ".[dev]"` to pick up the new packaging metadata.

## Known issues / limitations

- Cross-platform installs (native Windows, native macOS) were not
  exercised in this hardening pass — the backend is pure Python with no
  OS-specific paths, but only Linux/WSL was directly verified here.
- The VS Code extension has no linter/formatter configured yet (`tsc`
  type-checks it, but there's no ESLint/Prettier pass in CI).
- Token/cost usage per LLM call is not yet surfaced through Pearl's
  abstractions, though the underlying provider responses include it.
- `benchmarks/REPORT.md` reflects the prior (Phase 23) full 6-repository
  run; this release re-verified the indexing fix and full test suite but
  did not re-run the full multi-repository LLM benchmark end-to-end
  (would take significant wall-clock time against local Ollama for
  limited incremental signal beyond what's already measured directly).

## Recommendation

Suitable for a public beta: the test suite is green (403 Python tests,
164 TypeScript tests), packaging installs cleanly, CI enforces
lint/format/tests going forward, and the two most significant
performance issues found in the codebase (startup import cost, indexing
cost) are fixed with before/after numbers to back them. The known
limitations above are documentation/scope gaps, not correctness issues.
