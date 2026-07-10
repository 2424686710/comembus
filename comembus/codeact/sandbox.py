"""Process-isolated execution for validated CodeAct snippets."""

from __future__ import annotations

import contextlib
import copy
from dataclasses import dataclass
import json
import math as _math
import multiprocessing
from multiprocessing.connection import Connection
import resource
import time
from types import SimpleNamespace
from typing import Any, Mapping

from .validator import ASTCodeValidator, CodeValidationError

MAX_OUTPUT_CHARS = 4096
MEBIBYTE = 1024 * 1024
_VALIDATOR = ASTCodeValidator()
_SAFE_BUILTINS = {
    "float": float,
    "int": int,
    "len": len,
    "max": max,
    "min": min,
    "print": print,
    "range": range,
    "sorted": sorted,
    "str": str,
    "sum": sum,
}
_SAFE_MATH = SimpleNamespace(
    ceil=_math.ceil,
    cos=_math.cos,
    e=_math.e,
    exp=_math.exp,
    fabs=_math.fabs,
    floor=_math.floor,
    log=_math.log,
    log10=_math.log10,
    pi=_math.pi,
    sin=_math.sin,
    sqrt=_math.sqrt,
    tan=_math.tan,
)


@dataclass(frozen=True)
class SandboxResourceLimits:
    """Linux process limits applied inside every CodeAct worker."""

    cpu_seconds: int = 2
    address_space_bytes: int = 256 * MEBIBYTE
    file_size_bytes: int = 1 * MEBIBYTE
    open_files: int = 32
    child_processes: int = 0

    def __post_init__(self) -> None:
        if self.cpu_seconds <= 0:
            raise ValueError("cpu_seconds must be positive")
        if self.address_space_bytes <= 0:
            raise ValueError("address_space_bytes must be positive")
        if self.file_size_bytes <= 0:
            raise ValueError("file_size_bytes must be positive")
        if self.open_files < 3:
            raise ValueError("open_files must be at least 3")
        if self.child_processes < 0:
            raise ValueError("child_processes must be non-negative")


class _BoundedStdout:
    def __init__(self, limit: int = MAX_OUTPUT_CHARS) -> None:
        self._limit = limit
        self._parts: list[str] = []
        self._length = 0
        self._truncated = False

    def write(self, data: str) -> int:
        text = str(data)
        if self._length >= self._limit:
            if text:
                self._truncated = True
            return len(text)
        remaining = self._limit - self._length
        chunk = text[:remaining]
        self._parts.append(chunk)
        self._length += len(chunk)
        if len(chunk) < len(text):
            self._truncated = True
        return len(text)

    def flush(self) -> None:
        return None

    def getvalue(self) -> str:
        return "".join(self._parts)

    @property
    def truncated(self) -> bool:
        return self._truncated


def run_code_sandbox(
    code: str,
    inputs: Mapping[str, Any],
    timeout_sec: float = 2.0,
    resource_limits: SandboxResourceLimits | None = None,
) -> dict[str, object]:
    started = time.perf_counter()
    limits = resource_limits or SandboxResourceLimits()
    try:
        _VALIDATOR.validate(code)
    except CodeValidationError as exc:
        return _build_response(
            ok=False,
            result=None,
            stdout="",
            error=_limit_text(str(exc)),
            timeout=False,
            stdout_truncated=False,
            started=started,
        )

    result_receiver, result_sender = multiprocessing.Pipe(duplex=False)
    process = multiprocessing.Process(
        target=_sandbox_worker,
        args=(code, dict(inputs), result_sender, limits, float(timeout_sec)),
        daemon=True,
    )
    try:
        process.start()
    except BaseException:
        result_receiver.close()
        result_sender.close()
        raise
    result_sender.close()
    process.join(timeout=max(float(timeout_sec), 0.01))

    if process.is_alive():
        process.terminate()
        process.join()
        result_receiver.close()
        return _build_response(
            ok=False,
            result=None,
            stdout="",
            error="execution timed out",
            timeout=True,
            stdout_truncated=False,
            started=started,
        )

    try:
        if result_receiver.poll(0.2):
            payload = result_receiver.recv()
        else:
            exit_detail = (
                f"; worker exit code={process.exitcode}"
                if process.exitcode not in (None, 0)
                else ""
            )
            payload = {
                "ok": False,
                "result": None,
                "stdout": "",
                "error": f"sandbox produced no result{exit_detail}",
                "timeout": False,
                "stdout_truncated": False,
            }
    except EOFError:
        payload = {
            "ok": False,
            "result": None,
            "stdout": "",
            "error": f"sandbox worker exited before returning; exit code={process.exitcode}",
            "timeout": False,
            "stdout_truncated": False,
        }
    finally:
        result_receiver.close()

    return _build_response(
        ok=bool(payload.get("ok")),
        result=payload.get("result"),
        stdout=str(payload.get("stdout", "")),
        error=str(payload.get("error", "")),
        timeout=bool(payload.get("timeout")),
        stdout_truncated=bool(payload.get("stdout_truncated")),
        started=started,
    )


def _sandbox_worker(
    code: str,
    inputs: dict[str, Any],
    result_sender: Connection,
    limits: SandboxResourceLimits,
    timeout_sec: float,
) -> None:
    try:
        _apply_resource_limits(limits, timeout_sec)
        _VALIDATOR.validate(code)
        stdout_buffer = _BoundedStdout()
        globals_env: dict[str, Any] = {
            "__builtins__": _SAFE_BUILTINS,
            "inputs": copy.deepcopy(inputs),
            "math": _SAFE_MATH,
        }
        with contextlib.redirect_stdout(stdout_buffer):
            exec(code, globals_env, globals_env)
        if "result" not in globals_env:
            payload = {
                "ok": False,
                "result": None,
                "stdout": stdout_buffer.getvalue(),
                "error": "code must define result",
                "timeout": False,
                "stdout_truncated": stdout_buffer.truncated,
            }
        else:
            payload = {
                "ok": True,
                "result": _limit_result(globals_env.get("result")),
                "stdout": stdout_buffer.getvalue(),
                "error": "",
                "timeout": False,
                "stdout_truncated": stdout_buffer.truncated,
            }
    except CodeValidationError as exc:
        payload = {
            "ok": False,
            "result": None,
            "stdout": "",
            "error": _limit_text(str(exc)),
            "timeout": False,
            "stdout_truncated": False,
        }
    except Exception as exc:  # pragma: no cover - exercised via public API
        payload = {
            "ok": False,
            "result": None,
            "stdout": "",
            "error": _limit_text(f"{type(exc).__name__}: {exc}"),
            "timeout": False,
            "stdout_truncated": False,
        }

    try:
        result_sender.send(payload)
    finally:
        result_sender.close()


def _build_response(
    ok: bool,
    result: object | None,
    stdout: str,
    error: str,
    timeout: bool,
    stdout_truncated: bool,
    started: float,
) -> dict[str, object]:
    latency_ms = (time.perf_counter() - started) * 1000.0
    return {
        "ok": ok,
        "result": result,
        "stdout": _limit_text(stdout),
        "error": _limit_text(error),
        "timeout": timeout,
        "stdout_truncated": stdout_truncated,
        "latency_ms": latency_ms,
    }


def _apply_resource_limits(
    limits: SandboxResourceLimits,
    timeout_sec: float,
) -> None:
    """Apply all required rlimits before executing validated user code."""

    cpu_soft = min(limits.cpu_seconds, max(1, int(_math.ceil(timeout_sec))))
    _set_resource_limit(resource.RLIMIT_CPU, cpu_soft, cpu_soft + 1)
    _set_resource_limit(
        resource.RLIMIT_AS,
        limits.address_space_bytes,
        limits.address_space_bytes,
    )
    _set_resource_limit(
        resource.RLIMIT_FSIZE,
        limits.file_size_bytes,
        limits.file_size_bytes,
    )
    _set_resource_limit(resource.RLIMIT_NOFILE, limits.open_files, limits.open_files)
    _set_resource_limit(
        resource.RLIMIT_NPROC,
        limits.child_processes,
        limits.child_processes,
    )


def _set_resource_limit(kind: int, soft: int, hard: int) -> None:
    current_soft, current_hard = resource.getrlimit(kind)
    if current_hard != resource.RLIM_INFINITY:
        hard = min(hard, int(current_hard))
    soft = min(soft, hard)
    resource.setrlimit(kind, (soft, hard))


def _limit_text(text: str) -> str:
    return str(text)[:MAX_OUTPUT_CHARS]


def _limit_result(value: object) -> object:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        encoded = repr(value)
    if len(encoded) <= MAX_OUTPUT_CHARS:
        return value
    return encoded[:MAX_OUTPUT_CHARS]
