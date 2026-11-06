"""HTTP-cache integrity regressions with no external network access."""
from hashlib import sha256
import json

import pytest

from geopipeline.http import Http


URL = "https://public.example.test/measurements.csv"


class FakeResponse:
    def __init__(self, status_code, body=b"", headers=None):
        self.status_code = status_code
        self.body = body
        self.headers = dict(headers or {})
        self.closed = False
        self.body_read = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        self.body_read = True
        for index in range(0, len(self.body), chunk_size):
            yield self.body[index:index + chunk_size]


class MockSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def mount(self, *_):
        pass

    def close(self):
        pass

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs, "headers": dict(kwargs.get("headers", {}))})
        if not self.responses:
            raise AssertionError("Unexpected extra HTTP request")
        return self.responses.pop(0)


def client(tmp_path, monkeypatch, response):
    session = MockSession([response])
    monkeypatch.setattr("geopipeline.http.requests.Session", lambda: session)
    return Http(tmp_path), session


def seed_cache(tmp_path, body, **validators):
    name = sha256(URL.encode()).hexdigest()
    path, meta_path = tmp_path / name, tmp_path / f"{name}.json"
    path.write_bytes(body)
    metadata = {"url": URL, "sha256": sha256(body).hexdigest(),
                "bytes": len(body), **validators}
    meta_path.write_text(json.dumps(metadata))
    return path, meta_path, metadata


@pytest.mark.parametrize("validators,expected_headers", [
    ({"etag": '"v1"', "last_modified": "Wed, 04 Nov 2026 12:00:00 GMT"},
     {"If-None-Match": '"v1"'}),
    ({"last_modified": "Wed, 04 Nov 2026 12:00:00 GMT"},
     {"If-Modified-Since": "Wed, 04 Nov 2026 12:00:00 GMT"}),
])
def test_verified_cache_accepts_304_without_reading_response_body(
        tmp_path, monkeypatch, validators, expected_headers):
    body = b"AGS;Wert\n12051000;123\n"
    cached, meta_path, metadata = seed_cache(tmp_path, body, **validators)
    original_metadata = meta_path.read_bytes()
    response = FakeResponse(304)
    http, session = client(tmp_path, monkeypatch, response)

    path, result = http.download(URL, max_bytes=len(body))

    assert path == cached and path.read_bytes() == body
    assert result == {**metadata, "cached": True}
    assert meta_path.read_bytes() == original_metadata
    assert session.calls[0]["headers"] == expected_headers
    assert response.closed and not response.body_read


def test_corrupted_cache_is_refetched_without_conditional_headers(tmp_path, monkeypatch):
    original = b"AGS;Wert\n12051000;123\n"
    cached, meta_path, _ = seed_cache(tmp_path, original, etag='"v1"')
    # Same byte length proves that validation checks the hash, not just size.
    cached.write_bytes(original.replace(b"123", b"999"))
    replacement = b"AGS;Wert\n12051000;456\n"
    response = FakeResponse(200, replacement, {"Content-Length": str(len(replacement)), "ETag": '"v2"'})
    http, session = client(tmp_path, monkeypatch, response)

    path, result = http.download(URL)

    assert session.calls[0]["headers"] == {}
    assert path.read_bytes() == replacement
    assert result["cached"] is False
    assert result["sha256"] == sha256(replacement).hexdigest()
    assert result["etag"] == '"v2"'
    assert json.loads(meta_path.read_text())["sha256"] == result["sha256"]
    assert response.closed


@pytest.mark.parametrize("corrupted_cache", [False, True])
def test_unexpected_304_without_verified_cache_fails(tmp_path, monkeypatch, corrupted_cache):
    if corrupted_cache:
        cached, _, _ = seed_cache(tmp_path, b"original", etag='"v1"')
        cached.write_bytes(b"modified")
    response = FakeResponse(304)
    http, session = client(tmp_path, monkeypatch, response)

    with pytest.raises(ValueError, match="304 without a verified cache"):
        http.download(URL)

    assert session.calls[0]["headers"] == {}
    assert response.closed and not response.body_read


def test_incomplete_download_preserves_existing_cache_and_metadata(tmp_path, monkeypatch):
    original = b"AGS;Wert\n12051000;123\n"
    cached, meta_path, _ = seed_cache(tmp_path, original, etag='"v1"')
    original_metadata = meta_path.read_bytes()
    response = FakeResponse(200, b"truncated", {"Content-Length": "100", "ETag": '"v2"'})
    http, session = client(tmp_path, monkeypatch, response)

    with pytest.raises(ValueError, match="Incomplete download"):
        http.download(URL)

    assert session.calls[0]["headers"] == {"If-None-Match": '"v1"'}
    assert cached.read_bytes() == original
    assert meta_path.read_bytes() == original_metadata
    assert set(tmp_path.iterdir()) == {cached, meta_path}
    assert response.closed
