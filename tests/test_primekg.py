"""PrimeKG local index and queries on a small fixture, the download checks against a loopback
server, and the MCP surface."""

import asyncio
import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "primekg"

from primekgagent import primekg_data as data  # noqa: E402
from primekgagent import primekg_fetch as fetch  # noqa: E402


class Index(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / data.INDEX_NAME
        cls.counts = data.build_index(FIXTURE, cls.db)
        cls.con = data.connect(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_counts_and_meta(self):
        self.assertEqual(self.counts, {"nodes": 9, "edges": 18})
        self.assertEqual(data.meta(self.con)["version"], data.DATASET_VERSION)

    def test_find_by_name_type_and_source_id(self):
        out = data.find_nodes(self.con, "asp")
        self.assertEqual([n["name"] for n in out["nodes"]], ["Aspirin"])
        out = data.find_nodes(self.con, source="DrugBank", source_id="DB00945")
        self.assertEqual(out["nodes"][0]["node_index"], 3)
        out = data.find_nodes(self.con, node_type="gene/protein", limit=2)
        self.assertEqual((out["matches"], out["returned"]), (4, 2))
        self.assertEqual(data.find_nodes(self.con, "pain, chronic", exact=True)["nodes"][0]["node_index"], 6)

    def test_like_wildcards_are_literal(self):
        self.assertEqual(data.find_nodes(self.con, "%")["matches"], 0)

    def test_neighbours(self):
        out = data.neighbours(self.con, "Afatinib")
        self.assertEqual(out["total"], 3)
        self.assertEqual({n["name"] for n in out["neighbours"]}, {"EGFR", "ERBB2", "lung adenocarcinoma"})
        out = data.neighbours(self.con, 1, relation="drug_protein")
        self.assertEqual({n["name"] for n in out["neighbours"]}, {"EGFR", "ERBB2"})
        by = {(r["relation"], r["neighbour_type"]): r["count"] for r in data.neighbours(self.con, 3)["by_relation"]}
        self.assertEqual(by, {("drug_protein", "gene/protein"): 2, ("indication", "disease"): 1})

    def test_ambiguous_and_missing_names(self):
        with self.assertRaises(ValueError):
            data.resolve(self.con, "no such node")
        self.assertEqual(data.resolve(self.con, "egfr")["node_index"], 0)

    def test_paths(self):
        direct = data.paths(self.con, "Afatinib", "EGFR")
        self.assertEqual(direct["shortest_length"], 1)
        two = data.paths(self.con, "Afatinib", "lung adenocarcinoma", relations=["drug_protein", "disease_protein"])
        self.assertEqual(two["shortest_length"], 2)
        self.assertEqual([n["name"] for n in two["paths"][0]], ["Afatinib", "EGFR", "lung adenocarcinoma"])
        three = data.paths(self.con, "ERBB2", "lung adenocarcinoma", relations=["drug_protein", "indication", "protein_protein", "disease_protein"])
        self.assertEqual(three["shortest_length"], 2)  # ERBB2 - EGFR - disease and ERBB2 - Afatinib - disease
        self.assertEqual(three["paths_found"], 2)
        far = data.paths(self.con, "PTGS1", "pain, chronic", max_length=3)
        self.assertEqual(far["shortest_length"], 2)
        none = data.paths(self.con, "PTGS1", "lung adenocarcinoma", max_length=2)
        self.assertIsNone(none["shortest_length"])
        three_hops = data.paths(self.con, "PTGS1", "PTGS2", max_length=3, relations=["drug_protein", "disease_protein"])
        self.assertEqual(three_hops["shortest_length"], 2)
        longest = data.paths(self.con, "PTGS1", "pain, chronic", relations=["drug_protein", "disease_protein"])
        self.assertEqual(longest["shortest_length"], 3)
        self.assertEqual([n["name"] for n in longest["paths"][0]], ["PTGS1", "Acetylsalicylic acid", "PTGS2", "pain, chronic"])
        self.assertEqual([n.get("via_relation") for n in longest["paths"][0]], [None, "drug_protein", "drug_protein", "disease_protein"])


class Folder(unittest.TestCase):
    def test_folder_order(self):
        with mock.patch.dict(os.environ, {"PRIMEKG_DATA_DIR": "/x/kg", "BIOROUTER_PATH_ROOT": "/r"}):
            self.assertEqual(data.data_dir(), Path("/x/kg"))
        with mock.patch.dict(os.environ, {"PRIMEKG_DATA_DIR": "", "BIOROUTER_PATH_ROOT": "/r"}):
            self.assertEqual(data.data_dir(), Path("/r/data/extension-data/primekgagent"))

    def test_not_ready_message(self):
        with self.assertRaises(data.NotReady):
            data.connect(Path(tempfile.mkdtemp()) / "missing.sqlite3")


class _Handler(http.server.BaseHTTPRequestHandler):
    body = b""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


class Download(unittest.TestCase):
    def setUp(self):
        _Handler.body = b"relation,display_relation,x_index,y_index\n" * 50
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/f"
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def spec(self, **over):
        body = _Handler.body
        return {"size": len(body), "md5": hashlib.md5(body).hexdigest(), "sha256": hashlib.sha256(body).hexdigest(), **over}

    def test_kept_when_checksums_match(self):
        target = self.tmp / "edges.csv"
        fetch.fetch_file(self.url, self.spec(), target, "test")
        self.assertEqual(target.read_bytes(), _Handler.body)

    def test_discarded_on_checksum_mismatch(self):
        target = self.tmp / "edges.csv"
        with self.assertRaises(RuntimeError):
            fetch.fetch_file(self.url, self.spec(sha256="0" * 64), target, "test")
        self.assertFalse(target.exists())
        self.assertFalse(target.with_name("edges.csv.part").exists())

    def test_stops_when_larger_than_pinned(self):
        with self.assertRaises(RuntimeError):
            fetch.fetch_file(self.url, self.spec(size=10), self.tmp / "edges.csv", "test")

    def test_urls_are_fixed_dataverse_urls(self):
        for spec in data.FILES.values():
            self.assertTrue(fetch.file_url(spec).startswith("https://dataverse.harvard.edu/api/access/datafile/"))


class Surface(unittest.TestCase):
    def test_tools_and_annotations(self):
        from fastmcp import Client

        from primekgagent.server import create_server

        async def go():
            async with Client(create_server()) as client:
                return {t.name: t.annotations.model_dump(by_alias=True) for t in await client.list_tools()}

        tools = asyncio.run(go())
        for name in ("primekg_data_status", "primekg_find_nodes", "primekg_neighbours", "primekg_paths"):
            self.assertTrue(tools[name]["readOnlyHint"], name)
            self.assertFalse(tools[name]["openWorldHint"], name)
        self.assertFalse(tools["primekg_download"]["readOnlyHint"])
        self.assertIn("primekg_get", tools)

    def test_query_before_download_says_what_to_do(self):
        from fastmcp import Client

        from primekgagent.server import create_server

        async def go():
            async with Client(create_server()) as client:
                return await client.call_tool("primekg_neighbours", {"node": "EGFR"}, raise_on_error=False)

        with mock.patch.dict(os.environ, {"PRIMEKG_DATA_DIR": tempfile.mkdtemp()}):
            result = asyncio.run(go())
        self.assertTrue(result.is_error)
        self.assertIn("primekg_download", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
