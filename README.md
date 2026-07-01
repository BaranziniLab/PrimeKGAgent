# PrimeKGAgent

BioRouter BRXT extension for PrimeKG biomedical graph workflows.

## What it provides
- MCP tools for status, target discovery, guarded API calls, guarded local command planning/execution, and JSON resource summarization.
- Read-first behavior: mutation-capable HTTP methods require `allow_mutation=true`.
- Local command execution is allowlisted and requires `allow_execute=true`.
- Bundled BioRouter skill: `primekg-knowledge-graph`.

## Install
Download `primekgagent.brxt` from the release page and install it through BioRouter Extensions.

## License
Apache-2.0.
