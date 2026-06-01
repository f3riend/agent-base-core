from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from .approvals import ApprovalService
from .assistant_narrative import AssistantNarrativeComposer
from .conversation_memory_summarizer import ConversationMemorySummarizer
from .context_builder import OperationContextBuilder
from .events import EventEnvelope
from .fsm import OperationState, guard_transition
from .initiative_engine import InitiativeEngine
from .llm_gateway import LLMGateway
from .memory import MemoryStore
from .observability import ObservabilityService
from .operation_semantics import OperationSemantics
from .policy_engine import PolicyEngine
from .projections import ProjectionService
from .publisher import StreamPublisher
from .registry import ToolRegistry
from .semantic_operation_interpreter import SemanticOperationInterpreter
from .tools.contracts import ToolContext, ToolResult

runtime_logger = logger.bind(module="runtime-orchestrator")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperationStore:
    def __init__(self) -> None:
        self.operations: dict[str, dict] = {}
        self.events: dict[str, list[dict]] = {}
        self.subscribers: dict[str, list[asyncio.Queue]] = {}
        self._seq = 0
        self._lock = asyncio.Lock()

    async def create_operation(self, workspace_id: str, conversation_id: str, entity_type: str, entity_id: str) -> dict:
        operation_id = f"op_{uuid.uuid4().hex[:12]}"
        row = {
            "operation_id": operation_id,
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "state": OperationState.created.value,
            "status": "running",
            "progress": 0,
            "result": None,
            "created_at": _now_iso(),
        }
        async with self._lock:
            self.operations[operation_id] = row
            self.events[operation_id] = []
            self.subscribers.setdefault(operation_id, [])
        return dict(row)

    async def get_operation(self, workspace_id: str, operation_id: str) -> dict | None:
        async with self._lock:
            row = self.operations.get(operation_id)
            if row is None or row.get("workspace_id") != workspace_id:
                return None
            return dict(row)

    async def transition_state(self, workspace_id: str, operation_id: str, target: str) -> str:
        async with self._lock:
            row = self.operations.get(operation_id)
            if row is None or row.get("workspace_id") != workspace_id:
                raise ValueError("operation_not_found")
            current = str(row.get("state") or OperationState.created.value)
            guard_transition(current, target)
            row["state"] = target
            return target

    async def append_event(self, envelope: EventEnvelope) -> dict:
        async with self._lock:
            op = self.operations.get(envelope.operation_id)
            if op is None or op.get("workspace_id") != envelope.workspace_id:
                raise ValueError("operation_not_found")
            self._seq += 1
            row = envelope.model_dump(mode="python")
            row["seq"] = self._seq
            bucket = self.events.setdefault(envelope.operation_id, [])
            bucket.append(row)
            queues = list(self.subscribers.get(envelope.operation_id) or [])
        for q in queues:
            try:
                q.put_nowait(row)
            except asyncio.QueueFull:
                continue
        return row

    async def list_events(self, workspace_id: str, operation_id: str) -> list[dict]:
        async with self._lock:
            op = self.operations.get(operation_id)
            if op is None or op.get("workspace_id") != workspace_id:
                return []
            return [dict(x) for x in (self.events.get(operation_id) or [])]

    async def subscribe(self, workspace_id: str, operation_id: str) -> asyncio.Queue:
        async with self._lock:
            op = self.operations.get(operation_id)
            if op is None or op.get("workspace_id") != workspace_id:
                raise ValueError("operation_not_found")
            q: asyncio.Queue = asyncio.Queue(maxsize=300)
            self.subscribers.setdefault(operation_id, []).append(q)
            return q

    async def unsubscribe(self, operation_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            rows = self.subscribers.get(operation_id) or []
            if queue in rows:
                rows.remove(queue)
            self.subscribers[operation_id] = rows

    async def update_operation(self, workspace_id: str, operation_id: str, **fields: Any) -> None:
        async with self._lock:
            row = self.operations.get(operation_id)
            if row is None or row.get("workspace_id") != workspace_id:
                return
            row.update(fields)


class Orchestrator:
    def __init__(
        self,
        store: OperationStore,
        registry: ToolRegistry,
        policy_engine: PolicyEngine,
        memory: MemoryStore,
        approvals: ApprovalService,
        observability: ObservabilityService,
    ) -> None:
        self.store = store
        self.registry = registry
        self.policy_engine = policy_engine
        self.memory = memory
        self.approvals = approvals
        self.observability = observability
        self.projections = ProjectionService()
        self.narrative = AssistantNarrativeComposer()
        self.context_builder = OperationContextBuilder()
        self.llm = LLMGateway()
        self.memory_summarizer = ConversationMemorySummarizer()
        self.initiative_engine = InitiativeEngine()
        self.operation_semantics = OperationSemantics()
        self.semantic_interpreter = SemanticOperationInterpreter(self.llm, self.operation_semantics)

    def parse_intent(self, message: str) -> str:
        txt = (message or "").lower()
        if "onayla" in txt or "approve" in txt:
            return "approve_campaign"
        if (
            "instagram" in txt
            and ("post" in txt or "reel" in txt or "hikaye" in txt)
            and any(k in txt for k in ("hazirla", "olustur", "kampanya", "yayin"))
        ):
            return "create_campaign"
        if "anneler gunu" in txt or "15 may" in txt:
            return "create_campaign"
        if "optimiz" in txt:
            return "optimize_campaign"
        if "kampanya" in txt:
            return "create_campaign"
        if "yorum" in txt:
            return "analyze_reviews"
        if "banner" in txt:
            return "generate_banner"
        return "general_analysis"

    def tool_chain(self, intent: str, semantics: dict[str, Any] | None = None) -> list[str]:
        sem = dict(semantics or {})
        operation_action = str(sem.get("operation_action") or "")
        domain = str(sem.get("domain") or "").strip().lower()
        mapping = {
            "create_campaign": ["analyze_product", "generate_strategy", "generate_caption", "generate_image", "create_approval"],
            "approve_campaign": ["schedule_post", "publish_queue", "event_generated"],
            "analyze_reviews": ["analyze_reviews", "detect_complaint_clusters", "generate_mitigation_plan"],
            "generate_banner": ["analyze_product", "generate_banner_copy", "generate_banner_visual"],
            "optimize_campaign": ["load_previous_campaign", "optimize_strategy", "generate_caption"],
            "general_analysis": ["analyze_product", "summarize_operational_insights"],
        }
        chain = list(mapping.get(intent) or mapping["general_analysis"])
        if intent == "create_campaign" and operation_action == "save_draft":
            chain = ["analyze_product", "generate_strategy", "generate_caption", "generate_image", "event_generated"]
        elif intent == "create_campaign" and operation_action == "create_and_schedule":
            chain = ["analyze_product", "generate_strategy", "generate_caption", "generate_image", "create_approval", "schedule_post"]
        elif intent == "create_campaign" and operation_action == "create_and_publish":
            chain = [
                "analyze_product",
                "generate_strategy",
                "generate_caption",
                "generate_image",
                "create_approval",
                "schedule_post",
                "publish_queue",
                "event_generated",
            ]
        elif intent == "approve_campaign" and operation_action == "schedule_content":
            chain = ["schedule_post"]
        elif intent == "approve_campaign" and operation_action == "publish_content":
            chain = ["schedule_post", "publish_queue", "event_generated"]
        domain_whitelist = {
            "analytics": {
                "analyze_product",
                "summarize_operational_insights",
                "analyze_reviews",
                "detect_complaint_clusters",
                "generate_mitigation_plan",
                "load_previous_campaign",
            },
            "support": {"analyze_reviews", "detect_complaint_clusters", "generate_mitigation_plan"},
            "strategy": {"analyze_product", "load_previous_campaign", "optimize_strategy", "summarize_operational_insights"},
            "content_ops": {
                "analyze_product",
                "generate_strategy",
                "generate_caption",
                "generate_image",
                "generate_banner_copy",
                "generate_banner_visual",
                "create_approval",
            },
            "scheduling": {"schedule_post", "event_generated"},
            "publishing": {"schedule_post", "publish_queue", "event_generated"},
        }
        allowed = domain_whitelist.get(domain)
        if not allowed:
            return chain
        filtered = [tool for tool in chain if tool in allowed]
        if filtered:
            return filtered
        domain_defaults = {
            "analytics": ["analyze_product", "summarize_operational_insights"],
            "support": ["analyze_reviews", "detect_complaint_clusters", "generate_mitigation_plan"],
            "strategy": ["load_previous_campaign", "optimize_strategy"],
            "content_ops": ["analyze_product", "generate_strategy", "generate_caption", "generate_image"],
            "scheduling": ["schedule_post"],
            "publishing": ["schedule_post", "publish_queue", "event_generated"],
        }
        return list(domain_defaults.get(domain) or chain)

    async def emit(
        self,
        workspace_id: str,
        operation_id: str,
        event_type: str,
        correlation_id: str,
        causation_id: str,
        entity_type: str,
        entity_id: str,
        payload: dict,
    ) -> dict:
        env = EventEnvelope(
            event_type=event_type,
            workspace_id=workspace_id,
            operation_id=operation_id,
            entity_type=entity_type,
            entity_id=entity_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            payload=payload,
        )
        return await self.store.append_event(env)

    async def run_operation(
        self,
        workspace_id: str,
        operation_id: str,
        conversation_id: str,
        message: str,
        user_id: str,
        user_role: str,
        context: dict,
        history: list[dict[str, Any]] | None = None,
    ) -> None:
        def _weight_from_severity(severity: str) -> str:
            sev = str(severity or "").lower()
            if sev == "critical":
                return "critical"
            if sev == "warning":
                return "important"
            if sev in {"opportunity", "info"}:
                return "light"
            return "normal"

        def _weight_from_final(tone: str, risk_count: int, confidence: float) -> str:
            t = str(tone or "").lower()
            if t in {"alert"} or risk_count >= 3:
                return "critical"
            if t in {"warning"} or risk_count >= 1 or confidence >= 0.85:
                return "important"
            return "normal"

        def _should_emit_start_message(intent_name: str) -> bool:
            return intent_name in {"create_campaign", "approve_campaign", "optimize_campaign", "generate_banner"}

        def _should_emit_initiative(item: dict[str, Any]) -> bool:
            severity = str(item.get("severity") or "").lower()
            confidence = float(item.get("confidence") or 0.0)
            impact = float(item.get("impact") or 0.0)
            if severity in {"critical", "warning"}:
                return True
            return confidence >= 0.76 and impact >= 0.78

        correlation_id = conversation_id or operation_id
        entity_type = "product"
        entity_id = str(context.get("product_id") or "")
        context["chat_history"] = list(history or [])
        semantics = await self.semantic_interpreter.interpret(message=message, context=context)
        runtime_logger.info(
            "operation.start operation_id={} conversation_id={} product_id={} message={} semantics={}",
            operation_id,
            conversation_id,
            entity_id,
            str(message or "")[:320],
            {
                "intent": semantics.get("intent"),
                "domain": semantics.get("domain"),
                "operation_action": semantics.get("operation_action"),
                "scheduled_at": semantics.get("scheduled_at") or semantics.get("target_date"),
                "requires_clarification": semantics.get("requires_clarification"),
            },
        )
        domain = str(semantics.get("domain") or "").strip().lower()
        if domain in {"analytics", "strategy", "support", "general_chat"}:
            context["mode"] = "analiz"
        elif domain in {"content_ops", "scheduling", "publishing"}:
            context["mode"] = "operasyon"
        if bool(semantics.get("requires_clarification")):
            clarification = str(semantics.get("clarification_question") or "Devam etmeden once formati netlestirelim mi?")
            runtime_logger.warning(
                "operation.clarification_required operation_id={} question={}",
                operation_id,
                clarification,
            )
            await self.emit(
                workspace_id,
                operation_id,
                "assistant_message_complete",
                correlation_id,
                "",
                entity_type,
                entity_id,
                {
                    "type": "analysis",
                    "tone": "analysis",
                    "intent": str(semantics.get("intent") or "general_analysis"),
                    "confidence": float(semantics.get("confidence") or semantics.get("semantic_confidence") or 0.45),
                    "message": clarification,
                    "sections": [],
                    "suggested_actions": [],
                    "quick_replies": ["Instagram feed", "Hikaye", "Reels"],
                    "quick_actions": [],
                    "related_entities": [{"type": "product", "id": entity_id}] if entity_id else [],
                    "weight": "light",
                    "interruption_strength": "soft",
                    "stream_id": f"asst_{operation_id}_clarify",
                },
            )
            await self.store.transition_state(workspace_id, operation_id, OperationState.completed.value)
            done_payload = {
                "conversation_id": conversation_id,
                "events": await self.store.list_events(workspace_id, operation_id),
                "tool_states": [],
                "cards": [],
                "messages": [{"role": "assistant", "content": clarification, "timestamp": _now_iso()}],
                "pending_actions": [],
                "operation_state": OperationState.completed.value,
                "analysis_summary": clarification,
                "reasoning_response": clarification,
                "recommendation_summary": "",
                "suggested_actions": [],
                "detected_risks": [],
                "detected_opportunities": [],
                "operation_semantics": dict(semantics),
            }
            await self.store.update_operation(
                workspace_id,
                operation_id,
                status="completed",
                progress=100,
                result=done_payload,
            )
            await self.emit(
                workspace_id,
                operation_id,
                "operation.completed",
                correlation_id,
                "",
                entity_type,
                entity_id,
                {"state": OperationState.completed.value},
            )
            await self.emit(workspace_id, operation_id, "done", correlation_id, "", entity_type, entity_id, done_payload)
            runtime_logger.info(
                "operation.done operation_id={} state={} tools_executed={} pending_actions={}",
                operation_id,
                OperationState.completed.value,
                0,
                0,
            )
            return
        intent = str(semantics.get("intent") or self.parse_intent(message))
        mode = str(context.get("mode") or "analiz").strip().lower()
        context["active_operation_id"] = str(semantics.get("active_operation_id") or "")
        context["active_campaign_id"] = str(semantics.get("active_campaign_id") or "")
        context["active_asset_id"] = str(semantics.get("active_asset_id") or "")
        context["last_pending_approval"] = str(semantics.get("last_pending_approval") or "")
        lowered_message = str(message or "").lower()
        is_reschedule_followup = (
            domain in {"scheduling", "publishing"}
            and bool(context.get("active_campaign_id"))
            and any(k in lowered_message for k in ("gun", "yarin", "hafta", "ayin", "aksam", "sabah", "al", "ertele"))
        )
        if is_reschedule_followup:
            context["target_scheduled_post_id"] = str(context.get("active_campaign_id") or "")
        context["operation_semantics"] = dict(semantics)
        chain = self.tool_chain(intent, semantics)
        runtime_logger.info(
            "operation.plan operation_id={} intent={} domain={} mode={} tool_chain={}",
            operation_id,
            intent,
            domain,
            str(context.get("mode") or ""),
            chain,
        )
        entity_memory = self.memory.get_entity_memory(workspace_id, entity_type, entity_id) if entity_id else []
        await self.store.transition_state(workspace_id, operation_id, OperationState.queued.value)
        await self.emit(workspace_id, operation_id, "operation.queued", correlation_id, "", entity_type, entity_id, {"intent": intent})
        await self.store.transition_state(workspace_id, operation_id, OperationState.running.value)
        await self.emit(workspace_id, operation_id, "operation.running", correlation_id, "", entity_type, entity_id, {"tools": chain})

        tool_states: list[dict] = []
        cards: list[dict] = []
        messages: list[dict] = []
        pending_actions: list[dict] = []
        if _should_emit_start_message(intent):
            await self.emit(
                workspace_id,
                operation_id,
                "assistant_message_complete",
                correlation_id,
                "",
                entity_type,
                entity_id,
                {
                    "type": "analysis",
                    "tone": "analysis",
                    "intent": intent,
                    "confidence": 0.64,
                    "message": "Operasyonu baslattim. Net etkiye odaklanip gerektikce paylasacagim.",
                    "sections": [],
                    "suggested_actions": [],
                    "quick_replies": ["Devam et", "Detaylari goster"],
                    "quick_actions": [],
                    "related_entities": [{"type": "product", "id": entity_id}] if entity_id else [],
                    "weight": "light",
                    "interruption_strength": "soft",
                    "stream_id": f"asst_{operation_id}_start",
                },
            )

        proactive_items = self.initiative_engine.evaluate(context=context, entity_memory=entity_memory)
        filtered_proactive = [item for item in proactive_items if _should_emit_initiative(item)]
        for idx, item in enumerate(filtered_proactive):
            severity = str(item.get("severity") or "insight")
            tone_map = {
                "info": "insight",
                "insight": "insight",
                "warning": "warning",
                "opportunity": "insight",
                "critical": "alert",
            }
            confidence = float(item.get("confidence") or 0.72)
            initiative_message = str(item.get("message") or "")
            if confidence < 0.72 and initiative_message:
                initiative_message = f"Ilk sinyaller {initiative_message[:1].lower() + initiative_message[1:]}"
            follow_up = str(item.get("follow_up_question") or "") if bool(item.get("include_follow_up")) else ""
            payload = {
                "stream_id": f"asst_{operation_id}_initiative_{idx}",
                "type": "insight",
                "tone": tone_map.get(severity, "insight"),
                "severity": severity,
                "intent": intent,
                "mode": mode,
                "confidence": confidence,
                "message": initiative_message,
                "sections": [],
                "suggested_actions": list(item.get("suggested_actions") or []),
                "quick_replies": ["Bunu ac", "Detaylandir", "Hemen ilerleyelim"],
                "quick_actions": list(item.get("quick_actions") or []),
                "related_entities": list(item.get("related_entities") or []),
                "follow_up_question": follow_up,
                "weight": _weight_from_severity(severity),
                "interruption_strength": "strong" if severity in {"critical", "warning"} else "soft",
            }
            await self.emit(
                workspace_id,
                operation_id,
                "assistant_message_complete",
                correlation_id,
                "",
                entity_type,
                entity_id,
                payload,
            )
            if idx < len(filtered_proactive) - 1:
                await asyncio.sleep(0.12)
            if entity_id:
                self.memory.append_entity_memory(
                    workspace_id,
                    entity_type,
                    entity_id,
                    {
                        "kind": "initiative",
                        "code": str(item.get("code") or ""),
                        "severity": severity,
                        "message": str(item.get("message") or ""),
                        "timestamp": _now_iso(),
                    },
                )

        total = max(1, len(chain))
        for idx, tool in enumerate(chain):
            start = time.perf_counter()
            meta_handler = self.registry.get(tool)
            if meta_handler is None:
                runtime_logger.error(
                    "tool.missing operation_id={} index={}/{} tool={}",
                    operation_id,
                    idx + 1,
                    total,
                    tool,
                )
                await self.emit(workspace_id, operation_id, "tool.missing", correlation_id, "", entity_type, entity_id, {"tool": tool})
                continue
            metadata, handler = meta_handler
            policy = self.policy_engine.evaluate(metadata, user_role)
            if not policy.allowed:
                runtime_logger.warning(
                    "tool.blocked operation_id={} index={}/{} tool={} reason={} user_role={}",
                    operation_id,
                    idx + 1,
                    total,
                    tool,
                    policy.reason,
                    user_role,
                )
                await self.emit(
                    workspace_id,
                    operation_id,
                    "policy.denied",
                    correlation_id,
                    "",
                    entity_type,
                    entity_id,
                    {"tool": tool, "reason": policy.reason},
                )
                continue
            runtime_logger.info(
                "tool.start operation_id={} index={}/{} tool={} intent={} provider={} requires_approval={}",
                operation_id,
                idx + 1,
                total,
                tool,
                intent,
                str(getattr(metadata, "provider", "")),
                bool(getattr(metadata, "requires_approval", False)),
            )
            thinking_payload = self.narrative.compose_thinking(tool)
            await self.emit(
                workspace_id,
                operation_id,
                "thinking",
                correlation_id,
                "",
                entity_type,
                entity_id,
                {"message": str(thinking_payload.get("message") or "")},
            )
            await self.emit(
                workspace_id,
                operation_id,
                "tool_state",
                correlation_id,
                "",
                entity_type,
                entity_id,
                {
                    "tool": tool,
                    "status": "running",
                    "message": str(thinking_payload.get("message") or ""),
                },
            )
            tool_ctx = ToolContext(
                workspace_id=workspace_id,
                operation_id=operation_id,
                conversation_id=conversation_id,
                user_id=user_id,
                user_role=user_role,
                message=message,
                intent=intent,
                entity_type=entity_type,
                entity_id=entity_id,
                context=context,
                metadata=metadata.model_dump(mode="python"),
            )
            result: ToolResult = await handler(tool_ctx)
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            result_output = dict(result.output or {})
            runtime_logger.info(
                "tool.done operation_id={} tool={} status={} latency_ms={} signal={} event={} provider={} image_url={} real={}",
                operation_id,
                tool,
                str(result.status),
                latency_ms,
                str(result_output.get("signal") or ""),
                str(result_output.get("event") or ""),
                str((result.metadata or {}).get("provider") or result_output.get("provider") or ""),
                bool(result.image_url),
                (result.metadata or {}).get("real"),
            )
            self.observability.record(workspace_id, operation_id, "tool_latency_ms", latency_ms, {"tool": tool})
            tool_update = self.narrative.compose_tool_update(tool, str(result.status), dict(result.output or {}))
            tool_state = {
                "tool": tool,
                "status": result.status,
                "timestamp": _now_iso(),
                "description": str(tool_update.get("message") or f"{tool} {result.status}"),
                "output": dict(result.output or {}),
                "preview": result.preview,
            }
            tool_states.append(tool_state)
            await self.emit(workspace_id, operation_id, "tool_state", correlation_id, "", entity_type, entity_id, tool_state)
            if result.image_url:
                await self.emit(
                    workspace_id,
                    operation_id,
                    "generated_asset",
                    correlation_id,
                    "",
                    entity_type,
                    entity_id,
                    {
                        "tool": tool,
                        "image_url": result.image_url,
                        "preview": result.preview or result.image_url,
                        "message": "Gorsel varlik olusturuldu ve operasyona eklendi.",
                        "metadata": result.metadata,
                    },
                )
                self.memory.append_entity_memory(
                    workspace_id,
                    entity_type,
                    entity_id,
                    {
                        "kind": "asset",
                        "tool": tool,
                        "image_url": result.image_url,
                        "timestamp": _now_iso(),
                    },
                )
            output = dict(result.output or {})
            context_updates = output.get("context_updates")
            if isinstance(context_updates, dict):
                context.update(context_updates)
                lifecycle_state = str(context_updates.get("lifecycle_state") or "").strip().lower()
                if lifecycle_state == "pending_approval":
                    try:
                        await self.store.transition_state(workspace_id, operation_id, OperationState.waiting_approval.value)
                    except Exception:
                        pass
                elif lifecycle_state == "scheduled":
                    try:
                        await self.store.transition_state(workspace_id, operation_id, OperationState.scheduled.value)
                    except Exception:
                        pass
            event_type = str(output.get("event") or "").strip()
            if event_type:
                description = str(output.get("description") or output.get("summary") or "Operasyon adimi tamamlandi.").strip()
                await self.emit(
                    workspace_id,
                    operation_id,
                    "event",
                    correlation_id,
                    "",
                    entity_type,
                    entity_id,
                    {
                        "type": event_type,
                        "description": description,
                        "tool": tool,
                        "timestamp": _now_iso(),
                        "meta": dict(output.get("meta") or {}),
                    },
                )
                if entity_id and event_type in {
                    "campaign_created",
                    "asset_generated",
                    "scheduled_post_created",
                    "scheduled_post_updated",
                    "approval_requested",
                    "content_queued",
                    "content_published",
                    "draft_saved",
                }:
                    self.memory.append_entity_memory(
                        workspace_id,
                        entity_type,
                        entity_id,
                        {
                            "kind": event_type,
                            "summary": description,
                            "tool": tool,
                            "timestamp": _now_iso(),
                            "meta": dict(output.get("meta") or {}),
                        },
                    )
            card_payload = output.get("card")
            if isinstance(card_payload, dict):
                await self.emit(
                    workspace_id,
                    operation_id,
                    "card",
                    correlation_id,
                    "",
                    entity_type,
                    entity_id,
                    {
                        "title": str(card_payload.get("title") or "Operasyon Karti"),
                        "description": str(card_payload.get("description") or ""),
                        "preview_image": str(card_payload.get("preview_image") or ""),
                        "meta": dict(card_payload.get("meta") or {}),
                    },
                )
            pending_payload = output.get("pending_action")
            if isinstance(pending_payload, dict):
                pending = {
                    "id": str(pending_payload.get("id") or f"pending_{uuid.uuid4().hex[:8]}"),
                    "title": str(pending_payload.get("title") or "Onay bekleniyor"),
                    "status": str(pending_payload.get("status") or "pending"),
                    "timestamp": _now_iso(),
                }
                pending_actions.append(pending)
                await self.emit(
                    workspace_id,
                    operation_id,
                    "pending_action",
                    correlation_id,
                    "",
                    entity_type,
                    entity_id,
                    pending,
                )
            if policy.approval_required:
                runtime_logger.warning(
                    "tool.approval_required operation_id={} tool={} title={}",
                    operation_id,
                    tool,
                    str(output.get("approval_title") or f"{tool} adimi icin onay bekleniyor"),
                )
                approval = self.approvals.create(workspace_id, operation_id)
                approval_title = str(output.get("approval_title") or f"{tool} adimi icin onay bekleniyor")
                pending_actions.append(
                    {
                        "id": approval["approval_id"],
                        "title": approval_title,
                        "status": "waiting_approval",
                        "timestamp": _now_iso(),
                    }
                )
                await self.store.transition_state(workspace_id, operation_id, OperationState.waiting_approval.value)
                await self.emit(workspace_id, operation_id, "approval.created", correlation_id, "", entity_type, entity_id, approval)
                await self.emit(
                    workspace_id,
                    operation_id,
                    "pending_action",
                    correlation_id,
                    "",
                    entity_type,
                    entity_id,
                    {
                        "id": approval["approval_id"],
                        "title": approval_title,
                        "status": "waiting_approval",
                        "timestamp": _now_iso(),
                    },
                )

            progress = int(((idx + 1) / total) * 80)
            await self.store.update_operation(workspace_id, operation_id, progress=progress)
            await self.emit(workspace_id, operation_id, "operation.progress", correlation_id, "", entity_type, entity_id, {"progress": progress})
            # Strict approval flow: if a step enters pending state,
            # stop the remaining chain and wait for explicit user approval.
            if str(result.status or "").lower() == "pending":
                runtime_logger.warning(
                    "operation.paused_pending_approval operation_id={} tool={} progress={}",
                    operation_id,
                    tool,
                    progress,
                )
                break

        context["chat_history"] = list(history or [])
        context["last_user_message"] = message
        if entity_id:
            context["previous_operations"] = self.memory.get_entity_memory(workspace_id, entity_type, entity_id)
        temporal_signals: list[str] = []
        predictive_signals: list[str] = []
        try:
            trend = float(context.get("product_trend_pct") or 0.0)
            if trend < 0:
                temporal_signals.append("Son gunlerde dusus sinyali gucleniyor.")
                predictive_signals.append("Egilim korunursa tekrar siparis davranisinda ilave dusus olusabilir.")
            if trend > 0:
                temporal_signals.append("Son gunlerde pozitif ivme yeniden goruluyor.")
                predictive_signals.append("Ivme korunursa donusumde olcumlu iyilesme gorulebilir.")
        except Exception:
            pass
        try:
            overview = dict(context.get("product_overview") or {})
            conversion_delta = float(overview.get("conversionDelta") or context.get("conversion_delta") or 0.0)
            return_rate = float(overview.get("returnRate") or 0.0)
            if conversion_delta < 0:
                predictive_signals.append("Donusumde asamali erozyon sinyali var.")
            if return_rate >= 4.5:
                predictive_signals.append("Iade maliyeti baskisi buyuyebilir.")
        except Exception:
            pass
        temporal_signals.extend(
            [str(x.get("text") or "").strip() for x in (context.get("product_insights") or []) if str(x.get("text") or "").strip()][:3]
        )
        context["temporal_signals"] = temporal_signals
        context["predictive_signals"] = predictive_signals[:4]

        cards.append(
            {
                "type": "text",
                "title": "Operation Summary",
                "description": f"Intent {intent} executed with {len(tool_states)} tool states.",
                "actions": [],
                "preview_image": None,
            }
        )
        final_narrative = self.narrative.compose_final(
            intent=intent,
            context=context,
            tool_states=tool_states,
            pending_actions=pending_actions,
        )
        context["domain_insights"] = list(final_narrative.get("domain_insights") or [])
        context["campaign_intelligence"] = list(final_narrative.get("campaign_intelligence") or [])
        context["segment_awareness"] = list(final_narrative.get("segment_awareness") or [])
        context["category_family"] = str(final_narrative.get("category_family") or "")
        context["category_label"] = str(final_narrative.get("category_label") or "")
        context["evidence_signals"] = list(final_narrative.get("evidence_signals") or [])
        context["confidence_phrase"] = str(final_narrative.get("confidence_phrase") or "")
        context["trust_signal_strength"] = str(final_narrative.get("trust_signal_strength") or "")
        context["reasoning_transparency"] = str(final_narrative.get("reasoning_transparency") or "")
        context["pending_actions"] = list(pending_actions)
        conversation_memory_summary = self.memory_summarizer.summarize(list(history or []))
        llm_text = ""
        stream_id = f"asst_{operation_id}_final"
        final_conf = float(final_narrative.get("confidence") or 0.7)
        final_weight = _weight_from_final(
            str(final_narrative.get("tone") or ""),
            len(list(final_narrative.get("detected_risks") or [])),
            final_conf,
        )
        expertise_level = str(final_narrative.get("expertise_level") or "operational")
        expertise_reason = str(final_narrative.get("expertise_reason") or "")
        response_mode = str(final_narrative.get("response_mode") or "detailed")
        response_word_target = int(final_narrative.get("response_word_target") or 130)

        async def _emit_chunk(delta: str) -> None:
            await self.emit(
                workspace_id,
                operation_id,
                "assistant_message_chunk",
                correlation_id,
                "",
                entity_type,
                entity_id,
                {"stream_id": stream_id, "delta": delta, "weight": final_weight},
            )

        try:
            llm_prompt = self.context_builder.build_user_prompt(
                message=message,
                intent=intent,
                context=context,
                tool_states=tool_states,
                pending_actions=pending_actions,
                detected_risks=list(final_narrative.get("detected_risks") or []),
                detected_opportunities=list(final_narrative.get("detected_opportunities") or []),
                suggested_actions=list(final_narrative.get("suggested_actions") or []),
                previous_assistant_summary=str(final_narrative.get("analysis_summary") or ""),
                conversation_memory_summary=conversation_memory_summary,
                mode=mode,
                response_mode=response_mode,
                response_word_target=response_word_target,
                expertise_level=expertise_level,
            )
            llm_text = await self.llm.generate_with_stream(
                system_prompt=self.context_builder.SYSTEM_PROMPT,
                user_prompt=llm_prompt,
                on_chunk=_emit_chunk,
            )
        except Exception:
            llm_text = str(final_narrative.get("message") or "").strip()
            if llm_text:
                await _emit_chunk(llm_text)
        follow_up_question = str(final_narrative.get("follow_up_question") or "").strip()
        if follow_up_question and "?" not in llm_text:
            llm_text = f"{llm_text}\n\n{follow_up_question}"
            await _emit_chunk(f"\n\n{follow_up_question}")

        complete_event = {
            "stream_id": stream_id,
            "type": "analysis",
            "tone": str(final_narrative.get("tone") or "analysis"),
            "intent": intent,
            "mode": mode,
            "confidence": final_conf,
            "message": llm_text or str(final_narrative.get("message") or ""),
            "sections": list(final_narrative.get("sections") or []),
            "suggested_actions": list(final_narrative.get("suggested_actions") or []),
            "quick_replies": list(final_narrative.get("quick_replies") or []),
            "quick_actions": list(final_narrative.get("quick_actions") or []),
            "related_entities": list(final_narrative.get("related_entities") or []),
            "follow_up_question": follow_up_question,
            "alternative_hypothesis": str(final_narrative.get("alternative_hypothesis") or ""),
            "temporal_observation": str(final_narrative.get("temporal_observation") or ""),
            "continuity_notes": list(final_narrative.get("continuity_notes") or []),
            "business_impact_note": str(final_narrative.get("business_impact_note") or ""),
            "risk_trajectory": str(final_narrative.get("risk_trajectory") or ""),
            "early_signal_note": str(final_narrative.get("early_signal_note") or ""),
            "predictive_outlook": str(final_narrative.get("predictive_outlook") or ""),
            "business_consequences": list(final_narrative.get("business_consequences") or []),
            "domain_insights": list(final_narrative.get("domain_insights") or []),
            "campaign_intelligence": list(final_narrative.get("campaign_intelligence") or []),
            "segment_awareness": list(final_narrative.get("segment_awareness") or []),
            "category_family": str(final_narrative.get("category_family") or ""),
            "category_label": str(final_narrative.get("category_label") or ""),
            "detected_risks": list(final_narrative.get("detected_risks") or []),
            "detected_opportunities": list(final_narrative.get("detected_opportunities") or []),
            "evidence_signals": list(final_narrative.get("evidence_signals") or []),
            "confidence_phrase": str(final_narrative.get("confidence_phrase") or ""),
            "reasoning_transparency": str(final_narrative.get("reasoning_transparency") or ""),
            "trust_signal_strength": str(final_narrative.get("trust_signal_strength") or ""),
            "weight": final_weight,
            "expertise_level": expertise_level,
            "expertise_reason": expertise_reason,
            "response_mode": response_mode,
            "response_word_target": response_word_target,
            "lifecycle_state": str(context.get("lifecycle_state") or ""),
            "interruption_strength": "strong" if final_weight in {"important", "critical"} else "normal",
        }
        await self.emit(
            workspace_id,
            operation_id,
            "assistant_message_complete",
            correlation_id,
            "",
            entity_type,
            entity_id,
            complete_event,
        )
        recommendation_event = {
            "stream_id": f"asst_{operation_id}_recommendation",
            "type": "recommendation",
            "tone": "insight",
            "intent": intent,
            "mode": mode,
            "confidence": final_conf,
            "message": "",
            "sections": [],
            "suggested_actions": list(final_narrative.get("suggested_actions") or []),
            "quick_replies": list(final_narrative.get("quick_replies") or []),
            "quick_actions": list(final_narrative.get("quick_actions") or []),
            "related_entities": list(final_narrative.get("related_entities") or []),
            "follow_up_question": follow_up_question,
            "alternative_hypothesis": str(final_narrative.get("alternative_hypothesis") or ""),
            "temporal_observation": str(final_narrative.get("temporal_observation") or ""),
            "continuity_notes": list(final_narrative.get("continuity_notes") or []),
            "business_impact_note": str(final_narrative.get("business_impact_note") or ""),
            "risk_trajectory": str(final_narrative.get("risk_trajectory") or ""),
            "early_signal_note": str(final_narrative.get("early_signal_note") or ""),
            "predictive_outlook": str(final_narrative.get("predictive_outlook") or ""),
            "business_consequences": list(final_narrative.get("business_consequences") or []),
            "domain_insights": list(final_narrative.get("domain_insights") or []),
            "campaign_intelligence": list(final_narrative.get("campaign_intelligence") or []),
            "segment_awareness": list(final_narrative.get("segment_awareness") or []),
            "category_family": str(final_narrative.get("category_family") or ""),
            "category_label": str(final_narrative.get("category_label") or ""),
            "evidence_signals": list(final_narrative.get("evidence_signals") or []),
            "confidence_phrase": str(final_narrative.get("confidence_phrase") or ""),
            "reasoning_transparency": str(final_narrative.get("reasoning_transparency") or ""),
            "trust_signal_strength": str(final_narrative.get("trust_signal_strength") or ""),
            "weight": "light",
            "expertise_level": expertise_level,
            "expertise_reason": expertise_reason,
            "response_mode": response_mode,
            "response_word_target": response_word_target,
            "lifecycle_state": str(context.get("lifecycle_state") or ""),
            "interruption_strength": "soft",
        }
        should_emit_follow_up = (
            bool(follow_up_question)
            and response_mode in {"detailed", "warning"}
            and expertise_level in {"operational", "expert", "strategic"}
            and final_conf >= 0.72
        )
        if should_emit_follow_up:
            recommendation_event["message"] = follow_up_question
            await self.emit(
                workspace_id,
                operation_id,
                "assistant_message_complete",
                correlation_id,
                "",
                entity_type,
                entity_id,
                recommendation_event,
            )
        messages.append(
            {
                "role": "assistant",
                "content": complete_event["message"],
                "timestamp": _now_iso(),
            }
        )
        if recommendation_event["message"]:
            messages.append(
                {
                    "role": "assistant",
                    "content": recommendation_event["message"],
                    "timestamp": _now_iso(),
                }
            )

        current_op = await self.store.get_operation(workspace_id, operation_id)
        state = str((current_op or {}).get("state") or OperationState.running.value)
        if state == OperationState.running.value:
            await self.store.transition_state(workspace_id, operation_id, OperationState.completed.value)
        final_state = str((await self.store.get_operation(workspace_id, operation_id) or {}).get("state") or OperationState.completed.value)
        done_payload = {
            "conversation_id": conversation_id,
            "events": await self.store.list_events(workspace_id, operation_id),
            "tool_states": tool_states,
            "cards": cards,
            "messages": messages,
            "pending_actions": pending_actions,
            "operation_state": final_state,
            "analysis_summary": str(final_narrative.get("analysis_summary") or ""),
            "reasoning_response": complete_event["message"],
            "alternative_hypothesis": str(final_narrative.get("alternative_hypothesis") or ""),
            "temporal_observation": str(final_narrative.get("temporal_observation") or ""),
            "continuity_notes": list(final_narrative.get("continuity_notes") or []),
            "business_impact_note": str(final_narrative.get("business_impact_note") or ""),
            "risk_trajectory": str(final_narrative.get("risk_trajectory") or ""),
            "early_signal_note": str(final_narrative.get("early_signal_note") or ""),
            "predictive_outlook": str(final_narrative.get("predictive_outlook") or ""),
            "business_consequences": list(final_narrative.get("business_consequences") or []),
            "domain_insights": list(final_narrative.get("domain_insights") or []),
            "campaign_intelligence": list(final_narrative.get("campaign_intelligence") or []),
            "segment_awareness": list(final_narrative.get("segment_awareness") or []),
            "category_family": str(final_narrative.get("category_family") or ""),
            "category_label": str(final_narrative.get("category_label") or ""),
            "evidence_signals": list(final_narrative.get("evidence_signals") or []),
            "confidence_phrase": str(final_narrative.get("confidence_phrase") or ""),
            "reasoning_transparency": str(final_narrative.get("reasoning_transparency") or ""),
            "trust_signal_strength": str(final_narrative.get("trust_signal_strength") or ""),
            "recommendation_summary": str(final_narrative.get("recommendation_summary") or ""),
            "suggested_actions": list(final_narrative.get("suggested_actions") or []),
            "quick_replies": list(final_narrative.get("quick_replies") or []),
            "quick_actions": list(final_narrative.get("quick_actions") or []),
            "related_entities": list(final_narrative.get("related_entities") or []),
            "tone": str(final_narrative.get("tone") or "analysis"),
            "confidence": float(final_narrative.get("confidence") or 0.7),
            "intent": intent,
            "mode": mode,
            "weight": final_weight,
            "expertise_level": expertise_level,
            "expertise_reason": expertise_reason,
            "response_mode": response_mode,
            "response_word_target": response_word_target,
            "lifecycle_state": str(context.get("lifecycle_state") or ""),
            "operation_semantics": dict(semantics),
            "active_operation_id": str(context.get("active_operation_id") or ""),
            "active_campaign_id": str(context.get("active_campaign_id") or ""),
            "active_asset_id": str(context.get("active_asset_id") or ""),
            "last_pending_approval": str(context.get("last_pending_approval") or ""),
            "detected_risks": list(final_narrative.get("detected_risks") or []),
            "detected_opportunities": list(final_narrative.get("detected_opportunities") or []),
        }
        detected_risks = done_payload["detected_risks"]
        await self.store.update_operation(workspace_id, operation_id, status="completed", progress=100, result=done_payload)
        await self.emit(workspace_id, operation_id, "operation.completed", correlation_id, "", entity_type, entity_id, {"state": final_state})
        await self.emit(workspace_id, operation_id, "done", correlation_id, "", entity_type, entity_id, done_payload)
        runtime_logger.info(
            "operation.done operation_id={} state={} tools_executed={} pending_actions={} detected_risks={}",
            operation_id,
            final_state,
            len(tool_states),
            len(pending_actions),
            len(detected_risks),
        )

    async def stream_replay_and_live(self, workspace_id: str, operation_id: str):
        history = await self.store.list_events(workspace_id, operation_id)
        yield StreamPublisher.encode("replay_start", {"operation_id": operation_id, "count": len(history)})
        for row in history:
            event_name = str(row.get("event_type") or "event")
            payload = dict(row.get("payload") or {})
            if event_name.startswith("operation."):
                payload = {**payload, "status": event_name.split(".", 1)[1], "operation_id": operation_id}
                event_name = "operation"
            yield StreamPublisher.encode(
                "replay_event",
                {
                    "seq": row.get("seq"),
                    "event": event_name,
                    "data": payload,
                    "timestamp": row.get("timestamp"),
                },
            )
        yield StreamPublisher.encode("replay_complete", {"operation_id": operation_id, "count": len(history)})
        op = await self.store.get_operation(workspace_id, operation_id)
        if op is None or str(op.get("status")) != "running":
            return
        q = await self.store.subscribe(workspace_id, operation_id)
        try:
            while True:
                row = await q.get()
                event_name = str(row.get("event_type") or "event")
                payload = dict(row.get("payload") or {})
                if event_name.startswith("operation."):
                    payload = {**payload, "status": event_name.split(".", 1)[1], "operation_id": operation_id}
                    event_name = "operation"
                yield StreamPublisher.encode(event_name, payload)
                if event_name == "done":
                    break
        finally:
            await self.store.unsubscribe(operation_id, q)
