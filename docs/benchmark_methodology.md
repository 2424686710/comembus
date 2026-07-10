# CoMemBus v1.3 Benchmark Methodology

## Scope and compatibility

The v1.3 suite measures transport and collaboration mechanisms with Python standard-library code only. It is designed for openEuler 24.03-LTS-SP3 and does not require NumPy, pandas, pytest, a remote LLM, an API key, or network access.

The suite is additive. Existing `scripts/run_all.sh`, legacy benchmark CSV schemas, demos, and the fixed `AdaptiveTransportPolicy()` behavior remain available. New recorder arguments are optional and therefore preserve existing call sites.

## Reproducibility controls

- Default random seed: `20260710`.
- Scenario source: `examples/incident_diagnosis_mock/scenarios.jsonl`.
- Default ablation: 3 warmup runs plus 30 recorded runs for every mode/task pair.
- Transport calibration: 3 warmup runs plus 20 recorded runs for every direct/SHM size/receiver pair.
- Rigorous transport comparison: 3 warmup runs plus 30 recorded runs for every mode/size/receiver group.
- Warmup rows are never written to the formal CSV or used in percentile/CI calculations.
- Every transport payload is checksum-verified, and each ablation result must have `root_cause_correct=true`.

The deterministic payload changes by formal round but is identical across compared transport modes. All ablation modes receive identical log bytes and configuration text for a task.

## Ablation fairness

Every mode uses five agents and the same sequence:

1. Planner → Log
2. Log → Config
3. Config → Memory
4. Memory → Review
5. Review → Planner

The modes differ only in representation or one removed CoMemBus component:

| Mode | Representation or removed component |
|---|---|
| `text_full_context` | Full factual text and accumulated handoff history on every message |
| `text_summary` | Fixed deterministic summary; no LLM |
| `json_full_state` | Full `TaskState` JSON on every handoff |
| `structured_no_shm` | Inline large log object; no `ObjectRef` |
| `structured_no_patch` | Full `TaskState`; no `StatePatch` |
| `structured_no_memory` | No historical retrieval or `MemoryRef`; `saved_steps=0` |
| `structured_no_embedding` | No `EmbeddingState`/`EmbeddingRef` generation |
| `structured_no_capability` | Fixed agent map; no capability discovery |
| `structured_full` | ObjectRef + StatePatch + MemoryRef + embedding + discovery |

The text baseline has no synthetic latency penalty. Its latency is actual message construction, serialization, UDS transfer, receive, and validation time. The summary baseline can outperform structured transport for some small tasks; that is a valid result rather than a benchmark failure.

## Exact byte accounting

`wire_bytes` is `MetricsRecorder.sent_bytes`. `send_frame` serializes the message, adds the 4-byte big-endian length header, calls `sendall`, and only then records the exact frame length. `recv_frame` separately records the header plus body bytes actually read. Therefore:

- `sent_bytes == received_bytes` is an integrity check for these closed benchmark flows.
- `wire_bytes` does not double-count the receive observation.
- `message_count` counts successfully sent frames.
- `shm_bytes_written` and `shm_bytes_read` describe memory copies, not wire traffic.
- No estimated payload size is substituted for a real wire observation.

## Latency, CPU, RSS, and throughput

Wall-clock latency uses `time.perf_counter()`. CPU time is the delta from `time.process_time()`. `resource.getrusage(resource.RUSAGE_SELF)` provides `ru_maxrss`, `ru_nvcsw`, and `ru_nivcsw` when supported. On Linux/openEuler, peak RSS is reported in KiB. Because `ru_maxrss` is a process high-water mark, per-row values are real process peaks but need not decrease between rows.

Transport throughput is:

```text
(payload_size_bytes * receiver_count) / latency_seconds
```

and is reported in MiB/s. Ablation throughput uses the common large fact input size divided by end-to-end latency.

## Statistics

For the formal runs in each group, the suite reports:

- arithmetic mean
- median / p50
- p95 and p99 via linear interpolation on sorted observations
- sample standard deviation
- normal-approximation 95% confidence interval: `mean ± 1.96 * s / sqrt(n)`
- minimum and maximum

The raw latency remains present on every CSV row; group statistics are repeated on the rows for backward-friendly CSV consumption.

`estimated_tokens` is a deterministic character estimate:

```text
ceil(text_chars / 4)
```

It is explicitly labeled `character_estimate_4_chars_per_token`. It is not a tokenizer result, not a model-reported usage value, and not suitable for billing calculations.

## Adaptive transport calibration

The calibration matrix is:

- sizes: 1KB, 4KB, 16KB, 64KB, 256KB, 1MB, 8MB
- receivers: 1, 2, 4, 8

For each receiver count, the first measured size where mean SHM-ref latency is no greater than mean direct-UDS latency becomes the crossover threshold. If no crossover appears, the threshold is one byte larger than the largest measured size. The profile is written to `results/transport_profile.json` and includes the measured summaries.

`AdaptiveTransportPolicy.from_profile()` uses receiver-specific thresholds. Missing or malformed profiles fall back to the legacy fixed policy: direct UDS only below 64KB with one receiver, otherwise SHM ref.

## Commands and artifacts

```bash
bash scripts/run_tests.sh
bash scripts/run_ablation_bench.sh
bash scripts/run_rigorous_bench.sh
```

The two benchmark scripts generate:

- `results/ablation_bench.csv`
- `results/rigorous_transport.csv`
- `results/transport_profile.json`
- `results/rigorous_summary.md`
- `results/rigorous_metrics.json`
- four SVGs under `results/figures/`

After every run, `/dev/shm` should contain no `comembus_*` object. All object allocation paths use `finally` cleanup; the acceptance run performs an explicit residue check.
