"""Lightweight hash-based semantic embeddings."""

from __future__ import annotations

import hashlib
import math
import re
from typing import List

TOKEN_PATTERN = re.compile(r"[a-z0-9_]+")


class HashEmbeddingEncoder:
    """Encode text into a fixed-dimension hashed bag-of-words vector."""

    def __init__(self, dim: int = 128) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim

    def tokenize(self, text: str) -> List[str]:
        return TOKEN_PATTERN.findall(text.lower())

    def encode(self, text: str) -> List[float]:
        tokens = self.tokenize(text)
        vector = [0.0] * self.dim
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if len(a) != len(b):
        raise ValueError("vectors must have the same dimension")
    if not a:
        return 0.0

    dot_product = sum(left * right for left, right in zip(a, b))
    norm_a = math.sqrt(sum(value * value for value in a))
    norm_b = math.sqrt(sum(value * value for value in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot_product / (norm_a * norm_b)

