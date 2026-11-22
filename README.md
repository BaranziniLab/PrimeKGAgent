# PrimeKGAgent

Download the PrimeKG precision medicine knowledge graph once from Harvard Dataverse and query it on this computer: node lookup, neighbours and short paths between drugs, diseases, genes and phenotypes. No key needed.

Current version: **0.2.0**

PrimeKG is not on GitHub: the 0.1.0 adapter pointed at a `kg.csv` that does not exist there. This release downloads PrimeKG 2.1 once from Harvard Dataverse (`nodes.csv` and `edges.csv`, about 395 MB, each checked against a pinned size, MD5 and SHA-256), builds a local SQLite index, and answers `primekg_find_nodes`, `primekg_neighbours` and `primekg_paths` from it with no network call. `primekg_download` is the one tool that reaches the network outside the kit, and Biorouter asks before it runs.

This is a Biorouter extension (a `.brxt` bundle) built on the BRXT adapter kit
1.0.0. Install it from BAAM in Biorouter, or from a file:

```bash
biorouter extension install dist/primekgagent.brxt
```

## Tools

- `primekg_status`: shows which targets are configured, their routes and which settings are present. It never shows a value and makes no network call.
- `primekg_get`: reads from a target. It sends `GET`, or `POST` on routes declared as reads, and is marked read only, so Biorouter's Smart approval runs it without asking.

## Targets

| Target | Base URL | Credential | Reads | Writes |
| --- | --- | --- | --- | --- |
| `dataverse` | `https://dataverse.harvard.edu/api` | none | GET `/datasets/**`, GET `/info/**` | none |

## Settings

| Variable | Required | Secret | Purpose |
| --- | --- | --- | --- |
| `PRIMEKG_DATA_DIR` | no | no | Optional folder for the local PrimeKG copy (about 700 MB). Default: a folder beside Biorouter's data, kept across updates. |
| `PRIMEKG_LOG_LEVEL` | no | no | Logging level |

## How it protects your credentials

- Each credential is attached only to requests for its own target's host. A full URL
  that names another host, a path starting with `//`, plain `http` and a redirect to
  another host are all refused, so a prompt that names a URL cannot move a key off
  the service it belongs to.
- Reads use `GET`, or `POST` on the routes declared as reads. Writes go through a
  separate tool that Biorouter asks you to approve every time, and only to declared
  routes.
- The extension never runs a local command. Use Biorouter's Developer shell for that.
- Credential values never appear in tool results, error messages or logs.

## Develop and release

```bash
uv sync
uv run python -m unittest discover -s tests -v   # contract tests and leak suite
uv run python scripts/build_brxt.py               # writes dist/primekgagent.brxt and SHA256SUMS
uv run python scripts/version.py X.Y.Z            # moves every version string together
```

Push a tag `vX.Y.Z` equal to the manifest version to publish a release. The release
workflow refuses any other tag. Do not edit `src/primekgagent/_kit/` or `tests/kit_testing/`:
they are vendored from the kit in the Biorouter repository
(`scripts/brxt-adapter-kit/`, documented in `docs/extensions/brxt-adapter-kit.md`).

## License

Apache-2.0. See `LICENSE` and `NOTICE`.
