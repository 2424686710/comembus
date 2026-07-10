# CoMemBus v1.5 Memory Quality and Binary Embedding Methodology

## Binary embedding representation

`EmbeddingBinaryCodec` encodes each finite vector value as little-endian IEEE-754 float32. A vector with dimension `d` therefore occupies exactly `4*d` bytes. Decode rejects non-positive dimensions, incorrect payload lengths, and non-finite values. Float32 is lossy relative to Python float64, so tests use numeric tolerance and cosine preservation rather than byte equality with the original Python list.

`SharedEmbeddingStore` writes encoded bytes through the existing `SharedMemoryObjectStore`. Its shared `EmbeddingRef` contains:

- the full `ObjectRef`
- dimension
- `dtype=float32`
- SHA-256 checksum

The receiver attaches to the named segment and hashes a memoryview. `struct.iter_unpack` decodes from that buffer directly; it does not first call `bytes(view)`. The context manager releases the view before closing the SharedMemory handle. Creation and benchmark paths unlink in `finally` blocks.

The old `HashEmbeddingEncoder`, JSON `EmbeddingState`, and legacy JSON `EmbeddingRef` remain unchanged. Callers can therefore keep the old representation or fall back when shared memory is unavailable.

## Codec benchmark

The benchmark covers dimensions 32, 64, 128, 384, and 768 with 3 warmups and 30 formal rounds per mode/dimension. Modes are:

| Mode | Data representation |
|---|---|
| `summary_text` | Short deterministic natural-language summary; no recoverable vector |
| `embedding_json` | Full Python float array serialized in JSON |
| `embedding_float32` | Float32 bytes, base64-wrapped for the existing JSON UDS frame |
| `embedding_ref` | Float32 bytes in Shared Memory; only reference metadata on UDS |

`payload_bytes` measures the representation itself. `wire_bytes` is recorded by the UDS MetricsRecorder and includes the real JSON frame plus 4-byte header. `shm_bytes` is separate. Encode/decode latency uses `perf_counter_ns`. All payloads have an independently checked checksum; vector modes also require cosine similarity of at least 0.999999.

The current run produced mean wire bytes of 181.6 for summary text, 5893.0 for JSON vectors, 1620.4 for direct float32, and 389.8 for shared refs. These are averages across all dimensions. The correct interpretation is conditional: summary text can be the smallest when downstream work does not need the vector; float32/ref are preferable when the actual numeric state must be preserved. A fixed-size reference has overhead and is not automatically better than a short string.

## Memory lifecycle and provenance

The content hash is SHA-256 over exact UTF-8 content. A write with an existing hash returns the existing MemoryUnit and does not create another row. This intentionally deduplicates even when a caller changes summary or metadata around identical content.

Validity rules for default reuse are:

```text
valid_from <= query_time
and (expires_at is null or query_time < expires_at)
and superseded_by is empty
```

Direct `get_memory()` and audit listing may still read inactive records. Search and quality ranking only use reusable records.

Provenance can record source task, source agent, evidence memory IDs, derivation method, timestamp, and extra metadata. `parent_memory_ids` and `version` represent lineage; `superseded_by` points from an obsolete or contradicted memory to its replacement.

## Quality corpus

The labeled corpus includes:

- correct database wrong-port, credential ownership, and WAL/disk strategies;
- cross-family negatives;
- same-family hard negatives such as pool saturation versus wrong port, SELinux versus file ownership, and quota versus full disk;
- an expired high-confidence database policy;
- a contradictory DNS diagnosis superseded by a validated wrong-port memory;
- a duplicate content write used to verify hash deduplication.

Each query has explicit relevant memory IDs and stale IDs. All four methods receive the same corpus and query labels.

## Ranking methods

- `keyword_only`: weighted token overlap, with summary hits weighted twice.
- `tag_only`: normalized tag intersection.
- `hash_embedding_only`: cosine similarity from the retained `HashEmbeddingEncoder`.
- `hybrid`: normalized tag, keyword, hash-embedding, and confidence signals. Specific tags receive the largest weight to distinguish same-family root causes.

Hybrid is compared against the best single method with an absolute MRR tolerance of 0.01.

## Metrics

For every query and method:

- Precision@k is relevant results in the first `k`, divided by `k`.
- Recall@k is relevant results in the first `k`, divided by all labeled relevant memories.
- Reciprocal rank is `1/rank` of the first relevant result; MRR is its mean.
- `wrong_reuse_rate` is the fraction of queries whose top result exists but is not relevant.
- `stale_memory_rejection_rate` is the fraction of labeled expired/superseded IDs absent from results.
- `task_success_rate` is the fraction with a relevant top-1 result.
- `query_latency_ms` is measured end-to-end ranking latency.

The wrong reuse metric is intentionally distinct from hit rate: returning a plausible but incorrect hard negative is counted as a failure, not a successful memory hit.
