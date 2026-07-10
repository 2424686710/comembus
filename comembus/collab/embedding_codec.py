"""Portable float32 binary codec for non-text embedding exchange."""

from __future__ import annotations

from hashlib import sha256
import math
import struct
from typing import Iterable, List


class EmbeddingCodecError(ValueError):
    """Raised when an embedding binary payload is invalid."""


class EmbeddingBinaryCodec:
    """Encode embeddings as little-endian IEEE-754 float32 values."""

    dtype = "float32"
    byte_order = "little"
    item_size = 4

    @classmethod
    def encode_float32(cls, vector: Iterable[float]) -> bytes:
        values = [float(value) for value in vector]
        if not values:
            raise EmbeddingCodecError("vector must not be empty")
        if not all(math.isfinite(value) for value in values):
            raise EmbeddingCodecError("vector values must be finite")
        try:
            return struct.pack(f"<{len(values)}f", *values)
        except (OverflowError, struct.error) as exc:
            raise EmbeddingCodecError("vector cannot be represented as float32") from exc

    @classmethod
    def decode_float32(
        cls,
        data: bytes | bytearray | memoryview,
        dim: int,
    ) -> List[float]:
        if not isinstance(dim, int) or dim <= 0:
            raise EmbeddingCodecError("dim must be a positive integer")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must support the buffer protocol")
        expected_size = dim * cls.item_size
        if len(data) != expected_size:
            raise EmbeddingCodecError(
                f"float32 payload size mismatch: {len(data)} != {expected_size}"
            )
        try:
            values = [item[0] for item in struct.iter_unpack("<f", data)]
        except struct.error as exc:
            raise EmbeddingCodecError("invalid float32 payload") from exc
        if not all(math.isfinite(value) for value in values):
            raise EmbeddingCodecError("decoded vector contains non-finite values")
        return values

    @staticmethod
    def checksum(data: bytes | bytearray | memoryview) -> str:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must support the buffer protocol")
        return sha256(data).hexdigest()


def encode_float32(vector: Iterable[float]) -> bytes:
    return EmbeddingBinaryCodec.encode_float32(vector)


def decode_float32(
    data: bytes | bytearray | memoryview,
    dim: int,
) -> List[float]:
    return EmbeddingBinaryCodec.decode_float32(data, dim)
