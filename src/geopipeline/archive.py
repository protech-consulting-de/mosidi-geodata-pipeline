"""Read a single ZIP member without extracting files, including Deflate64."""
from contextlib import contextmanager
from fnmatch import fnmatchcase
import io
from pathlib import Path
import struct
import zipfile
import zlib


class _CheckedReader(io.RawIOBase):
    """Bounded reader whose integrity result remains false after an early close."""

    def __init__(self, stream, info, metadata, decoder=None):
        self.stream, self.info, self.metadata = stream, info, metadata
        self.decoder = decoder
        self.remaining = info.compress_size
        self.buffer = b""
        self.position = 0
        self.total = 0
        self.crc = 0
        self.finished = False

    def readable(self):
        return True

    def _check(self):
        if self.total != self.info.file_size:
            raise ValueError("ZIP member size mismatch or truncated content")
        if self.crc != self.info.CRC:
            raise ValueError("ZIP member CRC32 mismatch")
        self.finished = True
        self.metadata["integrity_verified"] = True

    def _next(self):
        if self.decoder is None:
            data = self.stream.read(64 * 1024)
            if not data:
                self._check()
                return b""
        else:
            while True:
                if self.remaining == 0:
                    if not self.decoder.eof:
                        raise ValueError("Truncated Deflate64 member")
                    self._check()
                    return b""
                if self.decoder.eof:
                    raise ValueError("Deflate64 member ends before its compressed size")
                # inflate64 does not expose unconsumed input. Small compressed
                # blocks bound expansion memory even for long Deflate64 matches.
                requested = min(512, self.remaining)
                chunk = self.stream.read(requested)
                if len(chunk) != requested:
                    raise ValueError("Truncated compressed ZIP member")
                self.remaining -= len(chunk)
                try:
                    data = self.decoder.inflate(chunk)
                except (ValueError, RuntimeError) as exc:
                    raise ValueError("Invalid Deflate64 stream") from exc
                if data:
                    break
        self.total += len(data)
        if self.total > self.info.file_size:
            raise ValueError("ZIP member exceeds its declared uncompressed size")
        self.crc = zlib.crc32(data, self.crc)
        return data

    def readinto(self, target):
        self._checkClosed()
        view = memoryview(target).cast("B")
        if not view:
            return 0
        if self.position == len(self.buffer):
            if self.finished:
                return 0
            self.buffer = self._next()
            self.position = 0
            if not self.buffer:
                return 0
        count = min(len(view), len(self.buffer) - self.position)
        view[:count] = self.buffer[self.position:self.position + count]
        self.position += count
        return count


@contextmanager
def open_zip_member(path: str | Path, pattern: str,
                    max_uncompressed_bytes: int = 8_000_000_000):
    """Yield ``(binary_stream, metadata)``; validate CRC and size at EOF.

    The caller must read through EOF to establish completeness. No archive member
    is written to the filesystem, and ambiguous patterns fail instead of picking
    an arbitrary member. Metadata records whether complete validation occurred.
    """
    if max_uncompressed_bytes < 0:
        raise ValueError("max_uncompressed_bytes must be nonnegative")
    with zipfile.ZipFile(path) as archive:
        members = [item for item in archive.infolist()
                   if not item.is_dir() and fnmatchcase(item.filename, pattern)]
        if len(members) != 1:
            raise ValueError(f"ZIP pattern must match exactly one member, found {len(members)}")
        info = members[0]
        if info.file_size > max_uncompressed_bytes:
            raise ValueError("ZIP member exceeds uncompressed limit")
        if info.flag_bits & (1 | 64):
            raise ValueError("Encrypted ZIP members are unsupported")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, 9}:
            raise ValueError(f"Unsupported ZIP compression method: {info.compress_type}")
        metadata = {"zip_member": info.filename, "uncompressed_bytes": info.file_size,
                    "compressed_bytes": info.compress_size,
                    "zip_compression": info.compress_type,
                    "crc32": f"{info.CRC:08x}", "integrity_verified": False}
        if info.compress_type != 9:
            with archive.open(info) as member:
                with io.BufferedReader(_CheckedReader(member, info, metadata)) as stream:
                    yield stream, metadata
            return

        from inflate64 import Inflater
        with Path(path).open("rb") as member:
            member.seek(info.header_offset)
            header = member.read(30)
            if len(header) != 30:
                raise ValueError("Truncated ZIP local header")
            signature, _, flags, method, _, _, _, _, _, name_len, extra_len = struct.unpack(
                "<4s5H3I2H", header)
            if signature != b"PK\x03\x04" or method != 9 or flags != info.flag_bits:
                raise ValueError("ZIP local header disagrees with central directory")
            name = member.read(name_len)
            encoding = "utf-8" if flags & 0x800 else "cp437"
            if name.decode(encoding) != info.orig_filename:
                raise ValueError("ZIP local filename disagrees with central directory")
            member.seek(extra_len, io.SEEK_CUR)
            if member.tell() + info.compress_size > archive.start_dir:
                raise ValueError("Truncated or overlapping ZIP member")
            with io.BufferedReader(_CheckedReader(member, info, metadata, Inflater())) as stream:
                yield stream, metadata
