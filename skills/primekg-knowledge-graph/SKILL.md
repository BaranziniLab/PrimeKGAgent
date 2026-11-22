---
name: primekg-knowledge-graph
description: "How to call PrimeKGAgent's tools: download PrimeKG once, then look up nodes, neighbours and short paths in the local copy."
---

# Calling PrimeKGAgent

This skill says how to call the PrimeKGAgent extension's tools. For the method itself (choosing
databases, interpreting interaction evidence), use the BAAM skill `database-access`.

PrimeKG (version 2.1, Harvard Dataverse doi:10.7910/DVN/IXA7BM, CC0 1.0) is a precision
medicine knowledge graph: about 129,000 nodes (genes and proteins, drugs, diseases, phenotypes,
exposures, anatomy, pathways and GO terms) and 4 million relations, each stored in both
directions. It is a different graph from SPOKE; use SPOKEAgent for SPOKE.

## First use

1. `primekg_data_status`: is PrimeKG on this computer yet?
2. If not, `primekg_download` once. Biorouter asks the person first: it fetches about 395 MB,
   checks each file against pinned checksums and builds a local index of about 700 MB, usually within a minute. It runs in
   the background; call `primekg_data_status` until `ready` is true.

## Queries (local, no network call)

- `primekg_find_nodes`: by part of a name (`query`), `node_type`, `source` (NCBI, MONDO,
  MONDO_grouped, DrugBank, HPO, GO, UBERON, REACTOME, CTD) or `source_id` (EGFR is NCBI 1956,
  aspirin is DrugBank DB00945). Use the returned `node_index` in the other tools.
- `primekg_neighbours`: a node's neighbours, with counts by relation and neighbour type. Filter
  with `relation` (for example `indication`, `contraindication`, `off-label use`,
  `drug_protein`, `disease_protein`, `protein_protein`, `disease_phenotype_positive`) or
  `neighbour_type`.
- `primekg_paths`: shortest paths of up to three edges between two nodes, optionally only along
  given `relations`.
- `primekg_status` and `primekg_get` read the Dataverse API (target `dataverse`), for example
  `/datasets/:persistentId/` with `query: {"persistentId": "doi:10.7910/DVN/IXA7BM"}`.

## Rules

- A name can be shared by nodes of different types (aspirin is both a drug and a CTD exposure).
  Pass `node_type`, or use the `node_index`.
- A relation in PrimeKG is evidence from a source database, not a causal claim. Report the
  relation name and the source and source id of each node, and cite PrimeKG (Chandak, Huang and
  Zitnik, Scientific Data 2023) and its version.
- The extension holds no credential and runs no command.
