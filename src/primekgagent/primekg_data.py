"""PrimeKG on this computer: where the files live, the SQLite index, and the graph queries.

The data are the PrimeKG release on Harvard Dataverse (doi:10.7910/DVN/IXA7BM, version 2.1,
CC0 1.0): nodes.csv (the original upload of nodes.tab) and edges.csv. They are downloaded once
by primekg_fetch.py, checked against the sizes and checksums pinned below, and loaded into one
SQLite file. Every query here reads that file and nothing else: no network call, no command.
"""

import csv
import os
import sqlite3
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

DATASET_DOI = "doi:10.7910/DVN/IXA7BM"
DATASET_VERSION = "2.1"
DATASET_LICENCE = "CC0 1.0"
CITATION = (
    "Chandak P, Huang K, Zitnik M. Building a knowledge graph to enable precision medicine. "
    "Scientific Data 10, 67 (2023). https://doi.org/10.1038/s41597-023-01960-3"
)
# Pinned on 2026-11-22 from the Dataverse file metadata (size, MD5) and a download (SHA-256).
FILES = {
    "nodes.csv": {
        "datafile_id": 6180617,
        "format": "original",
        "size": 7869553,
        "md5": "7f9ab4109c54049e819ecd14e15a6038",
        "sha256": "301644c421b436fb6d5f548954a5d77749d87ff8ecbf27d42f41c07a075a2ad3",
    },
    "edges.csv": {
        "datafile_id": 6180616,
        "format": None,
        "size": 386582390,
        "md5": "5d4d211a22e88544b78fde2735e797bc",
        "sha256": "57c405049d0adb5ee9070d8b3fbb38f55a053b5c8d45b8b5af1ce23ee5d3ad7c",
    },
}
INDEX_NAME = f"primekg-{DATASET_VERSION}.sqlite3"
SCHEMA_VERSION = 1
MAX_LIMIT = 500
MAX_PATHS = 100
MAX_FRONTIER = 60000
MAX_EXPANSION_ROWS = 3_000_000


def data_dir() -> Path:
    """PRIMEKG_DATA_DIR, else a folder beside Biorouter's own data, kept across extension updates."""
    configured = (os.getenv("PRIMEKG_DATA_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser()
    root = (os.getenv("BIOROUTER_PATH_ROOT") or "").strip()
    if root:
        return Path(root).expanduser() / "data" / "extension-data" / "primekgagent"
    if sys.platform == "win32":
        base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "UCSF" / "biorouter" / "data"
    else:
        base = Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "biorouter"
    return base / "extension-data" / "primekgagent"


def index_path(folder: Optional[Path] = None) -> Path:
    return (folder or data_dir()) / INDEX_NAME


class NotReady(Exception):
    """The local index does not exist yet."""


def build_index(source: Path, target: Path, progress=None) -> dict[str, int]:
    """Load nodes.csv and edges.csv from ``source`` into a new SQLite file at ``target``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".building")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(
            """
            PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE nodes(node_index INTEGER PRIMARY KEY, node_id TEXT, node_type TEXT, node_name TEXT,
                               node_source TEXT, name_lower TEXT);
            CREATE TABLE edges(x INTEGER, y INTEGER, relation TEXT, display_relation TEXT);
            """
        )
        with open(source / "nodes.csv", newline="", encoding="utf-8") as handle:
            rows = ((int(r["node_index"]), r["node_id"], r["node_type"], r["node_name"], r["node_source"], r["node_name"].lower())
                    for r in csv.DictReader(handle))
            con.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?)", rows)
        nodes = con.execute("SELECT count(*) FROM nodes").fetchone()[0]
        edges = 0
        with open(source / "edges.csv", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            batch = []
            for row in reader:
                batch.append((int(row["x_index"]), int(row["y_index"]), row["relation"], row["display_relation"]))
                if len(batch) >= 200000:
                    con.executemany("INSERT INTO edges VALUES (?,?,?,?)", batch)
                    edges += len(batch)
                    batch.clear()
                    if progress:
                        progress(edges)
            con.executemany("INSERT INTO edges VALUES (?,?,?,?)", batch)
            edges += len(batch)
        con.executescript(
            """
            CREATE INDEX edges_x ON edges(x, relation);
            CREATE INDEX nodes_name ON nodes(name_lower);
            CREATE INDEX nodes_source_id ON nodes(node_source, node_id);
            CREATE INDEX nodes_type ON nodes(node_type);
            """
        )
        con.executemany(
            "INSERT INTO meta VALUES (?,?)",
            [("schema", str(SCHEMA_VERSION)), ("dataset", DATASET_DOI), ("version", DATASET_VERSION),
             ("nodes", str(nodes)), ("edges", str(edges)), ("built", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))],
        )
        con.commit()
    finally:
        con.close()
    os.replace(tmp, target)
    return {"nodes": nodes, "edges": edges}


_local = threading.local()


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    path = path or index_path()
    if not path.is_file():
        raise NotReady(
            "PrimeKG is not on this computer yet. Call primekg_download once (about 395 MB to download, "
            "about 700 MB on disk), then wait for primekg_data_status to say it is ready."
        )
    cache = getattr(_local, "connections", None)
    if cache is None:
        cache = _local.connections = {}
    key = str(path)
    con = cache.get(key)
    if con is None:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        cache[key] = con
    return con


def meta(con: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM meta")}


def _node(row: sqlite3.Row) -> dict[str, Any]:
    return {"node_index": row["node_index"], "name": row["node_name"], "type": row["node_type"],
            "source": row["node_source"], "source_id": row["node_id"]}


def find_nodes(con, query: str = "", node_type: Optional[str] = None, source: Optional[str] = None,
               source_id: Optional[str] = None, exact: bool = False, limit: int = 25) -> dict[str, Any]:
    clauses, args = [], []
    q = query.strip().lower()
    if q:
        if exact:
            clauses.append("name_lower = ?"); args.append(q)
        else:
            clauses.append("name_lower LIKE ? ESCAPE '\\'")
            args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if node_type:
        clauses.append("node_type = ?"); args.append(node_type)
    if source:
        clauses.append("node_source = ?"); args.append(source)
    if source_id:
        clauses.append("node_id = ?"); args.append(source_id)
    if not clauses:
        raise ValueError("Give a name query, a source_id, or a node_type.")
    where = " AND ".join(clauses)
    order = "ORDER BY (name_lower = ?) DESC, length(node_name), node_index" if q else "ORDER BY node_index"
    order_args = [q] if q else []
    rows = con.execute(f"SELECT * FROM nodes WHERE {where} {order} LIMIT ?", [*args, *order_args, min(limit, MAX_LIMIT)]).fetchall()
    total = con.execute(f"SELECT count(*) FROM nodes WHERE {where}", args).fetchone()[0]
    return {"matches": total, "returned": len(rows), "nodes": [_node(r) for r in rows]}


def resolve(con, node: Any, node_type: Optional[str] = None) -> sqlite3.Row:
    """A node by index (int or digits) or by exact name, optionally restricted to a type."""
    if isinstance(node, int) or (isinstance(node, str) and node.strip().isdigit()):
        row = con.execute("SELECT * FROM nodes WHERE node_index = ?", (int(node),)).fetchone()
        if row is None:
            raise ValueError(f"No node has index {node}.")
        return row
    name = str(node).strip().lower()
    sql, args = "SELECT * FROM nodes WHERE name_lower = ?", [name]
    if node_type:
        sql += " AND node_type = ?"; args.append(node_type)
    rows = con.execute(sql + " ORDER BY node_index LIMIT 6", args).fetchall()
    if not rows:
        raise ValueError(f"No node is named {node!r}. Use primekg_find_nodes to search by part of the name.")
    if len(rows) > 1:
        options = ", ".join(f"{r['node_index']} ({r['node_type']}, {r['node_source']} {r['node_id']})" for r in rows)
        raise ValueError(f"{node!r} names several nodes: {options}. Pass node_type or the node_index.")
    return rows[0]


def neighbours(con, node: Any, node_type: Optional[str] = None, relation: Optional[str] = None,
               neighbour_type: Optional[str] = None, limit: int = 100) -> dict[str, Any]:
    centre = resolve(con, node, node_type)
    sql = ("SELECT e.relation, e.display_relation, n.* FROM edges e JOIN nodes n ON n.node_index = e.y "
           "WHERE e.x = ?")
    args: list[Any] = [centre["node_index"]]
    if relation:
        sql += " AND e.relation = ?"; args.append(relation)
    if neighbour_type:
        sql += " AND n.node_type = ?"; args.append(neighbour_type)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for r in con.execute(sql.replace("e.relation, e.display_relation, n.*", "e.relation, n.node_type"), args):
        counts[(r[0], r[1])] += 1
    rows = con.execute(sql + " ORDER BY e.relation, n.node_name LIMIT ?", [*args, min(limit, MAX_LIMIT)]).fetchall()
    return {
        "node": _node(centre),
        "total": sum(counts.values()),
        "by_relation": [{"relation": k[0], "neighbour_type": k[1], "count": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])],
        "returned": len(rows),
        "neighbours": [{"relation": r["relation"], "display_relation": r["display_relation"], **_node(r)} for r in rows],
    }


class _Truncated(Exception):
    pass


def _step(con, frontier: Iterable[int], relations: Optional[list[str]], max_rows: Optional[int] = None) -> dict[int, list[tuple[int, str]]]:
    """For each node in frontier, its (neighbour, relation) pairs. Raises _Truncated past max_rows."""
    out: dict[int, list[tuple[int, str]]] = defaultdict(list)
    read = 0
    ids = list(frontier)
    rel_sql, rel_args = "", []
    if relations:
        rel_sql = " AND relation IN (%s)" % ",".join("?" * len(relations)); rel_args = list(relations)
    for start in range(0, len(ids), 900):
        chunk = ids[start:start + 900]
        q = f"SELECT x, y, relation FROM edges WHERE x IN ({','.join('?' * len(chunk))}){rel_sql}"
        for x, y, rel in con.execute(q, [*chunk, *rel_args]):
            out[x].append((y, rel))
            read += 1
            if max_rows is not None and read > max_rows:
                raise _Truncated()
    return out


def paths(con, source: Any, target: Any, source_type: Optional[str] = None, target_type: Optional[str] = None,
          max_length: int = 3, relations: Optional[list[str]] = None, limit: int = 25) -> dict[str, Any]:
    """Shortest paths of at most max_length edges (1 to 3), found from both ends."""
    a = resolve(con, source, source_type)
    b = resolve(con, target, target_type)
    ai, bi = a["node_index"], b["node_index"]
    limit = min(limit, MAX_PATHS)
    found: list[list[tuple[int, str]]] = []  # each path: [(node, relation into it)], first relation ""
    a1 = _step(con, [ai], relations).get(ai, [])
    for y, rel in a1:
        if y == bi:
            found.append([(ai, ""), (bi, rel)])
    length = 1 if found else None
    truncated = False
    if not found and max_length >= 2:
        b1 = _step(con, [bi], relations).get(bi, [])
        b_map: dict[int, list[str]] = defaultdict(list)
        for y, rel in b1:
            b_map[y].append(rel)
        for y, rel in a1:
            for rel_b in b_map.get(y, []):
                found.append([(ai, ""), (y, rel), (bi, rel_b)])
        length = 2 if found else None
        if not found and max_length >= 3:
            middle_a = {y for y, _ in a1 if y != bi}
            if len(middle_a) > MAX_FRONTIER or len(b_map) > MAX_FRONTIER:
                truncated = True
            else:
                a_rel: dict[int, list[str]] = defaultdict(list)
                for y, rel in a1:
                    a_rel[y].append(rel)
                try:
                    step = _step(con, middle_a, relations, MAX_EXPANSION_ROWS)
                except _Truncated:
                    step, truncated = {}, True
                for m1, pairs in step.items():
                    for m2, rel_mid in pairs:
                        if m2 in b_map and m2 != ai and m1 != bi:
                            for rel_a in a_rel[m1]:
                                for rel_b in b_map[m2]:
                                    found.append([(ai, ""), (m1, rel_a), (m2, rel_mid), (bi, rel_b)])
                length = 3 if found else None
    total = len(found)
    found.sort(key=lambda p: [n for n, _ in p])
    shown = found[:limit]
    ids = sorted({n for p in shown for n, _ in p})
    names = {}
    for start in range(0, len(ids), 900):
        chunk = ids[start:start + 900]
        for r in con.execute(f"SELECT * FROM nodes WHERE node_index IN ({','.join('?' * len(chunk))})", chunk):
            names[r["node_index"]] = _node(r)
    return {
        "source": _node(a), "target": _node(b), "max_length": max_length, "shortest_length": length,
        "paths_found": total, "returned": len(shown), "search_truncated": truncated,
        "paths": [[{**names[n], **({"via_relation": rel} if rel else {})} for n, rel in p] for p in shown],
    }


def relation_types(con) -> list[dict[str, Any]]:
    return [{"relation": r[0], "display_relation": r[1], "edges": r[2]} for r in
            con.execute("SELECT relation, display_relation, count(*) FROM edges GROUP BY relation, display_relation ORDER BY 3 DESC")]


def node_types(con) -> list[dict[str, Any]]:
    return [{"node_type": r[0], "nodes": r[1]} for r in
            con.execute("SELECT node_type, count(*) FROM nodes GROUP BY node_type ORDER BY 2 DESC")]
