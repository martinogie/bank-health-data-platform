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
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

RAW_VOLUME = "/Volumes/bank_health/bronze/raw_files"


class DatabricksFiles:
    def __init__(self, host: str, token: str, session: requests.Session | None = None):
        if not host or not token:
            raise ValueError("Databricks host and token are required (see .env)")
        self.host = host.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    @classmethod
    def from_env(cls) -> "DatabricksFiles":
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
                wait = 2 ** attempt
                log.warning("Databricks %s %s -> %s, retrying in %ss", method, url, resp.status_code, wait)
                # A streamed file body has been consumed; rewind it before retrying.
                body = kwargs.get("data")
                if hasattr(body, "seek"):
                    body.seek(0)
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
        url = self._url("files", volume_path)
        with open(local_path, "rb") as fh:
            self._request(
                "PUT", url, data=fh,
                params={"overwrite": str(overwrite).lower()},
                headers={"Content-Type": "application/octet-stream"},
            )
        log.info("uploaded %s -> %s", local_path, volume_path)

    def upload_files(
        self, local_paths: Iterable[Path], local_root: Path, volume_root: str = RAW_VOLUME
    ) -> list[str]:
        """Upload files, keeping their path relative to `local_root`.

        data/raw/fdic/financials/x.jsonl.gz with local_root=data/raw becomes
        /Volumes/bank_health/bronze/raw_files/fdic/financials/x.jsonl.gz
        """
        uploaded = []
        for p in local_paths:
            rel = Path(p).resolve().relative_to(Path(local_root).resolve())
            target = str(PurePosixPath(volume_root, *rel.parts))
            self.upload_file(Path(p), target)
            uploaded.append(target)
        return uploaded

    # ---------------------------------------------------------------- download
    def list_directory(self, volume_dir: str) -> Iterator[dict]:
        """Yield entries ({path, is_directory, file_size, ...}) of a Volume folder."""
        url = self._url("directories", volume_dir.rstrip("/"))
        token = None
        while True:
            params = {"page_token": token} if token else {}
            payload = self._request("GET", url, params=params).json()
            yield from payload.get("contents", [])
            token = payload.get("next_page_token")
            if not token:
                break

    def download_file(self, volume_path: str, local_path: Path) -> Path:
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        resp = self._request("GET", self._url("files", volume_path), stream=True)
        with open(local_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
        return local_path
