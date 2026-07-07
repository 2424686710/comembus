# CoMemBus Result Summary

## Transport Benchmark Summary
- direct_uds: avg_latency_ms=36.826, checksum_ok_rate=1.0000
- shm_ref: avg_latency_ms=5.958, checksum_ok_rate=1.0000
- adaptive: avg_latency_ms=5.621, checksum_ok_rate=1.0000

## StatePatch Benchmark Summary
- small: full_state_bytes=1136, patch_bytes=214, reduction_ratio=0.188380
- medium: full_state_bytes=7437, patch_bytes=215, reduction_ratio=0.028910
- large: full_state_bytes=70436, patch_bytes=214, reduction_ratio=0.003038

## Memory Reuse Benchmark Summary
- memory_hit_count=7
- memory_hit_rate=0.7000
- total_saved_steps=12

## Collaboration Mode Benchmark Summary
- text_mode total_tokens=655654
- structured_mode total_tokens=10021
- token_saving_ratio=0.984716
- latency_saving_ratio=0.484606
- structured_mode memory_hit_rate=0.7000
- total_saved_steps=12
- embedding_state_count=10
- capability_discovery_count=30
- scenario_families=database_timeout,permission_denied,storage_full

## Competition Requirement Mapping
- 低开销通信: transport benchmark 对比 direct_uds、shm_ref、adaptive。
- 非文本状态传递: StatePatch 与 embedding_state 记录结构化状态和语义向量。
- 共享记忆复用: memory reuse benchmark 统计 memory_hit_rate 与 total_saved_steps。
- 纯文本 vs 结构化协议对比: collaboration benchmark 统计 token_saving_ratio。
- 10 轮连续任务: 当前 collaboration rows=20。
