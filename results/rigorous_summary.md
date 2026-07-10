# CoMemBus v1.3 rigorous benchmark summary

- Ablation rows: 3240
- Rigorous transport rows: 3360
- Token metric: deterministic character estimate at 4 characters/token; it is not a model-reported token count.
- Wire bytes: exact bytes recorded by `send_frame`, including each 4-byte frame header.
- Shared-memory bytes are reported separately from UDS wire bytes.

## Ablation modes

| mode | rows | correct | mean ms | p50 | p95 | p99 | mean wire bytes | mean state bytes | saved steps |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| text_full_context | 360 | 1.000 | 4.890 | 4.414 | 7.111 | 7.518 | 1333830.0 | 0.0 | 480 |
| text_summary | 360 | 1.000 | 0.267 | 0.259 | 0.309 | 0.376 | 3329.2 | 0.0 | 480 |
| json_full_state | 360 | 1.000 | 6.898 | 6.337 | 9.674 | 10.786 | 1336261.7 | 1333905.9 | 480 |
| structured_no_shm | 360 | 1.000 | 1.738 | 1.496 | 2.529 | 2.796 | 271382.7 | 2291.4 | 480 |
| structured_no_patch | 360 | 1.000 | 1.033 | 1.000 | 1.342 | 1.607 | 9805.7 | 6415.7 | 480 |
| structured_no_memory | 360 | 1.000 | 1.001 | 0.967 | 1.262 | 1.601 | 5835.1 | 2500.6 | 0 |
| structured_no_embedding | 360 | 1.000 | 0.930 | 0.899 | 1.115 | 1.505 | 5391.1 | 2253.6 | 480 |
| structured_no_capability | 360 | 1.000 | 0.965 | 0.916 | 1.382 | 1.486 | 4552.1 | 2534.1 | 480 |
| structured_full | 360 | 1.000 | 0.998 | 0.955 | 1.274 | 1.611 | 5863.2 | 2489.1 | 480 |

## Transport modes

| mode | rows | checksum | mean ms | p50 | p95 | p99 | throughput MiB/s | mean wire bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| direct_uds | 840 | 1.000 | 33.297 | 1.424 | 217.241 | 408.215 | 112.582 | 6990802.5 |
| shm_ref | 840 | 1.000 | 7.295 | 0.811 | 45.473 | 82.042 | 336.346 | 1074.7 |
| fixed_adaptive | 840 | 1.000 | 7.284 | 0.733 | 45.425 | 82.550 | 335.682 | 2083.6 |
| calibrated_adaptive | 840 | 1.000 | 7.305 | 0.770 | 45.335 | 82.284 | 337.112 | 4580.7 |

## Acceptance checks

- `all_root_causes_correct`: PASS
- `structured_full_wire_lower_than_text_full_context`: PASS
- `no_shm_wire_bytes_increase`: PASS
- `no_patch_state_bytes_increase`: PASS
- `no_memory_saved_steps_zero`: PASS
- `calibrated_within_five_percent_of_fixed`: PASS
- `all_transport_checksums_ok`: PASS
