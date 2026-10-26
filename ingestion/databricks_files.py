"""Move files in and out of a Databricks Unity Catalog Volume via the Files API.

Why: Databricks Free Edition restricts outbound internet, so Airflow downloads
the public data and pushes it into the Volume; Databricks notebooks read it from
there. The same client later pulls gold exports back out for Snowflake.

API reference: PUT/GET /api/2.0/fs/files{path}, GET /api/2.0/fs/directories{path}
The access token comes from the environment and is never logged.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import requests

from ingestion.environment import assert_can_write, databricks_catalog

log = logging.getLogger(__name__)


def raw_volume() -> str:
    """Raw-files Volume in the current environment's catalog (BANK_HEALTH_ENV decides which)."""
    return f"/Volumes/{databricks_catalog()}/bronze/raw_files"


class DatabricksFiles:
    def __init__(self, host: str, token: str, session: requests.Session | None = None):
        if not host or not token:
            raise ValueError("Databricks host and token are required (see .env)")
        self.host = host.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    @classmethod
    def from_env(cls) -> DatabricksFiles:
        return cls(os.environ.get("DATABRICKS_HOST", ""), os.environ.get("DATABRICKS_TOKEN", ""))

    def __repr__(self) -> str:  # never show the token
        return f"DatabricksFiles(host={self.host!r})"

    def _url(self, kind: str, path: str) -> str:
        if not path.startswith("/Volumes/"):
            raise ValueError(f"Expected a /Volumes/... path, got {path!r}")
        return f"{self.host}/api/2.0/fs/{kind}{quote(path)}"

    def _request(self, method: str, url: str, retries: int = 3, **kwargs) -> requests.Response:
        for attempt in range(retries + 1):
            resp = self.session.request(method, url, timeout=600, **kwargs)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = 2**attempt
                log.warning("Databricks %s %s -> %s, retrying in %ss", method, url, resp.status_code, wait)
                # A streamed file body has been consumed; rewind it before retrying.
                seek = getattr(kwargs.get("data"), "seek", None)
                if seek is not None:
                    seek(0)
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise requests.HTTPError(
                    f"Databricks {method} {url} failed: {resp.status_code} {resp.text[:300]}",
                    response=resp,
                )
            return resp
        raise AssertionError("unreachable")

    # ------------------------------------------------------------------ upload
    def upload_file(self, local_path: Path, volume_path: str, overwrite: bool = True) -> None:
        """Upload one file (streamed, so large files don't fill memory)."""
        assert_can_write(volume_path)
        url = self._url("files", volume_path)
        with open(local_path, "rb") as fh:
            self._request(
                "PUT",
                url,
                data=fh,
                params={"overwrite": str(overwrite).lower()},
                headers={"Content-Type": "application/octet-stream"},
            )
        log.info("uploaded %s -> %s", local_path, volume_path)

    def upload_files(
        self, local_paths: Iterable[Path], local_root: Path, volume_root: str | None = None
    ) -> list[str]:
        """Upload files, keeping their path relative to `local_root` (default: the raw Volume).

        data/raw/fdic/financials/x.jsonl.gz with local_root=data/raw becomes
        /Volumes/bank_health/bronze/raw_files/fdic/financials/x.jsonl.gz
        """
        volume_root = volume_root or raw_volume()
        uploaded = []
        for p in local_paths:
            rel = Path(p).resolve().relative_to(Path(local_root).resolve())
            target = str(PurePosixPath(volume_root, *rel.parts))
            self.upload_file(Path(p), target)
            uploaded.append(target)
        return uploaded

    def upload_if_missing(
        self, local_paths: Iterable[Path], local_root: Path, volume_root: str | None = None
    ) -> list[str]:
        """Upload only files the Volume doesn't have yet (or has with a different size).

        Used for complete files reused from disk, so a re-run doesn't push (and bronze
        doesn't re-ingest) hundreds of unchanged files, while a fresh Volume still fills up.
        """
        volume_root = volume_root or raw_volume()
        remote_sizes: dict[str, int | None] = {}
        listed: set[str] = set()
        uploaded = []
        for p in local_paths:
            rel = Path(p).resolve().relative_to(Path(local_root).resolve())
            target = str(PurePosixPath(volume_root, *rel.parts))
            folder = str(PurePosixPath(target).parent)
            if folder not in listed:
                listed.add(folder)
                try:
                    for e in self.list_directory(folder):
                        if not e.get("is_directory"):
                            remote_sizes[e["path"]] = e.get("file_size")
                except requests.HTTPError as exc:
                    if getattr(exc.response, "status_code", None) != 404:
                        raise
            if remote_sizes.get(target) != Path(p).stat().st_size:
                self.upload_file(Path(p), target)
                uploaded.append(target)
        return uploaded

    # ---------------------------------------------------------------- download
    def list_directory(self, volume_dir: str) -> Iterator[dict]:
        """Yield entries ({path, is_directory, file_size, ...}) of a Volume folder."""
        url = self._url("directories", volume_dir.rstrip("/"))
        token = None
        while True:
            params: dict[str, str] = {"page_token": token} if token else {}
            payload = self._request("GET", url, params=params).json()
            yield from payload.get("contents", [])
            token = payload.get("next_page_token")
            if not token:
                break

    def delete_file(self, volume_path: str) -> None:
        """Delete one file (used by tests to clean up their probe files)."""
        assert_can_write(volume_path)
        self._request("DELETE", self._url("files", volume_path))

    def download_file(self, volume_path: str, local_path: Path) -> Path:
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        resp = self._request("GET", self._url("files", volume_path), stream=True)
        with open(local_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
        return local_path
