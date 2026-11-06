"""Bounded HTTP reads, conditional cache and atomic downloads."""
from pathlib import Path
from hashlib import sha256
from urllib.parse import urlsplit, urlunsplit
import json
import os
import tempfile
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def public_url(url: str) -> str:
    parsed = urlsplit(url)
    # Reports must never reveal URL userinfo or query credentials.
    host = parsed.hostname or ""
    if parsed.port:
        host += f":{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


class Http:
    def __init__(self, cache_dir: str | Path, timeout: int = 120):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = (15, timeout)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MOSIDI-Geodata-Pipeline/0.1 (+public-data-import)", "Accept-Encoding": "identity"})
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"], respect_retry_after_header=True)
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.session.mount("http://", HTTPAdapter(max_retries=retries))

    def close(self):
        self.session.close()

    def get(self, url: str, params: dict, max_bytes: int = 80_000_000) -> bytes:
        with self.session.get(url, params=params, timeout=self.timeout, stream=True) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("HTTP response exceeds configured limit")
                chunks.append(chunk)
            return b"".join(chunks)

    def download(self, url: str, max_bytes: int = 650_000_000) -> tuple[Path, dict]:
        name = sha256(url.encode()).hexdigest()
        path, meta_path = self.cache_dir / name, self.cache_dir / f"{name}.json"
        metadata = {}
        if path.exists() and meta_path.exists():
            try:
                metadata = json.loads(meta_path.read_text())
            except (ValueError, OSError):
                pass
        if path.exists():
            size = path.stat().st_size
            if size > max_bytes:
                raise ValueError("Cached download exceeds configured limit")
            digest = sha256()
            with path.open("rb") as cached:
                for block in iter(lambda: cached.read(1024 * 1024), b""):
                    digest.update(block)
            if metadata.get("sha256") != digest.hexdigest() or metadata.get("bytes") != size:
                # Re-fetch a corrupt cache instead of assigning stale provenance.
                metadata = {}
        headers = {}
        if metadata.get("etag"):
            headers["If-None-Match"] = metadata["etag"]
        elif metadata.get("last_modified"):
            headers["If-Modified-Since"] = metadata["last_modified"]
        with self.session.get(url, timeout=self.timeout, stream=True, headers=headers) as response:
            if response.status_code == 304:
                if path.exists() and metadata.get("sha256"):
                    return path, {**metadata, "cached": True}
                raise ValueError("Unexpected HTTP 304 without a verified cache")
            response.raise_for_status()
            expected = int(response.headers.get("Content-Length", 0))
            if expected > max_bytes:
                raise ValueError(f"Download exceeds configured limit: {expected} bytes")
            fd, temp = tempfile.mkstemp(dir=self.cache_dir, prefix="download-")
            digest, size = sha256(), 0
            try:
                with os.fdopen(fd, "wb") as stream:
                    for chunk in response.iter_content(1024 * 1024):
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError("Download exceeds configured limit")
                        digest.update(chunk)
                        stream.write(chunk)
                if expected and size != expected:
                    raise ValueError("Incomplete download")
                os.replace(temp, path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            metadata = {"url": public_url(url), "sha256": digest.hexdigest(), "bytes": size,
                        "etag": response.headers.get("ETag"), "last_modified": response.headers.get("Last-Modified")}
            fd, temp = tempfile.mkstemp(dir=self.cache_dir, prefix="metadata-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(metadata, stream)
                os.replace(temp, meta_path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            return path, {**metadata, "cached": False}

