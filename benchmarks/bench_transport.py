#!/usr/bin/env python3
"""Benchmark CoMemBus direct UDS payloads versus shared-memory object refs."""

from __future__ import annotations

import argparse
import base64
import csv
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Iterable, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.client import AgentBusClient
from comembus.protocol import Message, ObjectRef, encode_frame
import comembus.protocol as protocol_module
from comembus.server import AgentBusServer
from comembus.transport.adaptive import (
    AdaptiveTransportPolicy,
    DIRECT_UDS,
    SHM_REF,
)


CSV_FIELDS = [
    "mode",
    "selected_mode",
    "size_bytes",
    "receivers",
    "round",
    "latency_ms",
    "uds_payload_bytes",
    "shm_bytes_written",
    "checksum_ok",
]


@dataclass(frozen=True)
class BenchmarkResult:
    mode: str
    selected_mode: str
    size_bytes: int
    receivers: int
    round: int
    latency_ms: float
    uds_payload_bytes: int
    shm_bytes_written: int
    checksum_ok: bool

    def to_csv_row(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "selected_mode": self.selected_mode,
            "size_bytes": self.size_bytes,
            "receivers": self.receivers,
            "round": self.round,
            "latency_ms": f"{self.latency_ms:.3f}",
            "uds_payload_bytes": self.uds_payload_bytes,
            "shm_bytes_written": self.shm_bytes_written,
            "checksum_ok": str(self.checksum_ok).lower(),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        default="1KB,16KB,64KB,1MB,8MB",
        help="Comma-separated payload sizes, for example: 1KB,16KB,64KB,1MB,8MB",
    )
    parser.add_argument(
        "--receivers",
        default="1,2,4",
        help="Comma-separated receiver counts, for example: 1,2,4",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=10,
        help="Benchmark rounds per mode/size/receiver combination",
    )
    parser.add_argument(
        "--output",
        default="results/transport_bench.csv",
        help="CSV output path",
    )
    parser.add_argument(
        "--modes",
        default="direct_uds,shm_ref,adaptive",
        help="Comma-separated benchmark modes, for example: direct_uds,shm_ref,adaptive",
    )
    return parser.parse_args()


def parse_sizes(spec: str) -> List[int]:
    sizes: List[int] = []
    for token in split_csv(spec):
        sizes.append(parse_size(token))
    return sizes


def parse_receivers(spec: str) -> List[int]:
    receivers: List[int] = []
    for token in split_csv(spec):
        value = int(token)
        if value <= 0:
            raise ValueError("receiver count must be positive")
        receivers.append(value)
    return receivers


def parse_modes(spec: str) -> List[str]:
    allowed = {DIRECT_UDS, SHM_REF, "adaptive"}
    modes = split_csv(spec)
    for mode in modes:
        if mode not in allowed:
            raise ValueError(f"unsupported mode: {mode}")
    return modes


def split_csv(spec: str) -> List[str]:
    tokens = [token.strip() for token in spec.split(",") if token.strip()]
    if not tokens:
        raise ValueError("expected at least one comma-separated value")
    return tokens


def parse_size(token: str) -> int:
    normalized = token.strip().upper()
    suffixes = [
        ("MB", 1024 * 1024),
        ("KB", 1024),
        ("B", 1),
    ]
    for suffix, multiplier in suffixes:
        if normalized.endswith(suffix):
            number = normalized[: -len(suffix)]
            break
    else:
        raise ValueError(f"unsupported size token: {token}")

    try:
        value = int(number)
    except ValueError as exc:
        raise ValueError(f"invalid size token: {token}") from exc
    if value <= 0:
        raise ValueError(f"size must be positive: {token}")
    return value * multiplier


def ensure_frame_limit(max_payload_size: int) -> None:
    # direct_uds serializes the full payload into the existing JSON frame format.
    required = (max_payload_size * 2) + 1024
    protocol_module.MAX_FRAME_SIZE = max(required, protocol_module.MAX_FRAME_SIZE)


def make_payload(size_bytes: int, round_index: int) -> bytes:
    seed = sha256(f"comembus:{size_bytes}:{round_index}".encode("utf-8")).digest()
    chunks = bytearray()
    while len(chunks) < size_bytes:
        seed = sha256(seed).digest()
        chunks.extend(seed)
    return bytes(chunks[:size_bytes])


def build_direct_payload(data: bytes, checksum: str) -> dict[str, object]:
    return {
        "data_b64": base64.b64encode(data).decode("ascii"),
        "checksum": checksum,
        "size_bytes": len(data),
    }


def payload_frame_bytes(topic: str, payload: dict[str, object]) -> int:
    publish_frame = encode_frame(Message(type="publish", topic=topic, payload=payload).to_dict())
    poll_response_frame = encode_frame({"ok": True, "data": payload})
    return len(publish_frame) + len(poll_response_frame)


def topic_names(receiver_count: int) -> List[str]:
    return [f"logs_r{index}" for index in range(receiver_count)]


class BenchmarkHarness:
    def __init__(
        self,
        max_receivers: int,
        adaptive_policy: AdaptiveTransportPolicy | None = None,
    ) -> None:
        self._tempdir = tempfile.TemporaryDirectory(prefix="comembus-bench-")
        self.socket_path = os.path.join(self._tempdir.name, "comembus.sock")
        self.server = AgentBusServer(self.socket_path)
        self.producer: AgentBusClient | None = None
        self.receivers: List[AgentBusClient] = []
        self.max_receivers = max_receivers
        self.adaptive_policy = adaptive_policy or AdaptiveTransportPolicy()

    def __enter__(self) -> "BenchmarkHarness":
        self.server.start()
        self.producer = AgentBusClient(self.socket_path)
        self.producer.register("producer")
        self.receivers = []
        for index in range(self.max_receivers):
            client = AgentBusClient(self.socket_path)
            client.register(f"receiver_{index}")
            self.receivers.append(client)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for client in self.receivers:
            client.close()
        if self.producer is not None:
            self.producer.close()
        self.server.stop()
        self._tempdir.cleanup()

    def run_direct_round(
        self,
        mode: str,
        size_bytes: int,
        receiver_count: int,
        round_index: int,
    ) -> BenchmarkResult:
        if self.producer is None:
            raise RuntimeError("benchmark harness is not started")
        data = make_payload(size_bytes, round_index)
        checksum = sha256(data).hexdigest()
        payload = build_direct_payload(data, checksum)
        checksum_ok = True
        started = time.perf_counter()

        for topic in topic_names(receiver_count):
            self.producer.publish(topic, payload)

        for index, topic in enumerate(topic_names(receiver_count)):
            message = self._poll_until_message(self.receivers[index], topic)
            received_b64 = message["data_b64"]
            if not isinstance(received_b64, str):
                raise ValueError("direct_uds receiver got a non-string payload")
            received = base64.b64decode(received_b64.encode("ascii"))
            if sha256(received).hexdigest() != checksum:
                checksum_ok = False

        latency_ms = (time.perf_counter() - started) * 1000.0
        total_uds_payload_bytes = sum(
            payload_frame_bytes(topic, payload) for topic in topic_names(receiver_count)
        )
        return BenchmarkResult(
            mode=mode,
            selected_mode=DIRECT_UDS,
            size_bytes=size_bytes,
            receivers=receiver_count,
            round=round_index + 1,
            latency_ms=latency_ms,
            uds_payload_bytes=total_uds_payload_bytes,
            shm_bytes_written=0,
            checksum_ok=checksum_ok,
        )

    def run_shm_ref_round(
        self,
        mode: str,
        size_bytes: int,
        receiver_count: int,
        round_index: int,
    ) -> BenchmarkResult:
        if self.producer is None:
            raise RuntimeError("benchmark harness is not started")
        data = make_payload(size_bytes, round_index)
        checksum = sha256(data).hexdigest()
        ref = None
        checksum_ok = True
        started = time.perf_counter()

        try:
            ref = self.producer.object_store.put_bytes(data)
            payload = {"object_ref": ref.to_dict()}
            for topic in topic_names(receiver_count):
                self.producer.publish(topic, payload)

            for index, topic in enumerate(topic_names(receiver_count)):
                message = self._poll_until_message(self.receivers[index], topic)
                received_ref = ObjectRef.from_dict(message["object_ref"])
                restored = self.receivers[index].object_store.get_bytes(received_ref)
                if sha256(restored).hexdigest() != checksum:
                    checksum_ok = False
        finally:
            if ref is not None:
                self.producer.object_store.unlink(ref)

        latency_ms = (time.perf_counter() - started) * 1000.0
        total_uds_payload_bytes = 0
        if ref is not None:
            payload = {"object_ref": ref.to_dict()}
            total_uds_payload_bytes = sum(
                payload_frame_bytes(topic, payload)
                for topic in topic_names(receiver_count)
            )
        return BenchmarkResult(
            mode=mode,
            selected_mode=SHM_REF,
            size_bytes=size_bytes,
            receivers=receiver_count,
            round=round_index + 1,
            latency_ms=latency_ms,
            uds_payload_bytes=total_uds_payload_bytes,
            shm_bytes_written=size_bytes,
            checksum_ok=checksum_ok,
        )

    def run_adaptive_round(
        self,
        size_bytes: int,
        receiver_count: int,
        round_index: int,
    ) -> BenchmarkResult:
        selected_mode = self.adaptive_policy.choose_mode(size_bytes, receiver_count)
        if selected_mode == DIRECT_UDS:
            return self.run_direct_round(
                mode="adaptive",
                size_bytes=size_bytes,
                receiver_count=receiver_count,
                round_index=round_index,
            )
        return self.run_shm_ref_round(
            mode="adaptive",
            size_bytes=size_bytes,
            receiver_count=receiver_count,
            round_index=round_index,
        )

    @staticmethod
    def _poll_until_message(client: AgentBusClient, topic: str) -> dict[str, object]:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            message = client.poll(topic)
            if message is not None:
                return message
            time.sleep(0.001)
        raise TimeoutError(f"timed out waiting for topic: {topic}")


def run_benchmark(
    modes: Sequence[str],
    sizes: Sequence[int],
    receivers: Sequence[int],
    rounds: int,
    adaptive_policy: AdaptiveTransportPolicy | None = None,
) -> List[BenchmarkResult]:
    if rounds <= 0:
        raise ValueError("rounds must be positive")
    if not modes:
        raise ValueError("at least one mode is required")
    if not sizes:
        raise ValueError("at least one size is required")
    if not receivers:
        raise ValueError("at least one receiver count is required")

    ensure_frame_limit(max(sizes))
    results: List[BenchmarkResult] = []
    with BenchmarkHarness(max(receivers), adaptive_policy=adaptive_policy) as harness:
        for size_bytes in sizes:
            for receiver_count in receivers:
                for round_index in range(rounds):
                    for mode in modes:
                        if mode == DIRECT_UDS:
                            result = harness.run_direct_round(
                                mode=DIRECT_UDS,
                                size_bytes=size_bytes,
                                receiver_count=receiver_count,
                                round_index=round_index,
                            )
                        elif mode == SHM_REF:
                            result = harness.run_shm_ref_round(
                                mode=SHM_REF,
                                size_bytes=size_bytes,
                                receiver_count=receiver_count,
                                round_index=round_index,
                            )
                        else:
                            result = harness.run_adaptive_round(
                                size_bytes=size_bytes,
                                receiver_count=receiver_count,
                                round_index=round_index,
                            )
                        results.append(result)
    return results


def write_results(path: str, results: Iterable[BenchmarkResult]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for result in results:
            writer.writerow(result.to_csv_row())


def print_summary(path: str, results: Sequence[BenchmarkResult]) -> None:
    print(f"wrote {len(results)} benchmark rows to {path}")
    if not results:
        return
    failures = [result for result in results if not result.checksum_ok]
    print(f"checksum failures: {len(failures)}")
    for mode in (DIRECT_UDS, SHM_REF, "adaptive"):
        mode_rows = [row for row in results if row.mode == mode]
        if not mode_rows:
            continue
        avg_latency = sum(row.latency_ms for row in mode_rows) / len(mode_rows)
        print(f"{mode}: avg_latency_ms={avg_latency:.3f}")
    adaptive_rows = [row for row in results if row.mode == "adaptive"]
    if adaptive_rows:
        direct_count = sum(1 for row in adaptive_rows if row.selected_mode == DIRECT_UDS)
        shm_count = sum(1 for row in adaptive_rows if row.selected_mode == SHM_REF)
        print(
            "adaptive selections:"
            f" direct_uds={direct_count} shm_ref={shm_count}"
        )


def main() -> int:
    args = parse_args()
    try:
        modes = parse_modes(args.modes)
        sizes = parse_sizes(args.sizes)
        receivers = parse_receivers(args.receivers)
        results = run_benchmark(
            modes=modes,
            sizes=sizes,
            receivers=receivers,
            rounds=args.rounds,
        )
    except Exception as exc:
        print(f"benchmark failed: {exc}", file=sys.stderr)
        return 1

    write_results(args.output, results)
    print_summary(args.output, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
