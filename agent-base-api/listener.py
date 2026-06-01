"""Timeline listener — hybrid routing: critical rules vs autonomous planner."""

from env_bootstrap import load_app_env

load_app_env()

import os
import time
import traceback

from action_engine import execute_rule_actions
from db import get_cursor, init_db, set_cursor
from event_envelope import EventEnvelope
from event_router import (
    is_critical_event,
    route_event,
    routing_confidence,
    should_use_autonomous,
)
from observability import log_routing_decision
from resource_service import (
    build_rule_context,
    fetch_events,
    fetch_item,
    fetch_order,
    fetch_store,
    mark_item_status,
    mark_store_status,
    upsert_item,
    upsert_order,
    upsert_store,
)
from rule_engine import find_matching_rules
from rule_service import resolve_user_id_from_event, seed_rules_from_file_if_empty
from timeline_processing import update_event_processing_meta


SKIP_SYNTHETIC = os.environ.get("LISTENER_SKIP_SYNTHETIC", "1") == "1"

POLL_INTERVAL = 2
AUTONOMOUS_ENABLED = os.environ.get("AUTONOMOUS_PLANNER_ENABLED", "1") == "1"
CRITICAL_FALLBACK = os.environ.get("CRITICAL_PLANNER_FALLBACK", "0") == "1"

init_db()
seed_rules_from_file_if_empty()


def process_event(event) -> dict:
    """Process one timeline event; returns orchestration outcome for meta update."""
    started = time.monotonic()
    event_id = event["id"]

    outcome = {
        "processed": True,
        "route": "",
        "path": "listener",
        "rules_matched": 0,
        "planner_used": False,
        "autonomous_used": False,
        "skip_reason": None,
    }

    print(
        f"\n[EVENT] {event['group']}.{event['event']} "
        f"(#{event_id})"
    )

    # Adapt raw timeline row into a typed envelope so downstream code can
    # consult source / priority / causation in one place instead of poking
    # at meta JSON. Legacy callsites continue to read event[...] dict keys.
    envelope = EventEnvelope.from_legacy(event)

    if SKIP_SYNTHETIC and envelope.is_synthetic:
        outcome["processed"] = True
        outcome["route"] = "synthetic"
        outcome["path"] = "synthetic_skip"
        outcome["skip_reason"] = f"synthetic_source_{envelope.source}"
        elapsed = int((time.monotonic() - started) * 1000)
        print(
            f"[EVENT] #{event_id} synthetic ({envelope.source}) skipped — "
            f"emitted by {envelope.meta.get('tool_name') or 'planner'}"
        )
        update_event_processing_meta(
            event_id,
            processed=True,
            route=outcome["route"],
            path=outcome["path"],
            rules_matched=0,
            planner_used=False,
            autonomous_used=False,
            skip_reason=outcome["skip_reason"],
            listener_ms=elapsed,
        )
        return outcome

    subject = event.get("subject") or {}
    subject_type = subject.get("type")
    subject_id = subject.get("id")
    event_name = f"{event['group']}.{event['event']}"

    if not subject_type or subject_id is None:
        # Synthetic / echo events (e.g. fake_tool_timeline writes) have no
        # entity to act on. They exist for observability only — skip cleanly
        # so the cursor advances and orchestration doesn't crash on .lower().
        outcome["processed"] = True
        outcome["route"] = "skipped"
        outcome["path"] = "skipped_no_subject"
        outcome["skip_reason"] = "missing_subject_type" if not subject_type else "missing_subject_id"
        elapsed = int((time.monotonic() - started) * 1000)
        print(
            f"[EVENT] #{event_id} skipped: {outcome['skip_reason']} "
            f"(group={event.get('group')} event={event.get('event')})"
        )
        update_event_processing_meta(
            event_id,
            processed=True,
            route=outcome["route"],
            path=outcome["path"],
            rules_matched=0,
            planner_used=False,
            autonomous_used=False,
            skip_reason=outcome["skip_reason"],
            listener_ms=elapsed,
        )
        return outcome

    user_id = resolve_user_id_from_event(event, subject_type, subject_id)
    route = route_event(event_name, event)
    route_conf = routing_confidence(event_name, event)
    outcome["route"] = route

    log_routing_decision(event_name, route, route_conf, f"user_id={user_id}")
    print(f"[TENANT] user_id={user_id} route={route} confidence={route_conf:.2f}")

    if subject_type == "Store":
        store = fetch_store(subject_id)
        if store:
            upsert_store(store, user_id=user_id)
        if event["event"] == "rejected":
            mark_store_status(subject_id, "rejected")

    elif subject_type == "Item":
        item = fetch_item(subject_id)
        if item:
            upsert_item(item, user_id=user_id)
        if event["event"] == "deleted":
            mark_item_status(subject_id, "deleted")

    elif subject_type == "Order":
        order = fetch_order(subject_id)
        if order:
            upsert_order(order)

    elif subject_type in ("Review", "Campaign", "Banner", "Story", "Coupon"):
        # Yeni subject_type'lar — DB'de upsert tablosu yok ama context yine de
        # build edilsin (event payload'tan + opsiyonel DB fetch). Story/Coupon
        # için fake_ai_api.db'de tablo yok, payload'a güvenilir.
        print(f"[EVENT] subject_type={subject_type} id={subject_id} — payload-based context")

    context = build_rule_context(subject_type, subject_id, event)

    # Structured Rules (LangGraph) — paralel path. Mevcut rule_engine ve
    # autonomous_planner yolu olduğu gibi çalışmaya devam eder; bu blok
    # SADECE eklemedir, hiçbir akışı kesintiye uğratmaz. Hata olursa
    # listener loop'u etkilenmesin diye geniş bir try/except.
    try:
        from structured_rule_engine import trigger_rules_for_event
        struct_event = {
            "event_id":     event["id"],
            "event_type":   event_name,
            "payload":      event.get("payload") or {},
            "subject_type": subject_type,
            "subject_id":   subject_id,
            "store":        context.get("store"),
            "item":         context.get("item"),
            "order":        context.get("order"),
            "received_at":  event.get("ts"),
        }
        # Tenant izolasyonu: event'in causer'ı kim ise (user_id) yalnız o
        # kullanıcının kuralları tetiklenir. Cross-tenant tetiklemeler için
        # kasıtlı olarak user_id=None geçilmek istenirse bu satır değişmeli.
        struct_results = trigger_rules_for_event(
            event_name, struct_event, user_id=user_id,
        )
        if struct_results:
            outcome["structured_rules_fired"] = len(struct_results)
            print(
                f"[STRUCTURED_RULES] {len(struct_results)} kural tetiklendi "
                f"(event={event_name}, event_user_id={user_id})"
            )
    except Exception as exc:
        print(f"[STRUCTURED_RULES] match/execute hata: {exc}")

    if route == "critical" or is_critical_event(event_name, event):
        o = _process_critical_path(
            event, event_name, context, subject_type, subject_id, user_id
        )
        outcome.update(o)
        outcome["path"] = "critical"
    elif route == "monitoring":
        o = _process_monitoring_path(
            event, event_name, context, subject_type, subject_id, user_id
        )
        outcome.update(o)
        outcome["path"] = "monitoring"
        outcome["processed"] = True
    elif AUTONOMOUS_ENABLED:
        o = _process_autonomous_path(
            event, event_name, context, subject_type, subject_id, user_id, route
        )
        outcome.update(o)
        outcome["path"] = o.get("path", "autonomous")
    else:
        o = _process_critical_path(
            event, event_name, context, subject_type, subject_id, user_id
        )
        outcome.update(o)
        outcome["path"] = "fallback_critical"

    elapsed = int((time.monotonic() - started) * 1000)
    print(f"[METRIC] event_processing_ms={elapsed} event_id={event_id}")

    update_event_processing_meta(
        event_id,
        processed=outcome["processed"],
        route=outcome["route"],
        path=outcome["path"],
        rules_matched=outcome["rules_matched"],
        planner_used=outcome["planner_used"],
        autonomous_used=outcome["autonomous_used"],
        skip_reason=outcome.get("skip_reason"),
        listener_ms=elapsed,
    )
    return outcome


def _process_critical_path(
    event, event_name, context, subject_type, subject_id, user_id
) -> dict:
    out = {"rules_matched": 0, "planner_used": False, "autonomous_used": False, "skip_reason": None}
    matched_rules = find_matching_rules(event_name, context, user_id=user_id)

    if matched_rules:
        out["rules_matched"] = len(matched_rules)
        print(f"[ROUTING] CRITICAL rules ({len(matched_rules)}) user_id={user_id}")
        for rule in matched_rules:
            print(f"[RULE] Matched: {rule['name']}")
            execute_rule_actions(
                rule, subject_type, subject_id,
                event_id=event["id"], user_id=user_id,
            )
        return out

    if CRITICAL_FALLBACK:
        print("[ROUTING] critical event, no rule — legacy planner fallback")
        from planner_runtime import handle_critical_fallback

        result = handle_critical_fallback(
            event, event_name, context, subject_type, subject_id, user_id
        )
        out["planner_used"] = bool(result)
        if result:
            print(f"[PLANNER] fallback result: {result}")
    else:
        out["skip_reason"] = "critical_no_rule_match"
        print("[ROUTING] critical event, no rule — autonomous disabled (safety)")
    return out


def _process_monitoring_path(
    event, event_name, context, subject_type, subject_id, user_id
) -> dict:
    out = {"rules_matched": 0, "skip_reason": None}
    matched = find_matching_rules(event_name, context, user_id=user_id)
    if matched:
        out["rules_matched"] = len(matched)
        for rule in matched:
            execute_rule_actions(
                rule, subject_type, subject_id,
                event_id=event["id"], user_id=user_id,
            )
        return out
    out["skip_reason"] = "monitoring_no_rule"
    print("[ROUTING] monitoring — no rule, skipped")
    return out


def _process_autonomous_path(
    event, event_name, context, subject_type, subject_id, user_id, route="hybrid"
) -> dict:
    out = {
        "rules_matched": 0,
        "planner_used": False,
        "autonomous_used": False,
        "skip_reason": None,
        "path": "autonomous",
    }
    matched_rules = find_matching_rules(event_name, context, user_id=user_id)

    if matched_rules:
        out["rules_matched"] = len(matched_rules)
        print(f"[ROUTING] {route}: rules matched ({len(matched_rules)})")
        for rule in matched_rules:
            print(f"[RULE] Matched: {rule['name']}")
            execute_rule_actions(
                rule, subject_type, subject_id,
                event_id=event["id"], user_id=user_id,
            )
        if not should_use_autonomous(event_name, event, rules_matched=True):
            out["skip_reason"] = "rules_only_hybrid"
            return out

    if not should_use_autonomous(event_name, event, rules_matched=bool(matched_rules)):
        out["skip_reason"] = f"autonomous_skipped_route_{route}"
        print(f"[ROUTING] autonomous skipped for route={route}")
        return out

    print(f"[ROUTING] AUTONOMOUS planner user_id={user_id} event={event_name}")
    from planner_runtime import handle_autonomous_event

    result = handle_autonomous_event(
        event=event,
        event_name=event_name,
        context=context,
        subject_type=subject_type,
        subject_id=subject_id,
        user_id=user_id,
    )
    out["autonomous_used"] = True
    out["planner_used"] = bool(result)
    if result:
        print(f"[AUTONOMOUS] runtime result: {result}")
    return out


def main():
    print("\nListener service started...")
    print("[CONFIG] Hybrid routing: critical=rules | creative=autonomous")
    print(f"[CONFIG] Autonomous planner: {'ENABLED' if AUTONOMOUS_ENABLED else 'disabled'}")
    print("[CONFIG] Timeline meta: processed_by_rule_engine updated per event")

    # ThreadPoolExecutor — process_event() içinde GPT-4o vision + image gen ~60-90sn
    # blokesini paralelize et. Worker sayısı LISTENER_WORKERS env veya default 4.
    # process_event'in kendisi sync; her event ayrı worker thread'inde koşar.
    from concurrent.futures import ThreadPoolExecutor
    worker_count = int(os.environ.get("LISTENER_WORKERS", "4"))
    _executor = ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="listener-worker",
    )
    print(f"[CONFIG] ThreadPoolExecutor: max_workers={worker_count}")
    print()

    def _safe_process(ev):
        """process_event'i wrap eder; istisna olursa meta'yı error olarak işaretler.
        Cursor advance ana thread'de — bu fonksiyon sadece event-içi işi yapar."""
        try:
            process_event(ev)
        except Exception as e:
            print(f"\n[ERROR] event #{ev['id']} failed: {e}")
            traceback.print_exc()
            try:
                update_event_processing_meta(
                    ev["id"],
                    processed=False,
                    route="error",
                    path="error",
                    skip_reason=str(e)[:200],
                )
            except Exception as meta_exc:
                print(f"[ERROR] update_meta failed for #{ev['id']}: {meta_exc}")

    while True:
        poll_started = time.monotonic()

        try:
            cursor = get_cursor()
            events = fetch_events(cursor)

            if events:
                print(f"\n[INFO] {len(events)} new event(s) — submitting to {worker_count}-worker pool")

            for event in events:
                # Worker thread'ine submit et — bloke etmeden cursor'u ilerlet.
                # process_event içindeki uzun süren çağrılar (LangGraph, GPT-4o, image gen)
                # ana polling loop'unu durdurmaz. Aynı event çift işlenmemesi için
                # cursor advance her submit'in hemen ardından gelir.
                _executor.submit(_safe_process, event)
                set_cursor(event["id"])

            poll_ms = int((time.monotonic() - poll_started) * 1000)
            print(
                f"[METRIC] listener_poll_ms={poll_ms} "
                f"events_processed={len(events)}"
            )

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\nStopped. Waiting workers to drain...")
            _executor.shutdown(wait=True, cancel_futures=False)
            break

        except Exception as e:
            print(f"\n[ERROR] listener loop: {e}")
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    main()
