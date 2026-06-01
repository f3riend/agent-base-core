"""
Controlled tool execution — real timeout, isolation, metrics, retry, circuit breaker.

Design:
    execute_in_sandbox runs run_fn on a ThreadPoolExecutor so we can enforce a
    true wall-clock timeout via future.result(timeout=...). Retries use
    exponential backoff. A lightweight in-memory circuit breaker short-fails
    further calls to a tool that has been failing repeatedly.
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Callable

from observability import _emit


DEFAULT_TIMEOUT_SEC = float(os.environ.get("TOOL_TIMEOUT_SEC", "30"))
MAX_RETRIES = int(os.environ.get("TOOL_MAX_RETRIES", "2"))
BACKOFF_BASE_SEC = float(os.environ.get("TOOL_BACKOFF_BASE_SEC", "0.5"))
BACKOFF_CAP_SEC = float(os.environ.get("TOOL_BACKOFF_CAP_SEC", "8.0"))

# Circuit breaker
CB_WINDOW_SEC = float(os.environ.get("TOOL_CB_WINDOW_SEC", "60"))
CB_FAILURE_THRESHOLD = int(os.environ.get("TOOL_CB_FAILURE_THRESHOLD", "5"))
CB_COOLDOWN_SEC = float(os.environ.get("TOOL_CB_COOLDOWN_SEC", "60"))

_EXECUTOR = ThreadPoolExecutor(
    max_workers=int(os.environ.get("TOOL_SANDBOX_WORKERS", "4")),
    thread_name_prefix="tool-sandbox",
)

_CB_LOCK = threading.Lock()
_CB_FAILURES: dict[str, deque] = defaultdict(deque)
_CB_OPENED_AT: dict[str, float] = {}


def _backoff_seconds(attempt: int) -> float:
    return min(BACKOFF_CAP_SEC, BACKOFF_BASE_SEC * (2 ** attempt))


def _circuit_open(tool_name: str, now: float) -> bool:
    opened_at = _CB_OPENED_AT.get(tool_name)
    if opened_at is None:
        return False
    if now - opened_at >= CB_COOLDOWN_SEC:
        _CB_OPENED_AT.pop(tool_name, None)
        _CB_FAILURES[tool_name].clear()
        return False
    return True


def _record_circuit_failure(tool_name: str, now: float):
    failures = _CB_FAILURES[tool_name]
    failures.append(now)
    while failures and now - failures[0] > CB_WINDOW_SEC:
        failures.popleft()
    if len(failures) >= CB_FAILURE_THRESHOLD and tool_name not in _CB_OPENED_AT:
        _CB_OPENED_AT[tool_name] = now
        _emit("TOOL_CIRCUIT_OPEN", {
            "tool": tool_name,
            "failures_in_window": len(failures),
            "cooldown_sec": CB_COOLDOWN_SEC,
        })


def _record_circuit_success(tool_name: str):
    _CB_FAILURES[tool_name].clear()
    _CB_OPENED_AT.pop(tool_name, None)


def execute_in_sandbox(
    tool,
    run_fn: Callable,
    input_payload: dict,
    *,
    tool_name: str,
    task_id: int | None = None,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
) -> dict:
    """
    Execute run_fn inside a sandboxed worker thread.

    Returns:
        {status, output, duration_ms, error, retries, traceback?}
    """
    started = time.monotonic()

    with _CB_LOCK:
        if _circuit_open(tool_name, started):
            _emit("TOOL_SANDBOX", {
                "tool": tool_name,
                "task_id": task_id,
                "status": "circuit_open",
                "duration_ms": 0,
            })
            return {
                "status": "failed",
                "output": None,
                "duration_ms": 0,
                "error": f"circuit_open for {tool_name}",
                "retries": 0,
            }

    last_error: Exception | None = None
    last_tb: str | None = None
    retries = 0

    for attempt in range(MAX_RETRIES + 1):
        retries = attempt
        attempt_started = time.monotonic()
        future = _EXECUTOR.submit(run_fn)

        try:
            output = future.result(timeout=timeout_sec if timeout_sec > 0 else None)
            duration_ms = int((time.monotonic() - started) * 1000)
            with _CB_LOCK:
                _record_circuit_success(tool_name)
            _emit("TOOL_SANDBOX", {
                "tool": tool_name,
                "task_id": task_id,
                "status": "success",
                "duration_ms": duration_ms,
                "retries": retries,
            })
            return {
                "status": "success",
                "output": output,
                "duration_ms": duration_ms,
                "error": None,
                "retries": retries,
            }

        except FuturesTimeoutError:
            future.cancel()
            last_error = TimeoutError(
                f"Tool {tool_name} exceeded {timeout_sec:.1f}s timeout"
            )
            last_tb = None
            _emit("TOOL_SANDBOX_TIMEOUT", {
                "tool": tool_name,
                "task_id": task_id,
                "timeout_sec": timeout_sec,
                "attempt": attempt,
                "attempt_elapsed_ms": int(
                    (time.monotonic() - attempt_started) * 1000
                ),
            })

        except Exception as exc:
            last_error = exc
            last_tb = traceback.format_exc()[:500]

        if attempt < MAX_RETRIES:
            time.sleep(_backoff_seconds(attempt))
            continue

    duration_ms = int((time.monotonic() - started) * 1000)
    with _CB_LOCK:
        _record_circuit_failure(tool_name, time.monotonic())
    _emit("TOOL_SANDBOX", {
        "tool": tool_name,
        "task_id": task_id,
        "status": "failed",
        "duration_ms": duration_ms,
        "error": str(last_error),
        "retries": retries,
    })
    result = {
        "status": "failed",
        "output": None,
        "duration_ms": duration_ms,
        "error": str(last_error) if last_error else "unknown_error",
        "retries": retries,
    }
    if last_tb:
        result["traceback"] = last_tb
    return result


def circuit_state() -> dict:
    """Diagnostic snapshot of the breaker — for /api/internal observability."""
    with _CB_LOCK:
        return {
            "open_tools": {
                k: {"opened_at": v, "cooldown_sec": CB_COOLDOWN_SEC}
                for k, v in _CB_OPENED_AT.items()
            },
            "failure_counts": {
                k: len(v) for k, v in _CB_FAILURES.items() if v
            },
        }
