"""PrimeKGAgent's typed tools: the one-time download, and graph queries on the local copy."""

import json
from typing import Any, Literal, Optional, Union

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from mcp.types import TextContent, ToolAnnotations
from pydantic import Field

from . import __version__
from . import primekg_data as data
from . import primekg_fetch as fetch
from ._kit.tools import user_agent

NodeTypes = Literal[
    "gene/protein", "drug", "effect/phenotype", "disease", "biological_process", "molecular_function",
    "cellular_component", "exposure", "pathway", "anatomy",
]
NodeRef = Union[int, str]


def _result(payload: Any) -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False, default=str))])


def _local(title: str) -> ToolAnnotations:
    return ToolAnnotations(title=title, readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def _query(action):
    failure = None
    try:
        return _result(action(data.connect()))
    except data.NotReady as exc:
        failure = str(exc)
    except ValueError as exc:
        failure = str(exc)
    except Exception as exc:
        failure = f"The local PrimeKG query failed ({type(exc).__name__})."
    raise ToolError(failure)


def register(mcp: FastMCP, spec) -> None:
    agent = user_agent(spec, __version__)

    def primekg_data_status() -> ToolResult:
        path = data.index_path()
        payload: dict[str, Any] = {
            "dataset": data.DATASET_DOI, "dataset_version": data.DATASET_VERSION, "licence": data.DATASET_LICENCE,
            "citation": data.CITATION, "folder": str(path.parent), "index": str(path), "ready": path.is_file(),
            "download": fetch.status(),
            "files": {name: {"bytes": s["size"], "md5": s["md5"], "sha256": s["sha256"]} for name, s in data.FILES.items()},
        }
        if path.is_file():
            con = data.connect(path)
            payload["index_meta"] = data.meta(con)
            payload["index_bytes"] = path.stat().st_size
            payload["node_types"] = data.node_types(con)
            payload["relations"] = data.relation_types(con)
        return _result(payload)

    mcp.tool(
        primekg_data_status,
        name="primekg_data_status",
        description=(
            "Report whether PrimeKG is on this computer: the data folder, download or indexing progress, the pinned "
            "file checksums, and, once ready, the node types and relation types with their counts. No network call."
        ),
        annotations=_local("PrimeKG local data status"),
    )

    def primekg_download(
        keep_source_files: bool = Field(default=False, description="Keep the downloaded CSV files after the index is built (about 395 MB more on disk)."),
    ) -> ToolResult:
        return _result({**fetch.start(agent, keep_sources=keep_source_files),
                        "note": "Runs in the background: about 395 MB from Harvard Dataverse, then a few minutes to build the index. "
                                "Poll primekg_data_status until ready is true."})

    mcp.tool(
        primekg_download,
        name="primekg_download",
        description=(
            "Download PrimeKG once from Harvard Dataverse (doi:10.7910/DVN/IXA7BM, version 2.1: nodes.csv and edges.csv, "
            "about 395 MB), check each file against its pinned size, MD5 and SHA-256, and build a local SQLite index "
            "(about 700 MB). Runs in the background; Biorouter asks before it starts."
        ),
        annotations=ToolAnnotations(title="Download PrimeKG", readOnlyHint=False, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=True),
    )

    def primekg_find_nodes(
        query: str = Field(default="", max_length=200, description="Part of a node name, case insensitive (or the whole name with exact)."),
        node_type: Optional[NodeTypes] = Field(default=None, description="Only nodes of this type."),
        source: Optional[str] = Field(default=None, max_length=40, description="Only nodes from this source, for example NCBI, MONDO, DrugBank, HPO, REACTOME, GO, UBERON, CTD."),
        source_id: Optional[str] = Field(default=None, max_length=100, description="The node's id in its source, for example 1956 for EGFR (NCBI) or DB00945 for aspirin (DrugBank)."),
        exact: bool = Field(default=False, description="Match the whole name instead of part of it."),
        limit: int = Field(default=25, ge=1, le=data.MAX_LIMIT),
    ) -> ToolResult:
        return _query(lambda con: data.find_nodes(con, query, node_type, source, source_id, exact, limit))

    mcp.tool(
        primekg_find_nodes,
        name="primekg_find_nodes",
        description="Find PrimeKG nodes by name, type, source or source id in the local copy. Returns node_index values for the other queries. No network call.",
        annotations=_local("Find PrimeKG nodes"),
    )

    def primekg_neighbours(
        node: NodeRef = Field(description="A node_index, or a node's exact name."),
        node_type: Optional[NodeTypes] = Field(default=None, description="The node's type, when its name is shared by several nodes."),
        relation: Optional[str] = Field(default=None, max_length=60, description="Only this relation, for example indication, contraindication, drug_protein, disease_protein, protein_protein."),
        neighbour_type: Optional[NodeTypes] = Field(default=None, description="Only neighbours of this type."),
        limit: int = Field(default=100, ge=1, le=data.MAX_LIMIT),
    ) -> ToolResult:
        return _query(lambda con: data.neighbours(con, node, node_type, relation, neighbour_type, limit))

    mcp.tool(
        primekg_neighbours,
        name="primekg_neighbours",
        description="List a PrimeKG node's neighbours in the local copy, with counts by relation and neighbour type, optionally filtered. No network call.",
        annotations=_local("PrimeKG neighbours"),
    )

    def primekg_paths(
        source: NodeRef = Field(description="Start node: a node_index or an exact name."),
        target: NodeRef = Field(description="End node: a node_index or an exact name."),
        source_type: Optional[NodeTypes] = Field(default=None),
        target_type: Optional[NodeTypes] = Field(default=None),
        max_length: int = Field(default=3, ge=1, le=3, description="Longest path to look for, in edges."),
        relations: Optional[list[str]] = Field(default=None, max_length=20, description="Only follow these relations."),
        limit: int = Field(default=25, ge=1, le=data.MAX_PATHS),
    ) -> ToolResult:
        return _query(lambda con: data.paths(con, source, target, source_type, target_type, max_length, relations, limit))

    mcp.tool(
        primekg_paths,
        name="primekg_paths",
        description="Find the shortest paths (up to 3 edges) between two PrimeKG nodes in the local copy, with every node and relation on each path. No network call.",
        annotations=_local("PrimeKG paths"),
    )
