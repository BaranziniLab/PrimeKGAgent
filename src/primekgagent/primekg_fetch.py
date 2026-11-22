# brxt-kit: allow-direct-network downloads the two pinned PrimeKG files from dataverse.harvard.edu (which redirects to its own storage); no credential or setting is sent and the URLs are fixed in primekg_data.FILES
"""Download the pinned PrimeKG files once and build the local index, in a background thread.

The kit's SafeHttpClient caps a response at 20 MB and never follows a redirect to another host,
so it cannot fetch a 387 MB file that Dataverse serves from cloud storage. This module is the one
place the extension reaches the network outside the kit. It sends no credential, takes no URL
from the model, writes only inside the PrimeKG data folder, and keeps a file only when its size,
MD5 and SHA-256 equal the values pinned in primekg_data.FILES.
"""

import hashlib
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from . import primekg_data as data

DATAVERSE = "https://dataverse.harvard.edu/api/access/datafile/{id}"
CHUNK = 1 << 20

_lock = threading.Lock()
_state: dict[str, Any] = {"phase": "idle"}


def file_url(spec: dict[str, Any]) -> str:
    url = DATAVERSE.format(id=int(spec["datafile_id"]))
    return url + ("?format=original" if spec.get("format") == "original" else "")


def fetch_file(url: str, spec: dict[str, Any], target: Path, user_agent: str, on_bytes=None, timeout_s: float = 60.0) -> None:
    """Stream one file to target.part and keep it only if size and checksums match."""
    part = target.with_name(target.name + ".part")
    md5, sha = hashlib.md5(), hashlib.sha256()
    size = 0
    limit = int(spec["size"])
    with httpx.Client(follow_redirects=True, timeout=timeout_s, headers={"User-Agent": user_agent}, trust_env=True) as client:
        with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise RuntimeError(f"Dataverse answered HTTP {response.status_code} for {target.name}.")
            with open(part, "wb") as out:
                for chunk in response.iter_bytes(CHUNK):
                    size += len(chunk)
                    if size > limit:
                        raise RuntimeError(f"{target.name} is larger than the pinned {limit} bytes; download stopped.")
                    md5.update(chunk); sha.update(chunk); out.write(chunk)
                    if on_bytes:
                        on_bytes(len(chunk))
    if size != limit or md5.hexdigest() != spec["md5"] or sha.hexdigest() != spec["sha256"]:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"{target.name} does not match the pinned size and checksums; it was discarded.")
    os.replace(part, target)


def status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def _set(**values: Any) -> None:
    with _lock:
        _state.update(values)


def _run(folder: Path, user_agent: str, keep_sources: bool) -> None:
    downloads = folder / "downloads"
    try:
        downloads.mkdir(parents=True, exist_ok=True)
        total = sum(int(s["size"]) for s in data.FILES.values())
        done = 0

        def count(n: int) -> None:
            nonlocal done
            done += n
            _set(downloaded_bytes=done)

        _set(phase="downloading", total_bytes=total, downloaded_bytes=0, started=time.time(), error=None)
        free = shutil.disk_usage(folder).free
        if free < 2 * 1024**3:
            raise RuntimeError(f"Only {free // 1024**2} MB free in {folder}; PrimeKG needs about 2 GB while it builds.")
        for name, spec in data.FILES.items():
            target = downloads / name
            if target.is_file() and target.stat().st_size == int(spec["size"]):
                count(int(spec["size"]))
                continue
            fetch_file(file_url(spec), spec, target, user_agent, count)
        _set(phase="indexing", edges_loaded=0)
        counts = data.build_index(downloads, data.index_path(folder), progress=lambda n: _set(edges_loaded=n))
        if not keep_sources:
            shutil.rmtree(downloads, ignore_errors=True)
        _set(phase="ready", finished=time.time(), **counts)
    except Exception as exc:  # reported through primekg_data_status, never raised into the server
        _set(phase="failed", error=f"{type(exc).__name__}: {exc}"[:500])


def start(user_agent: str, folder: Optional[Path] = None, keep_sources: bool = False) -> dict[str, Any]:
    folder = folder or data.data_dir()
    with _lock:
        if _state.get("phase") in {"downloading", "indexing"}:
            return {"started": False, "reason": "A download is already running.", **_state}
        _state.clear()
        _state.update(phase="starting", folder=str(folder))
    thread = threading.Thread(target=_run, args=(folder, user_agent, keep_sources), name="primekg-download", daemon=True)
    thread.start()
    return {"started": True, "folder": str(folder)}
