"""Comparable keyword, tag, hash-embedding, and hybrid memory rankers."""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence

from .embedding import HashEmbeddingEncoder, cosine_similarity
from .unit import MemorySearchResult, MemoryUnit


KEYWORD_ONLY = "keyword_only"
TAG_ONLY = "tag_only"
HASH_EMBEDDING_ONLY = "hash_embedding_only"
HYBRID = "hybrid"
RANKING_METHODS = (KEYWORD_ONLY, TAG_ONLY, HASH_EMBEDDING_ONLY, HYBRID)


class MemoryRanker:
    """Rank the same active corpus with one of four deterministic methods."""

    def __init__(self, embedding_dim: int = 128) -> None:
        self.encoder = HashEmbeddingEncoder(dim=embedding_dim)

    def rank(
        self,
        method: str,
        query: str,
        tags: Sequence[str],
        memories: Iterable[MemoryUnit],
        top_k: int = 5,
        at_time: float | None = None,
    ) -> List[MemorySearchResult]:
        if method not in RANKING_METHODS:
            raise ValueError(f"unsupported ranking method: {method}")
        if top_k <= 0:
            return []
        candidates = [
            memory for memory in memories if memory.is_reusable(at_time=at_time)
        ]
        if not candidates:
            return []
        keyword_scores = self._keyword_scores(query, candidates)
        tag_scores = self._tag_scores(tags, candidates)
        semantic_scores = self._semantic_scores(query, candidates)

        if method == KEYWORD_ONLY:
            scores = keyword_scores
        elif method == TAG_ONLY:
            scores = tag_scores
        elif method == HASH_EMBEDDING_ONLY:
            scores = semantic_scores
        else:
            scores = self._hybrid_scores(
                candidates, keyword_scores, tag_scores, semantic_scores
            )

        results: List[MemorySearchResult] = []
        for memory in candidates:
            score = scores[memory.memory_id]
            if score <= 0.0:
                continue
            reason = self._reason(
                method,
                keyword_scores[memory.memory_id],
                tag_scores[memory.memory_id],
                semantic_scores[memory.memory_id],
            )
            results.append(MemorySearchResult(memory=memory, score=score, reason=reason))
        results.sort(
            key=lambda item: (
                -item.score,
                -item.memory.version,
                -item.memory.confidence,
                item.memory.created_at,
                item.memory.memory_id,
            )
        )
        return results[:top_k]

    def _keyword_scores(
        self, query: str, memories: Sequence[MemoryUnit]
    ) -> Dict[str, float]:
        query_tokens = set(self.encoder.tokenize(query))
        scores: Dict[str, float] = {}
        for memory in memories:
            summary_hits = query_tokens & set(self.encoder.tokenize(memory.summary))
            content_hits = query_tokens & set(self.encoder.tokenize(memory.content))
            scores[memory.memory_id] = (2.0 * len(summary_hits)) + len(content_hits)
        return scores

    @staticmethod
    def _tag_scores(
        tags: Sequence[str], memories: Sequence[MemoryUnit]
    ) -> Dict[str, float]:
        query_tags = {tag.strip().lower() for tag in tags if tag.strip()}
        return {
            memory.memory_id: 3.0
            * len(query_tags & {tag.lower() for tag in memory.tags})
            for memory in memories
        }

    def _semantic_scores(
        self, query: str, memories: Sequence[MemoryUnit]
    ) -> Dict[str, float]:
        query_embedding = self.encoder.encode(query)
        scores: Dict[str, float] = {}
        for memory in memories:
            memory_embedding = self.encoder.encode(
                f"{memory.summary}\n{memory.content}"
            )
            scores[memory.memory_id] = max(
                0.0, cosine_similarity(query_embedding, memory_embedding)
            )
        return scores

    @staticmethod
    def _hybrid_scores(
        memories: Sequence[MemoryUnit],
        keyword_scores: Dict[str, float],
        tag_scores: Dict[str, float],
        semantic_scores: Dict[str, float],
    ) -> Dict[str, float]:
        keyword_max = max(keyword_scores.values(), default=0.0)
        tag_max = max(tag_scores.values(), default=0.0)
        semantic_max = max(semantic_scores.values(), default=0.0)
        scores: Dict[str, float] = {}
        for memory in memories:
            keyword = (
                keyword_scores[memory.memory_id] / keyword_max if keyword_max else 0.0
            )
            tag = tag_scores[memory.memory_id] / tag_max if tag_max else 0.0
            semantic = (
                semantic_scores[memory.memory_id] / semantic_max
                if semantic_max
                else 0.0
            )
            # Specific tags dominate hard negatives; lexical and semantic signals
            # break ties and still work when no tags are supplied.
            weights = (0.15, 0.75, 0.10) if tag_max else (0.55, 0.0, 0.45)
            confidence_bonus = max(0.0, min(1.0, memory.confidence)) * 0.01
            scores[memory.memory_id] = (
                weights[0] * keyword
                + weights[1] * tag
                + weights[2] * semantic
                + confidence_bonus
            )
        return scores

    @staticmethod
    def _reason(method: str, keyword: float, tag: float, semantic: float) -> str:
        if method == KEYWORD_ONLY:
            return f"keyword({keyword:.4f})"
        if method == TAG_ONLY:
            return f"tag({tag:.4f})"
        if method == HASH_EMBEDDING_ONLY:
            return f"hash_embedding({semantic:.4f})"
        return (
            f"hybrid(keyword={keyword:.4f},tag={tag:.4f},"
            f"hash_embedding={semantic:.4f})"
        )
