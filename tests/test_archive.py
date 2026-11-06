import io
import struct
import zipfile
import zlib

import inflate64
import pytest

from geopipeline.archive import open_zip_member


def deflate64_zip(path, data, *, crc=None, size=None, truncate=False, zip64=False):
    """A real method-9 ZIP fixture; stdlib cannot create this compression type."""
    compressor = inflate64.Deflater()
    compressed = compressor.deflate(data) + compressor.flush()
    if truncate:
        compressed = compressed[:-2]
    checksum = zlib.crc32(data) if crc is None else crc
    length = len(data) if size is None else size
    name = b"inkar_2025.csv"
    extra = struct.pack("<HHQQ", 1, 16, length, len(compressed)) if zip64 else b""
    declared_size = 0xffffffff if zip64 else length
    declared_compressed = 0xffffffff if zip64 else len(compressed)
    version = 45 if zip64 else 21
    local = struct.pack("<4s5H3I2H", b"PK\x03\x04", version, 0, 9, 0, 0,
                        checksum, declared_compressed, declared_size, len(name), len(extra)) + name + extra + compressed
    central = struct.pack("<4s6H3I5H2I", b"PK\x01\x02", version, version, 0, 9, 0, 0,
                          checksum, declared_compressed, declared_size, len(name), len(extra), 0,
                          0, 0, 0, 0) + name + extra
    end = struct.pack("<4s4H2IH", b"PK\x05\x06", 0, 0, 1, 1, len(central), len(local), 0)
    path.write_bytes(local + central + end)


@pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, 9])
def test_binary_and_text_streams_validate_complete_content(tmp_path, method):
    path = tmp_path / "source.zip"
    data = ("Kennziffer;Wert\n12051000;3,00\n12052000;4,00\n" * 10000).encode()
    if method == 9:
        deflate64_zip(path, data)
    else:
        with zipfile.ZipFile(path, "w", compression=method) as archive:
            archive.writestr("inkar_2025.csv", data)
    with open_zip_member(path, "inkar_*.csv") as (stream, metadata):
        text = io.TextIOWrapper(stream, encoding="utf-8")
        assert sum(1 for _ in text) == 30000
        assert metadata["integrity_verified"] is True
        assert metadata["uncompressed_bytes"] == len(data)


def test_early_close_does_not_claim_integrity(tmp_path):
    path = tmp_path / "source.zip"
    deflate64_zip(path, b"a" * 200000)
    with open_zip_member(path, "*.csv") as (stream, metadata):
        assert stream.read(1) == b"a"
    assert metadata["integrity_verified"] is False


def test_zip64_sizes_are_resolved_before_reading_deflate64(tmp_path):
    path = tmp_path / "source.zip"
    data = b"ZIP64-content\n" * 10000
    deflate64_zip(path, data, zip64=True)
    with open_zip_member(path, "*.csv") as (stream, metadata):
        assert stream.read() == data
        assert metadata["uncompressed_bytes"] == len(data)
        assert metadata["integrity_verified"]


@pytest.mark.parametrize("corruption", ["crc", "size", "truncated"])
def test_corrupt_deflate64_fails_before_success(tmp_path, corruption):
    path = tmp_path / "source.zip"
    options = {"crc": {"crc": 0}, "size": {"size": 10001}, "truncated": {"truncate": True}}
    deflate64_zip(path, b"a" * 10000, **options[corruption])
    with pytest.raises(ValueError):
        with open_zip_member(path, "*.csv") as (stream, _):
            stream.read()


def test_limits_ambiguity_and_no_extraction(tmp_path):
    path = tmp_path / "source.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../../outside.csv", b"safe")
    with open_zip_member(path, "*.csv") as (stream, metadata):
        assert stream.read() == b"safe"
        assert metadata["integrity_verified"]
    assert list(tmp_path.iterdir()) == [path]
    with pytest.raises(ValueError, match="limit"):
        with open_zip_member(path, "*.csv", 3):
            pass
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("second.csv", b"also safe")
    with pytest.raises(ValueError, match="exactly one"):
        with open_zip_member(path, "*.csv"):
            pass
