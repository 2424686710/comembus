#!/usr/bin/env python3
"""Compare text, JSON, float32, and shared-memory embedding exchange."""

from __future__ import annotations

import argparse
import base64
import csv
from hashlib import sha256
import json
import math
from pathlib import Path
import socket
import sys
import threading
import time
from typing import Any, Dict, Iterable, List, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.collab.embedding_codec import EmbeddingBinaryCodec
from comembus.collab.embedding_store import EmbeddingRef, SharedEmbeddingStore
from comembus.memory.embedding import cosine_similarity
from comembus.metrics.recorder import MetricsRecorder
from comembus.transport.uds import recv_frame, send_frame


MODES = ("summary_text", "embedding_json", "embedding_float32", "embedding_ref")
DIMENSIONS = (32, 64, 128, 384, 768)
CSV_FIELDS = [
    "mode",
    "dim",
    "round",
    "dtype",
    "payload_bytes",
    "wire_bytes",
    "shm_bytes",
    "encode_latency_us",
    "decode_latency_us",
    "cosine_similarity",
    "cosine_similarity_preserved",
    "checksum_ok",
]


def benchmark_rows(
    dimensions: Sequence[int] = DIMENSIONS,
    rounds: int = 30,
    warmup: int = 3,
) -> List[Dict[str, object]]:
    if rounds <= 0 or warmup < 0:
        raise ValueError("rounds must be positive and warmup non-negative")
    if not dimensions or any(dim <= 0 for dim in dimensions):
        raise ValueError("dimensions must be positive")
    rows: List[Dict[str, object]] = []
    for mode in MODES:
        for dim in dimensions:
            vector = _deterministic_vector(dim)
            for warmup_index in range(warmup):
                _measure_once(mode, vector, -(warmup_index + 1))
            for round_index in range(1, rounds + 1):
                rows.append(_measure_once(mode, vector, round_index))
    return rows


def _measure_once(
    mode: str,
    vector: List[float],
    round_index: int,
) -> Dict[str, object]:
    recorder = MetricsRecorder()
    store = SharedEmbeddingStore(recorder)
    ref = None
    recovered: List[float] | None = None
    payload_bytes = 0
    shm_bytes = 0
    dtype = "text" if mode == "summary_text" else "json_float64"
    encode_started = time.perf_counter_ns()
    try:
        if mode == "summary_text":
            summary = f"incident embedding summary: database timeout wrong port; dim={len(vector)}"
            encoded = summary.encode("utf-8")
            checksum = sha256(encoded).hexdigest()
            payload = {"mode": mode, "summary": summary, "checksum": checksum}
            payload_bytes = len(encoded)
        elif mode == "embedding_json":
            encoded = json.dumps(vector, separators=(",", ":")).encode("utf-8")
            checksum = sha256(encoded).hexdigest()
            payload = {"mode": mode, "vector": vector, "checksum": checksum}
            payload_bytes = len(encoded)
        elif mode == "embedding_float32":
            encoded = EmbeddingBinaryCodec.encode_float32(vector)
            checksum = EmbeddingBinaryCodec.checksum(encoded)
            payload = {
                "mode": mode,
                "data_b64": base64.b64encode(encoded).decode("ascii"),
                "dim": len(vector),
                "dtype": EmbeddingBinaryCodec.dtype,
                "checksum": checksum,
            }
            payload_bytes = len(encoded)
            dtype = EmbeddingBinaryCodec.dtype
        elif mode == "embedding_ref":
            ref = store.put_vector(vector)
            checksum = ref.checksum
            payload = {"mode": mode, "embedding_ref": ref.to_dict()}
            payload_bytes = len(ref.to_json_bytes())
            shm_bytes = ref.object_ref.size
            dtype = ref.dtype
        else:
            raise ValueError(f"unsupported embedding mode: {mode}")
        encode_latency_us = (time.perf_counter_ns() - encode_started) / 1000.0

        received = _transmit(payload, recorder)
        decode_started = time.perf_counter_ns()
        if mode == "summary_text":
            received_bytes = str(received["summary"]).encode("utf-8")
            checksum_ok = sha256(received_bytes).hexdigest() == received["checksum"]
        elif mode == "embedding_json":
            recovered = [float(value) for value in received["vector"]]
            rebuilt = json.dumps(recovered, separators=(",", ":")).encode("utf-8")
            checksum_ok = sha256(rebuilt).hexdigest() == received["checksum"]
        elif mode == "embedding_float32":
            received_binary = base64.b64decode(received["data_b64"].encode("ascii"))
            checksum_ok = (
                EmbeddingBinaryCodec.checksum(received_binary) == received["checksum"]
            )
            recovered = EmbeddingBinaryCodec.decode_float32(
                received_binary, int(received["dim"])
            )
        else:
            received_ref = EmbeddingRef.from_dict(received["embedding_ref"])
            recovered = store.get_vector(received_ref)
            checksum_ok = received_ref.checksum == checksum
        decode_latency_us = (time.perf_counter_ns() - decode_started) / 1000.0

        similarity = (
            0.0 if recovered is None else cosine_similarity(vector, recovered)
        )
        preserved = recovered is not None and similarity >= 0.999999
        return {
            "mode": mode,
            "dim": len(vector),
            "round": round_index,
            "dtype": dtype,
            "payload_bytes": payload_bytes,
            "wire_bytes": recorder.snapshot().wire_bytes,
            "shm_bytes": shm_bytes,
            "encode_latency_us": encode_latency_us,
            "decode_latency_us": decode_latency_us,
            "cosine_similarity": similarity,
            "cosine_similarity_preserved": preserved,
            "checksum_ok": bool(checksum_ok),
        }
    finally:
        if ref is not None:
            store.unlink(ref)
        store.close()


def _transmit(payload: Dict[str, object], recorder: MetricsRecorder) -> Dict[str, Any]:
    sender, receiver = socket.socketpair()
    received: List[Dict[str, Any]] = []
    errors: List[BaseException] = []

    def read() -> None:
        try:
            received.append(recv_frame(receiver, recorder))
        except BaseException as exc:
            errors.append(exc)
        finally:
            receiver.close()

    thread = threading.Thread(target=read)
    thread.start()
    try:
        send_frame(sender, payload, recorder)
    finally:
        sender.close()
        thread.join()
    if errors:
        raise errors[0]
    if len(received) != 1:
        raise RuntimeError("embedding receiver did not return one frame")
    return received[0]


def _deterministic_vector(dim: int) -> List[float]:
    values = [
        math.sin((index + 1) * 0.173) + math.cos((index + 1) * 0.071)
        for index in range(dim)
    ]
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values]


def write_results(path: str | Path, rows: Iterable[Mapping[str, object]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for source in rows:
            row = dict(source)
            row["encode_latency_us"] = f"{float(row['encode_latency_us']):.6f}"
            row["decode_latency_us"] = f"{float(row['decode_latency_us']):.6f}"
            row["cosine_similarity"] = f"{float(row['cosine_similarity']):.9f}"
            row["cosine_similarity_preserved"] = str(
                bool(row["cosine_similarity_preserved"])
            ).lower()
            row["checksum_ok"] = str(bool(row["checksum_ok"])).lower()
            writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dimensions", default="32,64,128,384,768")
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--output", default="results/embedding_codec.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        dimensions = [int(value) for value in args.dimensions.split(",") if value]
        rows = benchmark_rows(dimensions, rounds=args.rounds, warmup=args.warmup)
        write_results(args.output, rows)
    except Exception as exc:
        print(f"embedding codec benchmark failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {len(rows)} embedding codec rows to {args.output}")
    for mode in MODES:
        mode_rows = [row for row in rows if row["mode"] == mode]
        mean_wire = sum(int(row["wire_bytes"]) for row in mode_rows) / len(mode_rows)
        print(f"{mode}: mean_wire_bytes={mean_wire:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
