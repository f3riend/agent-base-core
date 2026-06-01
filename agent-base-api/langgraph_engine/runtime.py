"""
Dynamic LangGraph runtime — StructuredRule → compiled StateGraph.

Buradaki temel sorumluluk şunlar:
    1. StructuredRule'un eylem zincirini canonical bir sıraya sok
       (wait → generate_content → create_coupon → risk_check → approval →
        publish → notify_customer → monitor → finalize).
    2. Sadece kuralda istenen node'ları ekle (örn. approval yoksa atla).
    3. SqliteSaver ile checkpoint'i bağla — execution kalıcı, time-travel
       mümkün.
    4. approval_gate'i interrupt_before olarak işaretle, böylece operatör
       onayı bekleyince graph duraklar ve resume edilebilir.
    5. Execution row'unu rule_executions tablosunda aç/kapat.

LangGraph 1.2 API'sini kullanıyoruz:
    - StateGraph(typed_state).add_node(...).add_edge(START, ...).compile(checkpointer=..., interrupt_before=[...])
    - graph.invoke(initial_state, config={"configurable": {"thread_id": ...}})
    - Resume için: graph.invoke(None, config) — devam eder.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Optional

from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.sqlite import SqliteSaver

from db import db_connection, execute_query, execute_write, now_iso
from langgraph_engine import nodes
from langgraph_engine.state import (
    RuleExecutionState,
    initial_state,
)
from structured_rule import StructuredRule


# ---------------------------------------------------------------------------
# Sabit canonical sıra
# ---------------------------------------------------------------------------


# Eylem zincirinin canonical sıralaması. Operatör hangi sırayı yazmış olursa
# olsun graph builder bu sıraya göre node'ları bağlar — böylece akış her
# zaman güvenli (önce risk_check, sonra approval, sonra publish, ...).
_CANONICAL_ORDER: tuple[str, ...] = (
    "wait",
    "generate_content",
    "create_coupon",
    "risk_check",
    "approval",
    "publish",
    "notify_customer",
    "monitor",
)

# Hangi action_kind hangi node fonksiyonuna karşılık geliyor.
# Eski (canonical) yol bu haritayı kullanır.
_NODE_FUNCS = {
    "wait":             nodes.wait_node,
    "generate_content": nodes.content_generator_node,
    "risk_check":       nodes.risk_analyzer_node,
    "approval":         nodes.approval_gate_node,
    "publish":          nodes.publisher_node,
    "monitor":          nodes.monitor_node,
    "notify_customer":  nodes.notify_customer_node,
    "create_coupon":    nodes.create_coupon_node,
}


# Dinamik yol (Bölüm 4.2) — NodeDefinition.node_type → fonksiyon eşlemesi.
# Tek kaynak: nodes.NODE_FUNCTIONS. Aşağı uyumluluk için alias bırakıldı.
_DYNAMIC_NODE_FUNCS = nodes.NODE_FUNCTIONS


# ---------------------------------------------------------------------------
# Checkpointer singleton
# ---------------------------------------------------------------------------


_CHECKPOINT_DB = os.environ.get(
    "LANGGRAPH_CHECKPOINT_DB", "listener.db"
)
_checkpointer: SqliteSaver | None = None
_checkpoint_conn: sqlite3.Connection | None = None


def get_checkpointer() -> SqliteSaver:
    """Process-life checkpointer. Aynı SQLite dosyasına yazıyor."""
    global _checkpointer, _checkpoint_conn
    if _checkpointer is None:
        _checkpoint_conn = sqlite3.connect(
            _CHECKPOINT_DB, check_same_thread=False
        )
        _checkpointer = SqliteSaver(_checkpoint_conn)
    return _checkpointer


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def _selected_kinds(rule: StructuredRule) -> list[str]:
    """Kuraldan istenmiş action_kind'ları canonical sıraya sok, gereksizleri
    çıkar, eksikleri güvenli default'larla doldur.
    """
    requested = {a.kind for a in rule.actions}

    # Güvenlik için kritik node'lar varsa otomatik ekle
    if "publish" in requested:
        if "risk_check" not in requested:
            requested.add("risk_check")
        if rule.requires_approval and "approval" not in requested:
            requested.add("approval")

    # Eğer publish var ve generate_content yoksa, ekle (yayınlanacak içerik gerek)
    if "publish" in requested and "generate_content" not in requested:
        requested.add("generate_content")

    # Eğer wait varsa veya rule.timing.delay_seconds > 0 ise wait node'unu garantiye al
    if rule.timing.delay_seconds > 0:
        requested.add("wait")

    return [k for k in _CANONICAL_ORDER if k in requested]


def build_graph(rule: StructuredRule):
    """StructuredRule'dan derlenmiş LangGraph döndür.

    Bölüm 4.2 dispatcher:
        - rule.graph_definition doluysa → dinamik yol (_build_dynamic_graph)
        - boşsa → eski canonical yol (_build_canonical_graph)

    Returns:
        compiled StateGraph
    """
    if rule.graph_definition is not None:
        return _build_dynamic_graph(rule)
    return _build_canonical_graph(rule)


def _build_canonical_graph(rule: StructuredRule):
    """ESKİ YOL (dokunulmadı) — actions'tan canonical sırada graph.

    Graph her zaman supervisor ile başlar ve finalize ile biter. Aradaki
    node'lar `_selected_kinds(rule)` ile belirlenir. approval node'u
    eklenirse `interrupt_before=["approval"]` ile compile edilir.
    """
    selected = _selected_kinds(rule)

    g = StateGraph(RuleExecutionState)
    g.add_node("supervisor", nodes.supervisor_node)
    g.add_node("finalize", nodes.finalize_node)

    for kind in selected:
        node_fn = _NODE_FUNCS.get(kind)
        if node_fn:
            g.add_node(kind, node_fn)

    g.add_edge(START, "supervisor")

    chain = ["supervisor"] + selected + ["finalize"]
    for a, b in zip(chain[:-1], chain[1:]):
        g.add_edge(a, b)
    g.add_edge("finalize", END)

    interrupt_before: list[str] = []
    interrupt_after: list[str] = []
    if "approval" in selected:
        interrupt_before.append("approval")
    # Tur 2: wait_node'u runtime'da gerçekten "duraklat" — wait node'u
    # çalıştırdıktan SONRA graph durur. workflow_worker scheduled
    # entry'yi tetiklediğinde resume_after_wait çağrılır, kalan
    # node'lar (content_generator, risk, ...) çalışır.
    if "wait" in selected and rule.timing.delay_seconds > 0:
        interrupt_after.append("wait")

    checkpointer = get_checkpointer()
    return g.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before or None,
        interrupt_after=interrupt_after or None,
    )


# ---------------------------------------------------------------------------
# Dinamik graph builder (Bölüm 4.2)
# ---------------------------------------------------------------------------


def _build_dynamic_graph(rule: StructuredRule):
    """rule.graph_definition'dan dinamik LangGraph derle.

    GraphDefinition.nodes listesi sıralı veya paralel node'ları taşır:
      - parallel_with: bu node hangi node_id'lerle eş zamanlı çalışır
      - depends_on:   bu node hangi node'lar bitmeden başlamaz

    Edge stratejisi (_wire_edges):
      1. parallel_with grupları tespit edilir — aynı grup tek bir fan-out
         noktasından başlar, fan-in noktasında birleşir.
      2. depends_on olan node'lar açıkça bağlanır.
      3. Geri kalanlar tanımlı sırada zincirlenir.

    interrupt_before / interrupt_after GraphDefinition'dan doğrudan alınır.
    """
    gd = rule.effective_graph_definition()  # graph_definition None ise synthesize edilir

    g = StateGraph(RuleExecutionState)

    # 1) Node'ları ekle — her birini params-injecting wrapper'la sar
    for node_def in gd.nodes:
        fn = _DYNAMIC_NODE_FUNCS.get(node_def.node_type)
        if fn is None:
            # Bilinmeyen node tipi — finalize'a ekle ki graph kırılmasın
            continue
        wrapped = _wrap_with_params(fn, node_def)
        g.add_node(node_def.node_id, wrapped)

    # 2) Entry / exit
    entry = gd.entry_node or (gd.nodes[0].node_id if gd.nodes else None)
    exit_id = gd.exit_node or (gd.nodes[-1].node_id if gd.nodes else None)
    if entry:
        g.add_edge(START, entry)
    if exit_id:
        g.add_edge(exit_id, END)

    # 3) Edge'leri bağla
    _wire_edges(g, gd)

    checkpointer = get_checkpointer()
    return g.compile(
        checkpointer=checkpointer,
        interrupt_before=(gd.interrupt_before or None),
        interrupt_after=(gd.interrupt_after or None),
    )


def _wrap_with_params(fn, node_def):
    """Node fonksiyonunu sar: state.metadata.__node_params'a node_def.params
    yerleştirir, böylece nodes._get_node_params() doğru değeri okur.

    Wrapper aynı zamanda dönüş trace'inde node_id'yi (current_node) garantiye
    alır — bazı node'lar state.current_node'u kendi tipinden farklı set
    edebiliyor (ör. publish_post canonical mantıkta "publisher" gibi).
    """
    nd_params = dict(node_def.params or {})
    nd_id = node_def.node_id

    def wrapped(state):
        # Mevcut metadata'yı koru, sadece __node_params'i ekle
        existing_meta = state.get("metadata") or {}
        new_meta = {**existing_meta, "__node_params": nd_params}
        # Geçici state — fn'in göreceği state, orijinal state'i mutate etmez
        injected_state = {**state, "metadata": new_meta}
        result = fn(injected_state) or {}
        # fn metadata döndürdüyse __node_params'i temizle (cross-node leak yok)
        if "metadata" in result:
            md = dict(result["metadata"] or {})
            md.pop("__node_params", None)
            result = {**result, "metadata": md}
        # current_node node_id'ye sabitlensin (dinamik runtime kuralı)
        result.setdefault("current_node", nd_id)
        return result

    return wrapped


def _wire_edges(g, gd) -> None:
    """GraphDefinition'dan StateGraph'a edge'leri bağla.

    Algoritma:
      - "groups" tespit et: parallel_with'i aynı setteki node'lar bir gruptur.
      - Tanımlı sırada (gd.nodes) bir lineer "iskelet" oluştur, paralel
        grup karşılaşıldığında:
          * önceki tek node → grubun TÜM üyelerine fan-out
          * grubun tüm üyeleri → sonraki tek node (veya depends_on'u
            grubun tamamı olan node) fan-in
      - depends_on alanı boş değilse, bağımlılık edge'leri açıkça eklenir
        (override gibi davranır).
    """
    nodes_in_order = list(gd.nodes)
    if not nodes_in_order:
        return

    # 1) Paralel grupları kur. Her grup bir frozenset(node_ids).
    visited: set[str] = set()
    groups: list[list[str]] = []  # her grup, sırada eklenen node_id listesi
    id_to_group: dict[str, int] = {}
    for node_def in nodes_in_order:
        if node_def.node_id in visited:
            continue
        if not node_def.parallel_with:
            groups.append([node_def.node_id])
            id_to_group[node_def.node_id] = len(groups) - 1
            visited.add(node_def.node_id)
            continue
        # Bu node ve parallel_with'lerini aynı gruba topla
        group_members = {node_def.node_id, *node_def.parallel_with}
        # Sıralamayı koru
        ordered_members = [n.node_id for n in nodes_in_order
                           if n.node_id in group_members]
        groups.append(ordered_members)
        for m in ordered_members:
            id_to_group[m] = len(groups) - 1
            visited.add(m)

    # 2) Lineer iskelet — gruplar arası bağla
    for i, group in enumerate(groups):
        # Eğer bir sonraki grup varsa
        if i + 1 < len(groups):
            next_group = groups[i + 1]
            # fan-out + fan-in: bu gruptaki TÜM node'lar → sonraki gruptaki
            # ilk node'a. Eğer sonraki grup paraleldiyse, sonraki grubun
            # her node'una fan-out yap (bu durumda grup edge'i hub gibi davranır).
            for from_id in group:
                # Eğer sonraki grup paralel ise (len > 1) ve önceki grup tek
                # node ise: fan-out (bir → çoklu).
                if len(group) == 1 and len(next_group) > 1:
                    for to_id in next_group:
                        g.add_edge(from_id, to_id)
                # Eğer önceki grup paralel ve sonraki grup tek: fan-in
                elif len(group) > 1 and len(next_group) == 1:
                    g.add_edge(from_id, next_group[0])
                # İkisi de tek → düz zincir
                elif len(group) == 1 and len(next_group) == 1:
                    g.add_edge(from_id, next_group[0])
                # İkisi de paralel → her sol her sağa (cartesian) — bu
                # genelde istenmez ama _wire_edges minimal yaklaşımla
                # destekler.
                else:
                    for to_id in next_group:
                        g.add_edge(from_id, to_id)

    # 3) depends_on override — açıkça yazılan bağımlılıklar
    for node_def in nodes_in_order:
        for dep in (node_def.depends_on or []):
            # Yalnız aynı node mu? skip.
            if dep == node_def.node_id:
                continue
            # Edge'i ekle (LangGraph idempotent değil — duplicate exception
            # olabilir; safety için try)
            try:
                g.add_edge(dep, node_def.node_id)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Execution row helpers (rule_executions tablosu)
# ---------------------------------------------------------------------------


def _open_execution_row(
    *,
    user_id: int,
    rule_id: int,
    event_id: int | None,
    event_type: str | None,
    thread_id: str,
) -> int:
    ts = now_iso()
    new_id = execute_write(
        """
        INSERT INTO rule_executions (
            user_id, rule_id, event_id, event_type, thread_id,
            status, current_node, started_at
        )
        VALUES (?, ?, ?, ?, ?, 'running', 'supervisor', ?)
        """,
        (int(user_id), int(rule_id), event_id, event_type, thread_id, ts),
    )
    return int(new_id)


def _update_execution_row(
    execution_id: int,
    *,
    status: str | None = None,
    current_node: str | None = None,
    approval_id: int | None = None,
    error: str | None = None,
    trace_summary: str | None = None,
    end: bool = False,
    user_decision: str | None = None,
) -> None:
    fields: list[str] = []
    values: list[Any] = []
    if status is not None:
        fields.append("status=?"); values.append(status)
    if current_node is not None:
        fields.append("current_node=?"); values.append(current_node)
    if approval_id is not None:
        fields.append("approval_id=?"); values.append(int(approval_id))
    if error is not None:
        fields.append("error=?"); values.append(error[:500])
    if trace_summary is not None:
        fields.append("trace_summary=?"); values.append(trace_summary[:1200])
    if end:
        fields.append("ended_at=?"); values.append(now_iso())
    if not fields:
        return
    values.append(int(execution_id))
    execute_write(
        f"UPDATE rule_executions SET {', '.join(fields)} WHERE id=?",
        tuple(values),
    )

    # Tur 3: terminal status'a ulaşıldığında rule_learning hook'u.
    # Yalnızca end=True veya status'un terminal olduğu durumda tetikler;
    # waiting_* veya running ara durumlarında çağırmıyoruz.
    if end and status in ("completed", "cancelled", "failed"):
        try:
            ex_row = execute_query(
                "SELECT rule_id FROM rule_executions WHERE id=?",
                (int(execution_id),), one=True,
            )
            if ex_row and ex_row["rule_id"]:
                from rule_learning import record_execution_outcome
                record_execution_outcome(
                    int(ex_row["rule_id"]),
                    execution_status=status,
                    user_decision=user_decision,
                )
        except Exception as exc:
            print(f"[RUNTIME] rule_learning hook skipped: {exc}")


def _persist_trace_events(execution_id: int, events: list[dict]) -> None:
    if not events:
        return
    with db_connection() as conn:
        for ev in events:
            try:
                conn.execute(
                    """
                    INSERT INTO graph_node_traces (
                        execution_id, node_name, node_status, summary,
                        details_json, started_at, ended_at, duration_ms
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(execution_id),
                        ev.get("node", "unknown"),
                        ev.get("status", "ok"),
                        (ev.get("summary") or "")[:400],
                        json.dumps(ev.get("details") or {}, ensure_ascii=False, default=str),
                        ev.get("ts") or now_iso(),
                        ev.get("ts") or now_iso(),
                        ev.get("duration_ms"),
                    ),
                )
            except Exception as exc:
                print(f"[RUNTIME] trace persist failed: {exc}")


def get_execution_traces(execution_id: int, limit: int = 50) -> list[dict]:
    rows = execute_query(
        """
        SELECT * FROM graph_node_traces
        WHERE execution_id=? ORDER BY id ASC LIMIT ?
        """,
        (int(execution_id), int(limit)),
    )
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        try:
            d["details"] = json.loads(d.pop("details_json", "{}") or "{}")
        except json.JSONDecodeError:
            d["details"] = {}
        out.append(d)
    return out


def get_execution(execution_id: int) -> dict | None:
    row = execute_query(
        "SELECT * FROM rule_executions WHERE id=?",
        (int(execution_id),),
        one=True,
    )
    return dict(row) if row else None


def list_executions(
    *,
    user_id: int,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    sql = "SELECT * FROM rule_executions WHERE user_id=?"
    params: list[Any] = [int(user_id)]
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    rows = execute_query(sql, tuple(params))
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Public API — start + resume
# ---------------------------------------------------------------------------


def _config_for_thread(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def start_execution(
    *,
    rule: StructuredRule,
    event: dict[str, Any],
    user_id: int | None = None,
) -> dict:
    """Yeni bir graph execution başlat. interrupt'a kadar veya END'e
    kadar koşar. interrupt durumunda status='waiting_human' döner.
    """
    if rule.id is None:
        raise ValueError("rule must have an id (must be persisted before execution)")

    uid = int(user_id or rule.user_id or 1)
    thread_id = f"rule-{rule.id}-{uuid.uuid4().hex[:8]}"

    execution_id = _open_execution_row(
        user_id=uid,
        rule_id=rule.id,
        event_id=event.get("event_id"),
        event_type=event.get("event_type") or rule.trigger.event_type,
        thread_id=thread_id,
    )

    rule_dict = rule.to_storage_dict()
    state = initial_state(
        rule_id=rule.id,
        user_id=uid,
        org_id=rule.org_id,
        thread_id=thread_id,
        rule_dict=rule_dict,
        event_dict=event,
    )
    state["execution_id"] = execution_id

    graph = build_graph(rule)

    try:
        result = graph.invoke(state, config=_config_for_thread(thread_id))
    except Exception as exc:
        _update_execution_row(
            execution_id, status="failed",
            error=str(exc), end=True,
            current_node="error",
        )
        return {
            "execution_id": execution_id,
            "thread_id": thread_id,
            "status": "failed",
            "error": str(exc)[:240],
        }

    # interrupt: result, durduğu node'a kadar olan state'i döner — graph hâlâ alive
    snapshot = _read_snapshot(graph, thread_id)
    final_status = snapshot.get("status") or "running"
    current_node = snapshot.get("current_node")

    # Trace event'leri DB'ye yansıt
    _persist_trace_events(
        execution_id,
        (snapshot.get("trace_events") or []),
    )

    approval = snapshot.get("approval") or {}
    approval_id = approval.get("approval_id")

    # Status mapping: graph END'e ulaştıysa completed/cancelled/failed;
    # interrupt'ta durduysa waiting_human
    is_ended = _graph_is_finished(graph, thread_id)
    if is_ended:
        # finalize node'u status'u doğru set etmiş olmalı
        if final_status == "running":
            final_status = "completed"
        _update_execution_row(
            execution_id,
            status=final_status,
            current_node=current_node,
            approval_id=approval_id,
            trace_summary=_build_trace_summary(snapshot),
            end=True,
        )
    else:
        # Hangi interrupt'ta durduk? wait_node sonrası mı (waiting_timer)
        # yoksa approval öncesi mi (waiting_human)?
        wait_pending = (
            current_node == "wait"
            and (snapshot.get("metadata") or {}).get("resume_at")
            and not (snapshot.get("metadata") or {}).get("wait_resolved")
        )
        if wait_pending:
            _update_execution_row(
                execution_id,
                status="waiting_timer",
                current_node="wait",
                trace_summary=_build_trace_summary(snapshot),
            )
            final_status = "waiting_timer"
        else:
            _update_execution_row(
                execution_id,
                status="waiting_human",
                current_node=current_node or "approval",
                approval_id=approval_id,
                trace_summary=_build_trace_summary(snapshot),
            )
            final_status = "waiting_human"

    return {
        "execution_id": execution_id,
        "thread_id": thread_id,
        "status": final_status,
        "current_node": current_node,
        "approval_id": approval_id,
        "snapshot": snapshot,
    }


def resume_after_wait(execution_id: int) -> dict:
    """Bekleme süresi dolan bir execution'ı devam ettir.

    Approval resume'dan farkı: state'e karar enjekte etmiyoruz, sadece
    status'u running'e set edip graph'ı invoke ediyoruz. wait_node
    interrupt_before listesinde DEĞİL — yani LangGraph zaten interrupt
    yok, sadece bizim DB row'umuz 'waiting_timer' statüsündeydi. Burada
    rule_executions tablosunu güncelliyoruz + scheduling_service'in
    fired_at flag'ini yansıtıyoruz.

    Önemli not: wait_node aslında graph'ı durdurmuyor — sadece DB'ye bir
    schedule entry yazıp HEMEN devam ediyor (eski davranış). Bu helper,
    o schedule entry tetiklendiğinde "execution hâlâ canlı mı?" diye
    bakar ve canlıysa zaten devam etmiş kabul eder. Eğer
    `metadata.wait_strategy=='persistent'` ise (Phase 2'de yeni), graph
    gerçekten duraklatılmış olur — bu durumda kalan node'ları çalıştırır.
    """
    row = get_execution(execution_id)
    if not row:
        raise ValueError(f"execution {execution_id} not found")

    status = row["status"]
    if status in ("completed", "cancelled", "failed", "archived"):
        return {"execution_id": execution_id, "status": status, "noop": True}

    # Eğer status zaten "waiting_human" ise wait resume değil approval
    # bekliyor — ona dokunma.
    if status == "waiting_human":
        return {
            "execution_id": execution_id,
            "status": status,
            "noop": True,
            "note": "Approval bekleniyor; wait resume atlandı.",
        }

    # waiting_timer: graph gerçekten duraklatıldı (Phase 2 yeni wait
    # strategy). Resume mümkün.
    if status == "waiting_timer":
        return _do_resume_after_wait(execution_id, row)

    # running / scheduled: muhtemelen wait_node zaten geçmiş. No-op.
    return {
        "execution_id": execution_id,
        "status": status,
        "noop": True,
        "note": "Execution zaten ilerlemekteydi.",
    }


def _do_resume_after_wait(execution_id: int, row: dict) -> dict:
    """waiting_timer durumundaki execution'ı gerçekten resume et."""
    from structured_rule import StructuredRule
    rule_row = execute_query(
        "SELECT * FROM structured_rules WHERE id=?",
        (int(row["rule_id"]),),
        one=True,
    )
    if not rule_row:
        _update_execution_row(
            execution_id, status="failed",
            error="rule_not_found_at_resume", end=True,
        )
        return {"execution_id": execution_id, "status": "failed",
                "error": "rule no longer exists"}

    rule = StructuredRule.from_storage(dict(rule_row))
    graph = build_graph(rule)

    # State'i running'e set et — wait_node'un beklediği "delay_satisfied"
    # bayrağını metadata'ya işle.
    graph.update_state(
        _config_for_thread(row["thread_id"]),
        {
            "status": "running",
            "metadata": {"wait_resolved": True, "wait_resolved_at": now_iso()},
        },
    )

    try:
        graph.invoke(None, config=_config_for_thread(row["thread_id"]))
    except Exception as exc:
        _update_execution_row(
            execution_id, status="failed", error=str(exc), end=True,
        )
        return {"execution_id": execution_id, "status": "failed",
                "error": str(exc)[:240]}

    snapshot = _read_snapshot(graph, row["thread_id"])
    _persist_trace_events(
        execution_id, (snapshot.get("trace_events") or [])[-10:],
    )
    final_status = snapshot.get("status") or "running"
    is_ended = _graph_is_finished(graph, row["thread_id"])

    if is_ended:
        _update_execution_row(
            execution_id,
            status=final_status if final_status != "running" else "completed",
            current_node=snapshot.get("current_node"),
            trace_summary=_build_trace_summary(snapshot),
            end=True,
        )
        final_status = final_status if final_status != "running" else "completed"
    else:
        # Graph bir sonraki interrupt'ta durdu — büyük olasılıkla
        # approval interrupt_before. State.approval var ve decision
        # pending ise waiting_human.
        approval = snapshot.get("approval") or {}
        if approval.get("decision") in (None, "pending"):
            final_status = "waiting_human"
        _update_execution_row(
            execution_id,
            status=final_status,
            current_node=snapshot.get("current_node"),
            approval_id=approval.get("approval_id"),
            trace_summary=_build_trace_summary(snapshot),
        )

    return {
        "execution_id": execution_id,
        "status": final_status,
        "current_node": snapshot.get("current_node"),
        "resumed_from": "wait",
    }


def resume_execution(
    execution_id: int,
    *,
    approval_decision: str | None = None,
    feedback: str | None = None,
    edited_content: dict | None = None,
    decided_by: str = "operator",
) -> dict:
    """Duraklamış (waiting_human) bir execution'ı resume et.

    approval_decision: 'approved' | 'rejected' | 'edited'
    """
    row = get_execution(execution_id)
    if not row:
        raise ValueError(f"execution {execution_id} not found")
    if row["status"] not in ("waiting_human",):
        raise ValueError(f"execution {execution_id} is not paused (status={row['status']})")

    thread_id = row["thread_id"]

    # Rule'u snapshot'tan tekrar yükle (DB'den de okunabilir; thread güvende)
    from structured_rule import StructuredRule
    rule_row = execute_query(
        "SELECT * FROM structured_rules WHERE id=?",
        (int(row["rule_id"]),),
        one=True,
    )
    if not rule_row:
        raise ValueError("rule not found for execution")
    rule = StructuredRule.from_storage(dict(rule_row))
    graph = build_graph(rule)

    # State patch: approval.decision = 'approved' (vs.)
    decision = (approval_decision or "approved").lower()
    if decision not in ("approved", "rejected", "edited"):
        raise ValueError("approval_decision must be approved|rejected|edited")

    # update_state ile mevcut state'in üstüne approval bilgisini bindir
    snapshot = _read_snapshot(graph, thread_id)
    current_approval = snapshot.get("approval") or {}
    new_approval = {
        **current_approval,
        "decision": decision,
        "decided_by": decided_by,
        "feedback": feedback,
        "edited_content": edited_content,
    }

    # LangGraph state patch — update_state mevcut state'i bind eder
    graph.update_state(
        _config_for_thread(thread_id),
        {"approval": new_approval, "status": "running"},
    )

    # Resume
    try:
        graph.invoke(None, config=_config_for_thread(thread_id))
    except Exception as exc:
        _update_execution_row(
            execution_id, status="failed",
            error=str(exc), end=True,
        )
        return {
            "execution_id": execution_id,
            "status": "failed",
            "error": str(exc)[:240],
        }

    snapshot = _read_snapshot(graph, thread_id)
    _persist_trace_events(
        execution_id,
        (snapshot.get("trace_events") or [])[-15:],  # son ~15 event yeterli
    )
    final_status = snapshot.get("status") or "completed"
    # Tur 3: operatörün approve/reject kararını rule_learning hook'una geçir
    user_decision_map = {"approved": "approved", "rejected": "rejected"}.get(decision)
    if _graph_is_finished(graph, thread_id):
        _update_execution_row(
            execution_id,
            status=final_status,
            current_node=snapshot.get("current_node"),
            trace_summary=_build_trace_summary(snapshot),
            end=True,
            user_decision=user_decision_map,
        )
    else:
        _update_execution_row(
            execution_id,
            status=final_status,
            current_node=snapshot.get("current_node"),
            trace_summary=_build_trace_summary(snapshot),
        )

    return {
        "execution_id": execution_id,
        "status": final_status,
        "current_node": snapshot.get("current_node"),
        "decision": decision,
        "snapshot": snapshot,
    }


# ---------------------------------------------------------------------------
# LangGraph snapshot helpers
# ---------------------------------------------------------------------------


def _read_snapshot(graph, thread_id: str) -> dict:
    """Graph'ın güncel state snapshot'unu döndür."""
    try:
        state = graph.get_state(_config_for_thread(thread_id))
    except Exception:
        return {}
    if state is None:
        return {}
    return dict(state.values or {})


def _graph_is_finished(graph, thread_id: str) -> bool:
    """Graph END'e ulaştı mı?"""
    try:
        state = graph.get_state(_config_for_thread(thread_id))
        if state is None:
            return False
        # next == () demek artık beklenen node yok = bitti
        nxt = getattr(state, "next", None)
        return not nxt
    except Exception:
        return False


def _build_trace_summary(snapshot: dict) -> str:
    """Trace listesinden kısa Türkçe özet üret."""
    events = snapshot.get("trace_events") or []
    if not events:
        return ""
    parts = []
    for ev in events[-6:]:
        node = ev.get("node", "—")
        status = ev.get("status", "—")
        summary = (ev.get("summary") or "").split(".")[0][:80]
        parts.append(f"{node}:{status}:{summary}")
    return " | ".join(parts)[:1200]


# ---------------------------------------------------------------------------
# Convenience: dry-run preview without persistence
# ---------------------------------------------------------------------------


def dry_run_preview(rule: StructuredRule, event: dict[str, Any]) -> dict:
    """Test endpoint için — rule'u koş ama persistent execution row açma.

    Hâlâ checkpoint atılır (LangGraph zorunluluğu), ama operatör buna
    bir "tek seferlik" gibi davranabilir. Approval node'una girilirse
    durum waiting_human döner.
    """
    thread_id = f"dryrun-{uuid.uuid4().hex[:10]}"
    rule_dict = rule.to_storage_dict()
    state = initial_state(
        rule_id=rule.id or 0,
        user_id=rule.user_id or 1,
        org_id=rule.org_id,
        thread_id=thread_id,
        rule_dict=rule_dict,
        event_dict=event,
    )
    graph = build_graph(rule)
    try:
        graph.invoke(state, config=_config_for_thread(thread_id))
    except Exception as exc:
        return {"status": "failed", "error": str(exc)[:240], "trace": []}
    snapshot = _read_snapshot(graph, thread_id)
    return {
        "status": snapshot.get("status") or "completed",
        "current_node": snapshot.get("current_node"),
        "trace": snapshot.get("trace_events") or [],
        "content": snapshot.get("content"),
        "risk": snapshot.get("risk"),
        "publish": snapshot.get("publish"),
    }
