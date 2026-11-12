"""Peel encoding layers off staged payloads. Recovered content is never executed.

Attack tooling hides commands and stolen secrets behind stacked encodings
(base64, hex, compression, a repeating XOR key) and splits them into numbered
chunks. A plain text scan of such logs misses most of what they carry.
"""

from __future__ import annotations

import base64
import binascii
import bz2
import hashlib
import lzma
import re
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

MAX_OUTPUT = 1_000_000
MAX_INPUT = 4_000_000
_B64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_B64_URL = re.compile(r"[A-Za-z0-9_-]+={0,2}")
_HEX = re.compile(r"(?:[0-9a-fA-F]{2})+")
_KEY_HINT = re.compile(
    r"(?i)\b(?:xor|campaign|c2|enc(?:ryption)?|cipher)[\w-]{0,12}?key\w{0,8}"
    r"""\s*[:=]\s*["']?([A-Za-z0-9_+/=.-]{2,64})"""
)
_ENVELOPE = {
    "channel": ("channel", "chan", "ch", "stream"),
    "seq": ("seq", "sequence", "part", "idx", "index", "n", "i"),
    "payload": ("payload", "data", "body", "chunk", "blob", "p"),
    "checksum": ("checksum", "crc32", "crc", "sum", "md5", "sha1", "sha256", "digest"),
    "type": ("type", "kind", "op", "t"),
}


class DecodeError(ValueError):
    """Input or output exceeded a fixed bound."""


@dataclass(frozen=True)
class Decoded:
    data: bytes
    layers: tuple[str, ...] = ()

    @property
    def text(self) -> str | None:
        """The payload as text, or None when it is still binary."""
        return self.data.decode("utf-8") if _is_text(self.data) else None


@dataclass(frozen=True)
class Stream:
    """One reassembled multi-part message."""

    channel: str
    type: str
    parts: int
    missing: tuple[int, ...]
    checksum: str  # "ok", "mismatch" or "absent"
    decoded: Decoded
    sources: tuple[Any, ...] = field(default=(), compare=False)


def _is_text(data: bytes) -> bool:
    if not data:
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    printable = sum(char.isprintable() or char in "\n\r\t" for char in text)
    return printable / len(text) >= 0.9


def _bounded(decompressor: Any, data: bytes) -> bytes | None:
    """The whole stream, or None when the data stops before the stream ends."""
    out = decompressor.decompress(data, MAX_OUTPUT + 1)
    if len(out) > MAX_OUTPUT:
        raise DecodeError("decoded payload exceeds the 1 MB limit")
    return out if decompressor.eof else None


def _decompress(data: bytes) -> tuple[str, bytes] | None:
    name, decompressor = "", None
    if data[:2] == b"\x1f\x8b":
        name, decompressor = "gzip", zlib.decompressobj(31)
    elif len(data) > 2 and data[0] == 0x78 and (data[0] * 256 + data[1]) % 31 == 0:
        name, decompressor = "zlib", zlib.decompressobj(15)
    elif data[:3] == b"BZh":
        name, decompressor = "bz2", bz2.BZ2Decompressor()
    elif data[:6] == b"\xfd7zXZ\x00":
        name, decompressor = "xz", lzma.LZMADecompressor()
    if decompressor is None:
        return None
    try:
        out = _bounded(decompressor, data)
    except (zlib.error, OSError, lzma.LZMAError, EOFError):
        return None
    return None if out is None else (name, out)


def _plausible(data: bytes) -> bool:
    return _is_text(data) or _decompress(data) is not None


def xor(data: bytes, key: bytes) -> bytes:
    if not key:
        raise ValueError("XOR key must not be empty")
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))


def _xor_layer(data: bytes, keys: tuple[bytes, ...]) -> tuple[str, bytes] | None:
    for key in keys:
        candidate = xor(data, key)
        if _plausible(candidate):
            return "xor", candidate
    if len(data) < 20:
        return None
    # One unknown key byte is cheap to search; longer unknown keys are not guessed.
    # Only plain ASCII counts here: with 255 tries, anything looser turns
    # ordinary identifiers into "decoded" noise. Several keys can keep a short
    # text printable; the one that yields the most letters and spaces wins.
    best: tuple[int, int, bytes] | None = None
    for byte in range(1, 256):
        candidate = xor(data, bytes([byte]))
        if all(32 <= value < 127 or value in (9, 10, 13) for value in candidate):
            score = sum(value == 32 or 97 <= value <= 122 for value in candidate)
            if best is None or score > best[0]:
                best = (score, byte, candidate)
    return None if best is None else (f"xor:0x{best[1]:02x}", best[2])


def _armoured(text: str, keys: tuple[bytes, ...]) -> tuple[str, bytes] | None:
    """Hex or base64 text, accepted only when what it decodes to makes sense."""
    compact = "".join(text.split())
    candidates: list[tuple[str, bytes]] = []
    hexed = compact[2:] if compact[:2].lower() == "0x" else compact.replace(":", "")
    if len(hexed) >= 16 and _HEX.fullmatch(hexed):
        candidates.append(("hex", bytes.fromhex(hexed)))
    if len(compact) >= 16:
        for name, pattern, alt in (("base64", _B64, None), ("base64url", _B64_URL, b"-_")):
            if pattern.fullmatch(compact):
                padded = compact.rstrip("=")
                padded += "=" * (-len(padded) % 4)
                try:
                    candidates.append((name, base64.b64decode(padded, altchars=alt, validate=True)))
                except (binascii.Error, ValueError):
                    continue
                break
    for name, raw in candidates:
        if _plausible(raw) or _xor_layer(raw, keys) is not None:
            return name, raw
    return None


def _step(data: bytes, keys: tuple[bytes, ...]) -> tuple[str, bytes] | None:
    if (packed := _decompress(data)) is not None:
        return packed
    if not _is_text(data):
        return _xor_layer(data, keys)
    text = data.decode("utf-8").strip()
    if (armoured := _armoured(text, keys)) is not None:
        return armoured
    if re.search(r"%[0-9A-Fa-f]{2}", text):
        decoded = unquote(text)
        if decoded != text:
            return "percent", decoded.encode()
    return None


def peel(value: str | bytes, keys: Iterable[bytes | str] = (), max_depth: int = 8) -> Decoded:
    """Remove recognised layers until the payload is plain or nothing else applies."""
    data = value.encode() if isinstance(value, str) else bytes(value)
    if len(data) > MAX_INPUT:
        raise DecodeError("payload exceeds the 4 MB input limit")
    known = tuple(key.encode() if isinstance(key, str) else key for key in keys if key)
    layers: list[str] = []
    for _ in range(max_depth):
        step = _step(data, known)
        if step is None:
            break
        layers.append(step[0])
        data = step[1]
    return Decoded(data, tuple(layers))


def candidate_keys(text: str, limit: int = 20) -> list[str]:
    """Key material that tooling leaked next to a telling name, such as xor_key=..."""
    found: list[str] = []
    for match in _KEY_HINT.finditer(text[:MAX_INPUT]):
        if match.group(1) not in found:
            found.append(match.group(1))
            if len(found) >= limit:
                break
    return found


def _field(row: Mapping[str, Any], role: str) -> Any:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in _ENVELOPE[role]:
        if lowered.get(name) not in (None, ""):
            return lowered[name]
    return None


def _checksum_matches(expected: Any, candidates: list[bytes]) -> bool:
    text = str(expected).strip().lower().removeprefix("0x")
    if not text:
        return False
    for data in candidates:
        digests = [
            f"{zlib.crc32(data) & 0xFFFFFFFF:08x}",
            f"{zlib.adler32(data) & 0xFFFFFFFF:08x}",
            str(zlib.crc32(data) & 0xFFFFFFFF),
        ]
        hashes = [hashlib.new(name, data).hexdigest() for name in ("md5", "sha1", "sha256")]
        if text in digests or any(len(text) >= 8 and digest.startswith(text) for digest in hashes):
            return True
    return False


def reassemble(rows: Iterable[Mapping[str, Any]], keys: Iterable[bytes | str] = ()) -> list[Stream]:
    """Group numbered chunks by channel, order them and decode each whole message.

    A row counts as a chunk when it carries a sequence number and a payload
    under any of the usual field names. Rows without them are ignored.
    """
    known = tuple(keys)
    groups: dict[str, list[tuple[int, str, Any, Mapping[str, Any]]]] = {}
    for row in rows:
        payload, seq = _field(row, "payload"), _field(row, "seq")
        try:
            number = int(str(seq))
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, str):
            continue
        channel = str(_field(row, "channel") or "")
        groups.setdefault(channel, []).append((number, payload, _field(row, "checksum"), row))
    streams = []
    for channel, parts in sorted(groups.items()):
        parts.sort(key=lambda part: part[0])
        numbers = [part[0] for part in parts]
        expected = range(min(numbers), max(numbers) + 1)
        joined = peel("".join(part[1] for part in parts), known)
        per_chunk = [peel(part[1], known) for part in parts]
        separately = peel(b"".join(chunk.data for chunk in per_chunk), known)
        separately = Decoded(separately.data, ("per-chunk",) + separately.layers)
        # Chunks are either slices of one encoded string or encoded one by one.
        one_by_one = (
            all(chunk.layers for chunk in per_chunk)
            and separately.text is not None
            and (not joined.layers or joined.text is None)
        )
        decoded = separately if one_by_one else joined
        declared = [
            (part, chunk)
            for part, chunk in zip(parts, per_chunk, strict=True)
            if part[2] is not None
        ]
        if not declared:
            checksum = "absent"
        else:
            whole = [decoded.data, "".join(part[1] for part in parts).encode()]
            matches = all(
                _checksum_matches(part[2], [part[1].encode(), chunk.data] + whole)
                for part, chunk in declared
            )
            checksum = "ok" if matches else "mismatch"
        streams.append(
            Stream(
                channel=channel,
                type=str(_field(parts[0][3], "type") or ""),
                parts=len(parts),
                missing=tuple(number for number in expected if number not in numbers),
                checksum=checksum,
                decoded=decoded,
                sources=tuple(part[3] for part in parts),
            )
        )
    return streams
