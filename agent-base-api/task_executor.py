"""
Task executor worker — CrewAI replacement.

Pending `ai_tasks` satırlarını polling ile alır, tool_registry'den ilgili
tool(s)'u resolve eder ve doğrudan `tool._run(**kwargs)` ile çalıştırır.
Birden çok tool varsa hepsini sırayla çağırır; ilk başarısızlık task'i
fail eder.

Eski CrewAI worker'a göre farklar:
    - Agent / Crew / Task katmanı yok — düz Python tool invocation.
    - Tool argümanları payload'un her tool için tip-uyumlu alanlarından
      kuruluyor (heuristik mapping). Eski LLM "tool seçimi" yok çünkü
      structured rules zaten hangi tool'un çağırılacağını belirliyor;
      legacy autonomous_planner workflow'ları için de tool listesi
      payload.tools'tan veya CRITICAL_TASK_MAP'ten geliyor.

Backward-compat: `crewai_worker.py` artık bu modüle delegate eden bir
shim; `python crewai_worker.py` ile çağrılan eski deploy script'leri
kırılmıyor.
"""

from __future__ import annotations

import json
import time
import traceback
from typing import Any

from env_bootstrap import load_app_env

load_app_env()

from db import TASK_RETRYING, TASK_RUNNING, init_db
from task_service import (
    cancel_task,
    complete_task,
    fail_task,
    get_last_tool_execution,
    get_pending_tasks,
    log_tool_execution,
    transition_task,
)
from tool_registry import CRITICAL_TASK_MAP, resolve_tool_instances


POLL_INTERVAL = 5


init_db()


def _startup_tool_validation() -> None:
    from tool_schema_validator import print_validation_summary, validate_all_tools
    from tools import TOOLS
    summary = validate_all_tools(TOOLS)
    print_validation_summary(summary)
    if summary["invalid_count"]:
        print(
            "[TASK_EXECUTOR] Bazı araçlar şema hatası nedeniyle devre dışı "
            "olabilir"
        )


_startup_tool_validation()


# ---------------------------------------------------------------------------
# Tool resolution + argument mapping
# ---------------------------------------------------------------------------


class NoToolsResolvedError(Exception):
    """Tool registry boş döndü — task hatasız iptal edilir (retry yok)."""


def _resolve_tools_for_task(task_type: str, payload: dict) -> list[str]:
    if task_type in CRITICAL_TASK_MAP:
        return list(CRITICAL_TASK_MAP[task_type]["tools"])
    if payload.get("tools"):
        return list(payload["tools"])
    from tool_registry import get_tools_for_intent
    text = " ".join([
        task_type,
        payload.get("goal", ""),
        payload.get("business_intent", ""),
        payload.get("workflow_name", ""),
    ])
    return get_tools_for_intent(text, limit=4)


def _build_tool_args(tool_name: str, payload: dict) -> dict[str, Any]:
    """Payload'tan tool'a uygun argümanları çıkar.

    Eski CrewAI runtime'da LLM "hangi argümanla çağırayım" kararı
    veriyordu; şimdi structured_rules ve workflow metadata zaten yeterli
    bilgi taşıyor. Tool bazlı sade mapping yetiyor.
    """
    item = payload.get("item") or {}
    store = payload.get("store") or {}
    goal = (payload.get("goal") or "").strip()
    headline = (
        item.get("name")
        or store.get("name")
        or goal[:80]
        or "Yeni içerik"
    )

    if tool_name == "instagram_campaign_tool":
        return {
            "headline": headline,
            "hook": goal[:120] or None,
            "hashtags": [],
        }
    if tool_name == "banner_generator_tool":
        return {
            "headline": headline,
            "subline": goal[:120] or None,
            "cta": "Hemen incele",
        }
    if tool_name == "coupon_generator_tool":
        return {"label": headline[:40], "percent": 10}
    if tool_name == "faq_update_tool":
        return {
            "topic": "genel",
            "question": (goal or "Sıkça sorulan")[:80],
            "answer": (goal or "Cevap")[:280],
        }
    if tool_name == "support_response_tool":
        question = (payload.get("event") or {}).get("description") or goal or "Müşteri sorusu"
        return {"customer_question": question[:200], "tone": "friendly"}
    if tool_name == "trend_analysis_tool":
        return {
            "focus": (item.get("name") or store.get("name") or "genel"),
            "lookback_days": 7,
        }
    if tool_name == "low_stock_notification_tool":
        return {
            "item_name": item.get("name", "Ürün"),
            "current_stock": int(item.get("stock") or 0),
            "threshold": 10,
        }
    return {}


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def execute_task(task_row) -> None:
    task_id = task_row["id"]
    payload = json.loads(task_row["payload"] or "{}")
    task_type = task_row["task_type"]
    current_status = task_row["status"]

    started = time.monotonic()
    print(f"\n[TASK] Processing #{task_id} ({task_type})")

    transition_task(task_id, TASK_RUNNING)

    try:
        tool_names = _resolve_tools_for_task(task_type, payload)
        if not tool_names:
            raise NoToolsResolvedError(
                f"No tools resolved for task_type={task_type} "
                f"intent={payload.get('business_intent')}"
            )

        instances = resolve_tool_instances(tool_names)
        if not instances:
            raise NoToolsResolvedError(
                f"Tool registry returned empty for {tool_names}"
            )

        last_output: dict | None = None
        for tool in instances:
            tool.set_execution_context(task_id, log_tool_execution)
            args = _build_tool_args(tool.name, payload)
            last_output = tool._run(**args)
            # Tool zaten log_tool_execution callback'i içeriyor;
            # ek bir kayıt gerekmez.
            if isinstance(last_output, dict) and last_output.get("success") is False:
                raise RuntimeError(
                    f"tool {tool.name} returned failure: "
                    f"{last_output.get('error') or last_output.get('message')}"
                )

        selected = get_last_tool_execution(task_id) or ",".join(tool_names)
        complete_task(task_id, json.dumps(last_output or {}, default=str)[:2000],
                      selected_tool=selected)

        elapsed = int((time.monotonic() - started) * 1000)
        print(
            f"[TASK] Completed #{task_id} — tools={tool_names} "
            f"selected={selected} duration_ms={elapsed}"
        )

    except NoToolsResolvedError as exc:
        try:
            cancel_task(task_id, reason=f"no_tools_resolved: {exc}")
        except Exception:
            fail_task(task_id, str(exc))
        print(f"[TASK] Cancelled #{task_id}: {exc}")

    except Exception as exc:
        fail_task(task_id, str(exc))
        print(f"[TASK] Failed #{task_id}: {exc}")


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


def main() -> None:
    print("\nTask executor worker started (CrewAI removed in Phase 2 of LangGraph migration).\n")

    while True:
        poll_started = time.monotonic()
        try:
            tasks = get_pending_tasks()
            for task in tasks:
                try:
                    execute_task(task)
                except Exception as exc:
                    print(f"\n[ERROR] task #{task['id']}: {exc}")
                    traceback.print_exc()
            poll_ms = int((time.monotonic() - poll_started) * 1000)
            print(f"[METRIC] task_executor_poll_ms={poll_ms} queue={len(tasks)}")
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as exc:
            print(f"\n[ERROR] {exc}")
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    main()
