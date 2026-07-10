"""Empirical calibration for CoMemBus direct UDS versus shared memory refs."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import socket
import threading
import time
from typing import Any, Dict, Iterable, List, Sequence

from ..metrics.process_metrics import ProcessMetrics, ProcessUsage
from ..metrics.recorder import MetricsRecorder, MetricsSnapshot
from ..metrics.statistics import summarize
from ..object_store.shm_store import ObjectStoreError, SharedMemoryObjectStore
from ..protocol import ObjectRef
from .. import protocol as protocol_module
from .adaptive import DIRECT_UDS, SHM_REF
from .uds import recv_frame, send_frame


DEFAULT_SIZES = (
    1024,
    4 * 1024,
    16 * 1024,
    64 * 1024,
    256 * 1024,
    1024 * 1024,
    8 * 1024 * 1024,
)
DEFAULT_RECEIVERS = (1, 2, 4, 8)
DEFAULT_WARMUP = 3
DEFAULT_ROUNDS = 20
DEFAULT_RANDOM_SEED = 20260710


@dataclass(frozen=True)
class TransportMeasurement:
    selected_mode: str
    size_bytes: int
    receivers: int
    round: int
    latency_ms: float
    cpu_time_ms: float
    peak_rss_kb: int
    voluntary_context_switches: int | None
    involuntary_context_switches: int | None
    sent_bytes: int
    received_bytes: int
    wire_bytes: int
    shm_bytes_written: int
    shm_bytes_read: int
    message_count: int
    throughput_mib_s: float
    checksum_ok: bool

    def to_dict(self) -> Dict[str, object]:
        return {
            "selected_mode": self.selected_mode,
            "size_bytes": self.size_bytes,
            "receivers": self.receivers,
            "round": self.round,
            "latency_ms": self.latency_ms,
            "cpu_time_ms": self.cpu_time_ms,
            "peak_rss_kb": self.peak_rss_kb,
            "voluntary_context_switches": self.voluntary_context_switches,
            "involuntary_context_switches": self.involuntary_context_switches,
            "sent_bytes": self.sent_bytes,
            "received_bytes": self.received_bytes,
            "wire_bytes": self.wire_bytes,
            "shm_bytes_written": self.shm_bytes_written,
            "shm_bytes_read": self.shm_bytes_read,
            "message_count": self.message_count,
            "throughput_mib_s": self.throughput_mib_s,
            "checksum_ok": self.checksum_ok,
        }


def deterministic_payload(size_bytes: int, seed: int, round_index: int) -> bytes:
    if size_bytes <= 0:
        raise ValueError("size_bytes must be positive")
    block = sha256(f"{seed}:{size_bytes}:{round_index}".encode("ascii")).digest()
    return (block * ((size_bytes // len(block)) + 1))[:size_bytes]


def measure_transport_once(
    selected_mode: str,
    data: bytes,
    receivers: int,
    round_index: int,
) -> TransportMeasurement:
    """Measure one checksum-verified end-to-end fan-out with real frame bytes."""

    if selected_mode not in {DIRECT_UDS, SHM_REF}:
        raise ValueError(f"unsupported transport mode: {selected_mode}")
    if receivers <= 0:
        raise ValueError("receivers must be positive")
    if not data:
        raise ValueError("data must not be empty")

    recorder = MetricsRecorder()
    store = SharedMemoryObjectStore(recorder)
    process = ProcessMetrics().start()
    started = time.perf_counter()
    ref = None
    checksum_ok = False
    try:
        checksum = sha256(data).hexdigest()
        if selected_mode == DIRECT_UDS:
            payload = {
                "transport": DIRECT_UDS,
                "size_bytes": len(data),
                "checksum": checksum,
                "data_b64": base64.b64encode(data).decode("ascii"),
            }
            received = _transmit_to_receivers(payload, receivers, recorder)
            checksum_ok = all(
                sha256(base64.b64decode(item["data_b64"].encode("ascii"))).hexdigest()
                == checksum
                for item in received
            )
        else:
            ref = store.put_bytes(data)
            payload = {
                "transport": SHM_REF,
                "size_bytes": len(data),
                "object_ref": ref.to_dict(),
            }
            received = _transmit_to_receivers(payload, receivers, recorder)
            restored = [
                store.get_bytes(ObjectRef.from_dict(item["object_ref"]))
                for item in received
            ]
            checksum_ok = all(sha256(item).hexdigest() == checksum for item in restored)
    finally:
        if ref is not None:
            try:
                store.unlink(ref)
            except ObjectStoreError:
                pass
        latency_ms = (time.perf_counter() - started) * 1000.0
        usage = process.stop()
        snapshot = recorder.snapshot()

    delivered_bytes = len(data) * receivers
    throughput = 0.0
    if latency_ms > 0.0:
        throughput = (delivered_bytes / (1024.0 * 1024.0)) / (latency_ms / 1000.0)
    return _measurement_from_parts(
        selected_mode=selected_mode,
        size_bytes=len(data),
        receivers=receivers,
        round_index=round_index,
        latency_ms=latency_ms,
        usage=usage,
        snapshot=snapshot,
        throughput_mib_s=throughput,
        checksum_ok=checksum_ok,
    )


class AdaptiveTransportCalibrator:
    """Find an empirical direct-to-SHM crossover for each receiver count."""

    def __init__(
        self,
        sizes: Sequence[int] = DEFAULT_SIZES,
        receivers: Sequence[int] = DEFAULT_RECEIVERS,
        warmup: int = DEFAULT_WARMUP,
        rounds: int = DEFAULT_ROUNDS,
        random_seed: int = DEFAULT_RANDOM_SEED,
    ) -> None:
        self.sizes = tuple(int(value) for value in sizes)
        self.receivers = tuple(int(value) for value in receivers)
        self.warmup = int(warmup)
        self.rounds = int(rounds)
        self.random_seed = int(random_seed)
        if not self.sizes or any(value <= 0 for value in self.sizes):
            raise ValueError("sizes must contain positive values")
        if not self.receivers or any(value <= 0 for value in self.receivers):
            raise ValueError("receivers must contain positive values")
        if self.warmup < 0:
            raise ValueError("warmup must be non-negative")
        if self.rounds <= 0:
            raise ValueError("rounds must be positive")

    def calibrate(self, output_path: str | Path = "results/transport_profile.json") -> Dict[str, Any]:
        summaries: List[Dict[str, object]] = []
        means: Dict[tuple[int, int, str], float] = {}
        for receiver_count in self.receivers:
            for size_bytes in self.sizes:
                for warmup_index in range(self.warmup):
                    data = deterministic_payload(
                        size_bytes, self.random_seed, -(warmup_index + 1)
                    )
                    for selected_mode in (DIRECT_UDS, SHM_REF):
                        result = measure_transport_once(
                            selected_mode, data, receiver_count, -(warmup_index + 1)
                        )
                        if not result.checksum_ok:
                            raise RuntimeError("transport calibration warmup checksum failed")

                mode_results: Dict[str, List[TransportMeasurement]] = {
                    DIRECT_UDS: [],
                    SHM_REF: [],
                }
                for round_index in range(self.rounds):
                    data = deterministic_payload(size_bytes, self.random_seed, round_index)
                    for selected_mode in (DIRECT_UDS, SHM_REF):
                        result = measure_transport_once(
                            selected_mode, data, receiver_count, round_index + 1
                        )
                        if not result.checksum_ok:
                            raise RuntimeError("transport calibration checksum failed")
                        mode_results[selected_mode].append(result)

                for selected_mode, results in mode_results.items():
                    latency = summarize([item.latency_ms for item in results])
                    means[(receiver_count, size_bytes, selected_mode)] = latency["mean"]
                    summaries.append(
                        {
                            "selected_mode": selected_mode,
                            "size_bytes": size_bytes,
                            "receivers": receiver_count,
                            "rounds": self.rounds,
                            "latency_ms": latency,
                            "mean_wire_bytes": sum(item.wire_bytes for item in results)
                            / len(results),
                            "mean_throughput_mib_s": sum(
                                item.throughput_mib_s for item in results
                            )
                            / len(results),
                        }
                    )

        thresholds = {
            str(receiver_count): self._crossover(receiver_count, means)
            for receiver_count in self.receivers
        }
        profile: Dict[str, Any] = {
            "profile_version": 1,
            "method": "measured_direct_uds_vs_shm_ref",
            "random_seed": self.random_seed,
            "warmup": self.warmup,
            "rounds": self.rounds,
            "sizes_bytes": list(self.sizes),
            "receivers": list(self.receivers),
            "fallback_direct_threshold_bytes": 64 * 1024,
            "thresholds_by_receivers": thresholds,
            "measurements": summaries,
        }
        _write_json(output_path, profile)
        return profile

    def _crossover(
        self,
        receivers: int,
        means: Dict[tuple[int, int, str], float],
    ) -> int:
        for size_bytes in sorted(self.sizes):
            direct = means[(receivers, size_bytes, DIRECT_UDS)]
            shared = means[(receivers, size_bytes, SHM_REF)]
            if shared <= direct:
                return size_bytes
        return max(self.sizes) + 1


def _transmit_to_receivers(
    payload: Dict[str, object],
    receivers: int,
    recorder: MetricsRecorder,
) -> List[Dict[str, Any]]:
    encoded_payload_bytes = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    previous_frame_limit = protocol_module.MAX_FRAME_SIZE
    protocol_module.MAX_FRAME_SIZE = max(
        protocol_module.MAX_FRAME_SIZE, encoded_payload_bytes + 4096
    )
    try:
        pairs = [socket.socketpair() for _ in range(receivers)]
        received: List[Dict[str, Any]] = []
        errors: List[BaseException] = []
        result_lock = threading.Lock()

        def receive_one(sock: socket.socket) -> None:
            try:
                item = recv_frame(sock, recorder)
                with result_lock:
                    received.append(item)
            except BaseException as exc:  # Propagate receiver failures to the benchmark thread.
                with result_lock:
                    errors.append(exc)
            finally:
                sock.close()

        threads = [
            threading.Thread(target=receive_one, args=(receiver_sock,))
            for _, receiver_sock in pairs
        ]
        for thread in threads:
            thread.start()
        try:
            for sender_sock, _ in pairs:
                send_frame(sender_sock, payload, recorder)
                sender_sock.close()
        finally:
            for sender_sock, _ in pairs:
                try:
                    sender_sock.close()
                except OSError:
                    pass
            for thread in threads:
                thread.join()
        if errors:
            raise errors[0]
        if len(received) != receivers:
            raise RuntimeError("not all transport receivers produced a frame")
        return received
    finally:
        protocol_module.MAX_FRAME_SIZE = previous_frame_limit


def _measurement_from_parts(
    selected_mode: str,
    size_bytes: int,
    receivers: int,
    round_index: int,
    latency_ms: float,
    usage: ProcessUsage,
    snapshot: MetricsSnapshot,
    throughput_mib_s: float,
    checksum_ok: bool,
) -> TransportMeasurement:
    return TransportMeasurement(
        selected_mode=selected_mode,
        size_bytes=size_bytes,
        receivers=receivers,
        round=round_index,
        latency_ms=latency_ms,
        cpu_time_ms=usage.cpu_time_ms,
        peak_rss_kb=usage.peak_rss_kb,
        voluntary_context_switches=usage.voluntary_context_switches,
        involuntary_context_switches=usage.involuntary_context_switches,
        sent_bytes=snapshot.sent_bytes,
        received_bytes=snapshot.received_bytes,
        wire_bytes=snapshot.wire_bytes,
        shm_bytes_written=snapshot.shm_bytes_written,
        shm_bytes_read=snapshot.shm_bytes_read,
        message_count=snapshot.message_count,
        throughput_mib_s=throughput_mib_s,
        checksum_ok=checksum_ok,
    )


def _write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
