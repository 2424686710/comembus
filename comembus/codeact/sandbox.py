"""Process-isolated execution for validated CodeAct snippets."""

from __future__ import annotations

import contextlib
import copy
import json
import math as _math
import multiprocessing
import queue
import time
from types import SimpleNamespace
from typing import Any, Mapping

from .validator import ASTCodeValidator, CodeValidationError

MAX_OUTPUT_CHARS = 4096
_VALIDATOR = ASTCodeValidator()
_SAFE_BUILTINS = {
    "float": float,
    "int": int,
    "len": len,
    "max": max,
    "min": min,
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


class _BoundedStdout:
    def __init__(self, limit: int = MAX_OUTPUT_CHARS) -> None:
        self._limit = limit
        self._parts: list[str] = []
        self._length = 0

    def write(self, data: str) -> int:
        text = str(data)
        if self._length >= self._limit:
            return len(text)
        remaining = self._limit - self._length
        chunk = text[:remaining]
        self._parts.append(chunk)
        self._length += len(chunk)
        return len(text)

    def flush(self) -> None:
        return None

    def getvalue(self) -> str:
        return "".join(self._parts)


def run_code_sandbox(
    code: str,
    inputs: Mapping[str, Any],
    timeout_sec: float = 2.0,
) -> dict[str, object]:
    started = time.perf_counter()
    try:
        _VALIDATOR.validate(code)
    except CodeValidationError as exc:
        return _build_response(
            ok=False,
            result=None,
            stdout="",
            error=_limit_text(str(exc)),
            timeout=False,
            started=started,
        )

    result_queue: multiprocessing.Queue[dict[str, object]] = multiprocessing.Queue(maxsize=1)
    process = multiprocessing.Process(
        target=_sandbox_worker,
        args=(code, dict(inputs), result_queue),
        daemon=True,
    )
    process.start()
    process.join(timeout=max(float(timeout_sec), 0.01))

    if process.is_alive():
        process.terminate()
        process.join()
        result_queue.close()
        return _build_response(
            ok=False,
            result=None,
            stdout="",
            error="execution timed out",
            timeout=True,
            started=started,
        )

    try:
        payload = result_queue.get_nowait()
    except queue.Empty:
        payload = {
            "ok": False,
            "result": None,
            "stdout": "",
            "error": "sandbox produced no result",
            "timeout": False,
        }
    finally:
        result_queue.close()

    return _build_response(
        ok=bool(payload.get("ok")),
        result=payload.get("result"),
        stdout=str(payload.get("stdout", "")),
        error=str(payload.get("error", "")),
        timeout=bool(payload.get("timeout")),
        started=started,
    )


def _sandbox_worker(
    code: str,
    inputs: dict[str, Any],
    result_queue: multiprocessing.Queue[dict[str, object]],
) -> None:
    try:
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
            }
        else:
            payload = {
                "ok": True,
                "result": _limit_result(globals_env.get("result")),
                "stdout": stdout_buffer.getvalue(),
                "error": "",
                "timeout": False,
            }
    except CodeValidationError as exc:
        payload = {
            "ok": False,
            "result": None,
            "stdout": "",
            "error": _limit_text(str(exc)),
            "timeout": False,
        }
    except Exception as exc:  # pragma: no cover - exercised via public API
        payload = {
            "ok": False,
            "result": None,
            "stdout": "",
            "error": _limit_text(f"{type(exc).__name__}: {exc}"),
            "timeout": False,
        }

    try:
        result_queue.put(payload)
    except Exception:
        return None


def _build_response(
    ok: bool,
    result: object | None,
    stdout: str,
    error: str,
    timeout: bool,
    started: float,
) -> dict[str, object]:
    latency_ms = (time.perf_counter() - started) * 1000.0
    return {
        "ok": ok,
        "result": result,
        "stdout": _limit_text(stdout),
        "error": _limit_text(error),
        "timeout": timeout,
        "latency_ms": latency_ms,
    }


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
