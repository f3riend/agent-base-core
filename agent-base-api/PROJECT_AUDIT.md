# PROJECT_AUDIT.md

> AI-native Business Operating System — full architectural & stabilization audit.
> Generated before any code changes, per the audit-first directive.
> All citations use `file:line`. Verified findings are marked ✅; agent-reported findings cross-checked but not re-read end-to-end are marked 🟡.

## Audit revision log

- **Rev 1** (initial): catalogued CB-1..CB-18 + AR-1..AR-10.
- **Rev 2** (after Phase 0–6 + HTTP-loop pass): see §15 below for the
  current status of each finding (FIXED / PARTIAL / OPEN) and the new
  critical issue surfaced during Rev 2 — **CB-19 self-referential HTTP**.
- **Rev 3** (platform-vision repositioning): see §16 below for the
  gap analysis between today's repo and the "AI-native commerce/social
  operations platform" vision (social auth, campaign lifecycle, calendar,
  customer channels, real product sync, multi-tenant org isolation, billing).

---

## 0. System Identity

This is **not** a CRUD app, chatbot, or workflow toy. It is a deterministic event-driven orchestration runtime that ingests business events from a fake e-commerce platform API, routes them through either a deterministic rule engine (critical events) or an autonomous AI planner (creative/hybrid events), gates AI proposals through approval + safety services, and executes them as workflows that schedule AI tasks consumed by a CrewAI worker invoking fake tools.

**Core philosophy (as built):** AI proposes plans, runtime executes them deterministically through workflow + approval gates; tool execution is sandboxed; events are sourced from a timeline with cursor-based replay; multi-tenancy is enforced by `user_id` on every table.

---

## 1. Architecture Overview

### 1.1 Processes (independent OS processes — `Makefile`)
| Process | Entry | Purpose |
|---|---|---|
| API server | `uv run uvicorn main:app --reload` | Fake e-commerce platform API (`/api/ai/v1/*`) + internal event emit (`/internal/*`) + dashboard API (`/api/internal/*` mounted from `orchestration_api.py`) |
| Listener | `python listener.py` | Polls timeline events, routes them, fires rules or autonomous planner |
| Workflow worker | `python workflow_worker.py` | Polls `workflow_instances` where status=scheduled, creates AI tasks |
| CrewAI worker | `python crewai_worker.py` | Polls `ai_tasks` where status=pending, instantiates Crews, runs tools |

### 1.2 Storage — Two SQLite Databases
- **`fake_ai_api.db`** (`main.py:47`): the fake platform's data — stores, items, orders, banners, campaigns, customer questions, reviews, **the timeline of events**.
- **`listener.db`** (`db.py:9`): the orchestration runtime's data — rules, workflow_instances, ai_tasks, tool_executions, automation_logs, planner_proposals, approval_requests, planner_memory, execution_cooldowns.

No cross-DB JOINs; coupling is via `event_id` reference and an HTTP fetch from the listener to the fake API.

### 1.3 End-to-End Event Lifecycle

```
fake platform API (main.py)
  └─ /internal/create-* → INSERT into fake_ai_api.db.timeline
       │
       ▼
LISTENER (listener.py) polls timeline.events WHERE id > cursor
  └─ resource_service.fetch_events()
  └─ resolve_user_id_from_event() → tenant binding
  └─ route_event(event_name, event) → critical | creative | hybrid | analytical | monitoring (event_router.py)
  └─ build_rule_context() — pulls store/item/order from fake API → listener.db
  └─ Switch by route:
       ┌─ critical path ────────────────────────────────────┐
       │ rule_engine.find_matching_rules(user_id) (deterministic)
       │   └─ action_engine.execute_rule_actions()
       │        └─ workflow_service.create_workflow()  ← inserts workflow_instance
       │ If no rule + CRITICAL_FALLBACK=1: ai_planner.propose_action() (legacy, off by default)
       │ If no rule + default config: SKIPPED with skip_reason=critical_no_rule_match
       └────────────────────────────────────────────────────┘
       ┌─ creative / hybrid path ───────────────────────────┐
       │ rules first (if any matched)
       │ then autonomous_planner.create_plan()
       │   └─ build_planner_context() (BI + business_state + memory + cross_event)
       │   └─ _plan_with_crewai()  (if OPENAI_API_KEY set)
       │      OR _plan_from_context()  (heuristic, deterministic)
       │   └─ assess_approval_need() — external publish detection
       │   └─ record_plan() to planner_memory
       │ planner_runtime.handle_autonomous_event()
       │   └─ save_proposal() → planner_proposals
       │   └─ apply_proposal()
       │        ├─ If requires_approval → create_approval_request() — STOPS here
       │        ├─ validate_autonomous_execution() (safety: hourly/daily/cooldown)
       │        └─ workflow_service.create_workflow() with metadata.plan
       └────────────────────────────────────────────────────┘
       ┌─ monitoring path ──────────────────────────────────┐
       │ rules-only; no rule match → skip                   │
       └────────────────────────────────────────────────────┘
  └─ update_event_processing_meta() → write back to timeline.processed_by_rule_engine_meta
  └─ set_cursor(event["id"])   ← cursor advance is LAST step

WORKFLOW WORKER (workflow_worker.py) polls workflow_instances WHERE status='scheduled'
  └─ should_run() — scheduled_at <= utcnow
  └─ validate_workflow() — critical workflows do entity-state checks
  └─ status → running
  └─ _execute_critical_workflow() OR _execute_autonomous_workflow()
       └─ task_service.create_ai_task() → ai_tasks (status=pending)
  └─ status → completed (immediately after AI task is queued; not after task runs)

CREWAI WORKER (crewai_worker.py) polls ai_tasks WHERE status IN ('pending','retrying')
  └─ status → running
  └─ _resolve_tools_for_task() — CRITICAL_TASK_MAP or get_tools_for_intent()
  └─ _build_crew() — Agent + Task with tools
  └─ crew.kickoff()  (synchronous; this is where CrewAI calls the fake tools)
  └─ tool_sandbox.execute_in_sandbox() wraps tool calls (retry + supposed timeout)
  └─ fake_tool_timeline.emit_tool_event() — writes back to fake_ai_api.db timeline
  └─ tool_executions row inserted; status → completed | failed | retrying | dead_letter

DASHBOARD (index.html) polls /api/internal/* every 12s
  └─ /dashboard-metrics, /timeline, /workflows, /tasks, /tool-executions, /approvals, ...
  └─ Approvals: POST /approvals/{id}/approve → planner_runtime.apply_approved_proposal()
       └─ same workflow path with skip_approval_check=True
```

### 1.4 Subsystem-Module Map
| Layer | Modules |
|---|---|
| **Event source** | `main.py` (the fake platform), `fake_data_generator.py`, `business_activity_simulator.py`, `product_import_service.py` |
| **Ingest & routing** | `listener.py`, `event_router.py`, `resource_service.py`, `rule_service.resolve_user_id_from_event` |
| **Rule engine** | `rule_engine.py`, `rule_service.py`, `rule_manager.py`, `semantic_parser.py`, `rules.txt`, `action_engine.py` |
| **Autonomous planner** | `autonomous_planner.py`, `ai_planner.py` (legacy), `planner_runtime.py`, `planner_memory.py`, `planner_learning.py`, `business_intelligence.py`, `business_state.py`, `cross_event_reasoner.py`, `business_chat.py`, `agent_registry.py` |
| **Workflow + tasks** | `workflow_service.py`, `workflow_worker.py`, `task_service.py` |
| **Tools / agents** | `tool_registry.py`, `tools.py`, `tool_schema_validator.py`, `tool_sandbox.py`, `crewai_worker.py`, `fake_tool_timeline.py` |
| **Gates** | `approval_service.py`, `safety_service.py` |
| **Persistence** | `db.py` (listener.db schema + connection helpers), `main.py:init_db` (fake_ai_api.db schema) |
| **Observability** | `observability.py`, `automation_log_service.py`, `timeline_service.py`, `timeline_processing.py` |
| **API surface** | `main.py` (~16 fake-platform + 12 internal-event endpoints), `orchestration_api.py` (~33 dashboard endpoints) |
| **UI** | `index.html` (main dashboard, vanilla JS+Tailwind, 12s polling), `test_dashboard.html` (separate testing surface) |
| **Glue** | `env_bootstrap.py`, `agent_runtime.py`, `context_compressor.py`, `timeline_processing.py` |

---

## 2. Subsystem Lifecycles

### 2.1 Event Lifecycle
- Source: every `/internal/*` POST on `main.py` writes to `fake_ai_api.db.timeline`.
- Listener cursor: `listener_state.last_cursor` in **listener.db** (cross-DB cursor; we read from fake_ai_api.db but track cursor in listener.db).
- Cursor advance: `set_cursor(event["id"])` is called **after** `process_event()` returns successfully (`listener.py:259-260`).
- Per-event meta: `update_event_processing_meta()` writes back `processed_by_rule_engine` + route/path/rules_matched/skip_reason for replay introspection.

### 2.2 Orchestration / Routing Lifecycle
- `route_event()` (`event_router.py:93-105`): hardcoded prefix tables — critical (`stock.`, `order.`, `payment.`, `inventory.`, `shipping.`, `fraud.`, `risk.`), creative (`product.`, `banner.`, `campaign.`, etc.), analytical (`sales.`, `analytics.`, `metric.`), monitoring (`health.`, `alert.`, `monitor.`, `system.`), else hybrid.
- Confidence per route is a fixed float (`routing_confidence`); 0.55 for "hybrid" (unknown).
- `should_use_autonomous()` blocks autonomous for critical events; allows it for hybrid/creative/analytical even when rules already matched (unless rules + critical).

### 2.3 Planner Lifecycle
- `create_plan()` short-circuits on critical events (`autonomous_planner.py:391`) — by design.
- Builds rich context: BI insights, business_state, memory, automation_logs summary, workflow_history, tools registry summary, cross-event reasoning.
- If `OPENAI_API_KEY` + `AUTONOMOUS_PLANNER_USE_AI=1` (default 1): `_plan_with_crewai()` — a CrewAI Agent returns a JSON plan parsed by regex.
- Else (or on AI exception): `_plan_from_context()` — deterministic heuristic from BI top opportunity + tool ranking + memory bias.
- Outputs a `proposal` dict that always goes through `assess_approval_need()` and `record_plan()`.

### 2.4 Workflow Lifecycle
- States: `scheduled → running → completed | cancelled`. Transition is single-row UPDATE.
- Scheduling: `scheduled_at = utcnow + delay_days`.
- Idempotency: `workflow_exists(name, entity_type, entity_id)` checked **before** insert (`workflow_service.py:30-54`) — non-atomic SELECT-then-INSERT, race window exists.
- Worker is single-threaded, polls every 5s (`workflow_worker.py:34`).
- `validate_workflow()` does entity-state checks for the two known critical workflows; autonomous workflows only check `requires_approval && !approved`.
- Status transitions to `completed` **immediately after the AI task is queued**, not after the AI task itself runs. So "completed" workflow does not mean tools fired successfully.

### 2.5 AI Task / Tool Lifecycle
- `ai_tasks`: `pending → running → completed | failed | retrying | dead_letter`.
- CrewAI worker polls every 5s (`crewai_worker.py:23`).
- Retry: `MAX_RETRIES=3`; failed tasks transition to `retrying` and are re-polled with **no backoff** — fast-failing tasks spin until dead-letter.
- Tool invocation: CrewAI handles the actual function call; `tool_sandbox.execute_in_sandbox` wraps with retry/timing but **the wrapping is partial** — see §3 bug list.
- `fake_tool_timeline.emit_tool_event()` writes a synthetic "tool.run" event back to the fake platform timeline, which the listener then re-ingests (this is intentional: tools feed BI).

### 2.6 Approval Lifecycle
- Scope: **only external social/campaign publishing** (`approval_service.py:60-76`). `EXTERNAL_PUBLISH_TOOLS = {"instagram_campaign_tool"}` + keyword sniff on workflow_name / business_intent.
- States: `pending → approved | rejected | edited | retried`. `approve()` and `reject()` both check `status=="pending"` first.
- Dashboard endpoints in `orchestration_api.py:317-345` expose all five transitions plus feedback.
- A workflow blocked by approval sits as a row in `approval_requests` with the proposal JSON; on `apply_approved_proposal()` it goes through `apply_proposal(..., skip_approval_check=True)`.

### 2.7 Safety / Quota Lifecycle
- `safety_service.validate_autonomous_execution()` checks: hourly autonomous quota (default 30/h/user), daily campaign limit (default 15/d/user), 5-minute cooldown per `(user, entity_type, entity_id, workflow_name)` tuple.
- `record_execution()` writes to `execution_cooldowns` with `ON CONFLICT … UPDATE last_executed_at`.

### 2.8 Memory Lifecycle
- `planner_memory.record_plan()` writes one row per planner decision (planned / noop / failed).
- `build_memory_context()` reads recent N rows + similar campaigns for the same entity.
- `planner_learning.get_confidence_adjustment()` bounds adjustment to [-0.05, +0.05] using `stat_value / count` from learned outcomes; guarded by `WHERE count >= 3`.
- **No retention policy** — table grows unboundedly.

### 2.9 Dashboard Lifecycle
- `index.html` (vanilla JS + Tailwind via CDN) polls `/api/internal/*` every 12s.
- `setListHtml(elId, html)` is the central render path with two guards: (a) `isUserTyping` short-circuit; (b) `htmlCache[elId]` equality dedupe.
- Typing detection: `markTyping()` debounces 1200ms; bound only to `chatInput` and `ruleInput` via `bindProtectedInputs()`.

### 2.10 Observability Lifecycle
- `observability.py` writes **stdout only** via `_emit()` — tagged JSON; no DB writes.
- `automation_log_service.py` writes structured rows to `listener.db.automation_logs`.
- `timeline_service.fetch_timeline()` reads the fake-platform timeline for the dashboard.
- These are **independent paths** — the same event creates stdout JSON + an automation_log + a fake-platform timeline entry; no single source of truth.

---

## 3. Critical Bugs (verified, with file:line)

### CB-1 — `tool_sandbox.py:38-39` — Timeout check is dead code ✅
```python
for attempt in range(MAX_RETRIES + 1):
    try:
        retries = attempt
        t0 = time.monotonic()                                   # t0 set inside loop
        if timeout_sec and (time.monotonic() - t0) > timeout_sec:  # always ~0, never true
            raise TimeoutError(f"Tool {tool_name} timeout")
```
`t0` is sampled inside the loop, then compared against `now` on the very next line, so elapsed is always ~0µs. The timeout never fires. Retries do work, but a tool hanging indefinitely will block the worker. **Fix:** use the outer `started` (line 31) as the baseline, or wrap `run_fn()` in a `concurrent.futures` timeout.

### CB-2 — `rule_service.py:121-132` — `list_rules(include_disabled=False)` is broken ✅
Both branches execute the identical query without `enabled` filtering. The default-False parameter has zero effect; disabled rules are always returned, contaminating the cache compile (`load_and_compile_rules` likely filters elsewhere) and the dashboard. Dashboard's "active rules" count is wrong if any rule has been disabled.

### CB-3 — `workflow_service.py:297` — Item-deleted check is parse-corrupted ✅
```python
if item["status"] if "status" in item.keys() else "active" == "deleted":
```
Python parses this as `if item["status"] if ("status" in item.keys()) else ("active" == "deleted"):`, i.e. the conditional expression evaluates to either `item["status"]` (truthy for any non-empty status string — so the IF is always taken!) or `False`. The intent was `if item.get("status","active") == "deleted":` — i.e. cancel the low_stock workflow if the item is deleted. As written: **every item with a non-empty status field is treated as "deleted" for the purpose of cancellation**, which means `low_stock_alert` workflows are wrongly cancelled with reason `item_deleted`.

### CB-4 — `orchestration_api.py:249-260` — `/tool-executions` is cross-tenant 🟡
```python
@router.get("/tool-executions")
async def get_tool_executions(limit: int = Query(50, le=200)):
    rows = execute_query("""SELECT te.*, t.task_type, t.user_id
                            FROM tool_executions te LEFT JOIN ai_tasks t ON t.id = te.task_id
                            ORDER BY te.id DESC LIMIT ?""", (limit,))
```
No `WHERE t.user_id = ?` filter — endpoint returns tool executions from **every tenant**. Sibling endpoints (`/workflows`, `/tasks`, `/automation-logs`) all filter by user_id. Also `/users` (line 381-384) returns the entire users table.

### CB-5 — `workflow_worker.py:42-62` + `workflow_service.py:30-54` — No concurrency control 🟡
- `workflow_exists()` SELECT-then-INSERT is non-atomic. Two listeners (or one listener + one approval-triggered call) hitting the same event window can both pass the dedup check and both insert. Verified: no UNIQUE constraint on `(workflow_name, entity_type, entity_id, status)` in `db.py`.
- `workflow_worker.py` has no row-level lock / worker identity. Running the worker twice (or restarting it mid-execution) double-processes pending workflows. The file itself has TODOs at the top of the module acknowledging this is unimplemented.

### CB-6 — `listener.py:258-271` — Listener cursor not advanced on exception 🟡
The cursor write (`set_cursor`) is inside the same `try` block as `process_event`. On an exception during processing, `update_event_processing_meta(processed=False, path="error")` is called but the cursor is **not** advanced, so the event is reprocessed on next loop iteration / restart. Combined with non-idempotent workflow creation (CB-5), a transient failure on a creative event can produce duplicate workflows once safety races resolve. Listener should advance the cursor regardless of outcome and rely on `processed=False` to mark poisoned events.

### CB-7 — `crewai_worker.py:112-113` (per agent report) — ValueError on empty tool resolution 🟡
If `_resolve_tools_for_task` returns `[]`, `_build_crew` raises. Current code keeps a hardcoded fallback (`banner_generator_tool`), so this would only fire if the fallback name were removed from the registry. Documented as latent risk + brittle hardcoded fallback.

### CB-8 — `automation_log_service` writes without `user_id` 🟡
`_insert_log()` does not accept `user_id` (per workflow-stack agent report). All automation_log rows land in tenant 1 (`DEFAULT 1` on the column). Multi-tenant audit trail is broken silently. Needs verification against the file — flagged for follow-up.

### CB-9 — `task_service.transition_task()` uses dynamic SQL string assembly 🟡
Per workflow-stack agent: f-string SQL with field names derived from kwargs keys. Field names come from code, not user input, so it is not an injection vector today; but it raises on unknown keys with a SQL error, not a clean ValueError. Brittle, not unsafe.

### CB-10 — `safety_service.check_cooldown` keyed on (user, entity, workflow_name) 🟡
Same business action under different workflow names (`discount_promo_v1` vs `discount_promo_v2`) bypasses the cooldown. The cooldown is name-equal, not intent-equal. Acceptable for safety theater, weak for actual rate-limit semantics.

### CB-11 — `business_intelligence.py:256-262` — Seasonal heuristic breaks replayability 🟡
`analyze()` uses `datetime.utcnow().month` to score `seasonal_opportunity`. Replaying the same event in a different month produces a different insight → different intent → different workflow_name → different proposal. The system is otherwise designed to be replayable from the timeline; this is the one non-deterministic input outside the AI plan path.

### CB-12 — `autonomous_planner.py:44-54 INTENT_FROM_BI` — Dead/unmapped insights 🟡
Insights produced by BI (`shipping_delay`, `inventory_risk`) are not in `INTENT_FROM_BI`. Fallback path (`intent = INTENT_FROM_BI.get(top["type"], top["type"])`) uses the insight type as the business intent, producing workflow names like `shipping_delay_<entity>` that the agent_registry then cannot route (`_intent_to_domain` returns "marketing" for unknowns). End result: shipping/inventory events get routed to marketing agents.

### CB-13 — `agent_registry._intent_to_domain` — Hardcoded ontology, marketing catch-all 🟡
The dict at `agent_registry.py:101-112` is the only intent→domain map. Dead entries (`instagram_content`, `banner_campaign`) are not produced anywhere. Every unknown intent → marketing. Together with CB-12: a non-marketing event can quietly land in the marketing agent.

### CB-14 — `subject_type` is unsafe input 🟡
`subject_type.lower()` called without guards in `action_engine.py:15`, `planner_runtime.py:113,49`, `planner_memory.py:116`, `autonomous_planner.py:93,127`. If `event["subject"]["type"]` is `None` or missing, listener crashes at `listener.py:65`. User explicitly listed this as a known crash. Need both a guard at listener entry and a defensive `or "store"` at the deep call sites.

### CB-15 — All seven tools share `FakeToolArgs` with `default=""` ✅
`tools.py:16-23` plus all seven tool classes (lines 72-167) use the same Pydantic model with a single optional `input_text` field. Every tool's OpenAI schema becomes:
```json
{ "type":"object", "properties":{"input_text":{...,"default":""}}, "required":[], "additionalProperties": false }
```
This is **valid OpenAI JSON schema** (the validator's `validate_openai_tool_schema` passes), but CrewAI may not invoke tools meaningfully because every signature is "give me anything or nothing". Tool calls degenerate into "execute the named tool" with no semantic argument. Symptom on the user's known-issues list: "invalid CrewAI/OpenAI tool schemas" — the schema is syntactically valid but **semantically degenerate**; this needs separate per-tool argument models.

### CB-16 — Dashboard typing protection is incomplete ✅ (mechanism unconfirmed)
`index.html:186-192` short-circuits `setListHtml` while `isUserTyping`, and `markTyping()` debounces 1200ms. But:
- `markTyping` is only bound to `chatInput` and `ruleInput` (`index.html:203-217`); typing into any other input (search filters in test_dashboard.html, future inputs) won't pause refresh.
- `setText()` (line 237) has no typing guard — it can blur an input by mutating sibling DOM during a refresh.
- The 12s `setInterval` is not paused when typing begins; it only fires `loadAll(false)` which is then short-circuited per-section. If a focused input ever has its container parent rewritten in a section refresh, focus is lost. The exact pixel-level repro should be confirmed in-browser, but the bug class is real.

### CB-17 — `crewai_worker.py` retry has no backoff
Per workflow agent: `fail_task` flips status to `retrying`; next poll (~5s) re-runs immediately. A tool that fails on every invocation spins up `MAX_RETRIES` runs in <30s before dead-lettering, burning OpenAI/CrewAI quota.

### CB-18 — `index.html`'s `internal()` helper has empty `BASE`
`index.html:160-161`: `const API="/api/internal"; const BASE="";` so `internal("/internal/create-store", body)` posts to `/internal/create-store` (no host). Works on the same origin only — flag for production deploys.

---

## 4. Architectural Risks

### AR-1 — `workflow.status="completed"` does not mean tools fired
`workflow_service.execute_workflow` flips status to `completed` immediately after `create_ai_task` returns. The dashboard's "active workflows" KPI and "completed" tables conflate two very different states (workflow scheduling vs. actual tool success). The real "did it run?" signal lives in `tool_executions` + `ai_tasks.status`.

### AR-2 — `apply_approved_proposal` reconstructs entity_type from string casing
`planner_runtime.py:236-238`:
```python
entity_type = (proposal.get("entity_type") or "store").lower()
subject_type = entity_type.title() if entity_type != "item" else "Item"
if entity_type == "store": subject_type = "Store"
```
The `.title()` branch is dead — overwritten on the next line for "store", and "Item" already title-cased. If a new entity_type is added (e.g. "order", "customer"), this branch silently does the wrong title-casing. Centralize entity-type normalization.

### AR-3 — Two competing ontologies
- **Rule path:** `semantic_parser` produces intents like `low_stock`, `high_sales`, `sales_drop`, `negative_review`, `delayed_order`, `banner_performance`, `store_welcome`, `store_rejected`, `customer_question`, `coupon_workflow`.
- **Autonomous path:** `business_intelligence` produces insight types `campaign_opportunity`, `price_drop_promotion`, `viral_product`, `engagement_spike`, `customer_dissatisfaction`, `reputation_risk`, `seasonal_opportunity`, `sales_drop`, `repeat_failures`, plus `shipping_delay`, `inventory_risk`.
- These two namespaces overlap on `sales_drop` but diverge everywhere else, and **neither references the other**. There is no shared "business event taxonomy" — duplicating semantic interpretation across both paths.

### AR-4 — `ai_planner.py` is effectively dead under default config
`CRITICAL_FALLBACK=0` is the default. The only invocation site is `handle_critical_fallback` in `planner_runtime.py:319`. With the default config, `ai_planner.propose_action` is never called. The module remains maintained but untested. Decision needed: delete, gate behind a feature flag with tests, or move to `legacy/`.

### AR-5 — `_plan_with_crewai` JSON parsing by regex
`autonomous_planner.py:311-315`: `re.search(r"\{[\s\S]*\}", result)` — any nested `{` in CrewAI's prose will be consumed. Fragile. A `try: json.loads(result)` first, fallback to regex, would be safer.

### AR-6 — `apply_proposal` writes approval-and-noop side effects in the same function
`planner_runtime.apply_proposal` is responsible for: approval gating, safety gating, workflow creation, cancel-workflow, noop, error returns, memory updates, log emission. ~150 lines. Should be split: `gate(plan) → execute(plan)` so the approval/safety logic is unit-testable.

### AR-7 — `observability` is stdout-only
The dashboard cannot show ad-hoc "log lines". The `/api/internal/automation-logs` endpoint reads `listener.db.automation_logs`, but anything emitted via `observability._emit` is stdout-only. Without a log aggregator the operator has no UI window into routing decisions, AI reasoning, BI signals.

### AR-8 — Dashboard polls every 12s with no diff
Every poll re-fetches full snapshots for every section. Mitigated by `htmlCache` equality dedupe but not by ETag / If-None-Match. At scale this is wasteful and noisy. Future direction: server-sent events on a /timeline/stream-style endpoint.

### AR-9 — No FK constraints
`db.py` declares foreign-key-like columns (`workflow_id`, `task_id`, `event_id`, `user_id`) but no `REFERENCES` clauses. `PRAGMA foreign_keys=ON` is set but has no effect without declared FKs. Orphan rows possible.

### AR-10 — Single SQLite file with WAL — concurrency ceiling
Two workers + a listener + an API server hitting one SQLite file with `check_same_thread=False`. WAL helps reads but every write contends. Acceptable in test mode; ceiling will be obvious at modest event rates.

---

## 5. Duplicate / Overlapping Systems

| Area | Duplication |
|---|---|
| Workflow creation | `action_engine.execute_rule_actions` and `planner_runtime.apply_proposal` both call `workflow_service.create_workflow` with independently constructed metadata. Different shapes of `metadata.plan`. No shared adapter. |
| Subject/entity-type normalization | `subject_type.lower()` is sprinkled across 5+ files; `entity_type.lower()` again across the same set. Three different "is this a store/item?" branches reimplemented (`workflow_service.py`, `planner_runtime.py`, `business_state.py`). |
| Intent / domain ontology | `semantic_parser.INTENT_ONTOLOGY`, `autonomous_planner.INTENT_FROM_BI`, `agent_registry._intent_to_domain` — three hardcoded maps, none cross-validate. |
| Automation logging | `observability._emit` (stdout), `automation_log_service.log_*` (DB), `fake_tool_timeline.emit_tool_event` (cross-DB timeline write). Three layers, three log-writing surface areas. |
| Event vocab | `event_router.CRITICAL_EVENT_PREFIXES/EXACT`, `semantic_parser.KNOWN_EVENTS`, `rule_manager.KNOWN_WORKFLOWS` — three editors must edit three constants to add a new event/workflow. |
| Tool registry shape | `tool_registry.TOOL_METADATA` (scores + descriptions), `tools.ALL_TOOLS_RAW` (instances), `tool_registry.CRITICAL_TASK_MAP` (hardcoded routes). Three sources of truth. |

---

## 6. Dead / Deprecated Code

- `ai_planner.py` — only `handle_critical_fallback`, which requires `CRITICAL_FALLBACK=1` (default off). Effectively unused.
- `rule_service.list_rules(include_disabled=...)` — flag has no effect (CB-2).
- `rule_manager._extract_workflow` regex patterns reference workflows that are also produced by `semantic_parser`; the active path uses `semantic_parser`, so this regex helper is unreachable for active rule creation (per rule-stack agent report).
- `agent_registry._intent_to_domain` entries `instagram_content` and `banner_campaign` — no producer.
- `autonomous_planner.INTENT_FROM_BI` mappings `engagement_spike` and `repeat_failures` are produced by BI but the resulting intents (`growth_marketing`, `insights`) are not present in `agent_registry._intent_to_domain` → fallback to "marketing" — half-wired ontology.
- `RuleManager.dry_run()` (`rule_manager.py:721-735`) defined but never called from API/CLI (per rule-stack agent).
- `_plan_with_crewai` returns an `agent_ctx` derived from `route` and `intent`; this is also set by the caller in `_plan_from_context`. Redundant double-assignment.

---

## 7. Unsafe Execution Paths

- **No real timeout on tool execution** (CB-1) — a hanging tool blocks the CrewAI worker forever.
- **Workflow creation race** (CB-5) — duplicates possible without DB-level uniqueness constraint.
- **Cursor non-advance on exception** (CB-6) — a poisonous event blocks the listener loop and keeps re-firing.
- **Retry without backoff** (CB-17) — fast-failing tasks burn quota.
- **CORS `allow_origins=["*"]`** (`main.py:33`) — fine for test, must not ship.
- **No auth on `/api/internal/*` and `/internal/*`** — dashboard and event ingest both unauthenticated. Acceptable behind a private network; flag for any non-local exposure.
- **Fake token check on `/api/ai/v1/*`** — `FAKE_TOKEN = "aio_test_token"` (`main.py:45`) is the only auth gate, and it's a constant.

---

## 8. Replay Safety Risks

- **Seasonal heuristic** (CB-11) — BI insight depends on wall-clock month → same event replays differently across months.
- **`_plan_with_crewai`** non-determinism — when `OPENAI_API_KEY` is set, plans are model-generated and not reproducible. Replay must be guarded by `AUTONOMOUS_PLANNER_USE_AI=0` to be deterministic.
- **Tool re-emission to timeline** — `fake_tool_timeline.emit_tool_event` writes new events back into the timeline. Replaying the original event re-creates these synthetic events on top of the historical ones. The system never marks "synthetic vs primary" — replays double-count.
- **Cursor in listener.db, events in fake_ai_api.db** — a wipe of one DB without the other leaves the cursor stranded.

---

## 9. Multi-Tenant Risks

- `/tool-executions` and `/users` (CB-4) leak cross-tenant data.
- `automation_logs.user_id` defaults to 1 if caller omits (CB-8).
- `workflow_service.create_workflow(user_id: int = 1)` and `task_service.create_ai_task(user_id: int = 1)` — silent default-1 if any caller forgets the kwarg.
- `planner_runtime.apply_proposal(user_id: int = 1)` — same default.
- `safety_service` quotas keyed on `user_id` but counter writes must trust the caller's user_id, which traces back to `resolve_user_id_from_event` — if the event's subject_id doesn't resolve to a known store, returns DEFAULT_USER_ID=1.

---

## 10. Scaling / Orchestration Bottlenecks

- **Single SQLite, multiple workers** — write contention will be the bottleneck before any AI cost is.
- **No DB indexes declared** — `automation_logs`, `tool_executions`, `planner_proposals`, `planner_memory`, `workflow_instances`, `ai_tasks` will sequentially scan once row counts exceed ~10k. The dashboard's `LIMIT 50 ORDER BY id DESC` pattern is fast on the PK index, but `WHERE user_id=? AND status=?` queries on the workflow/task tables need composite indexes.
- **Polling everywhere** — listener 2s, workers 5s, dashboard 12s. Latency floor is ≥7s end-to-end best case (event → listener pickup → worker pickup → AI task pickup) before any CrewAI call.
- **CrewAI `crew.kickoff()` is sync** — one CrewAI worker = one concurrent AI task. No bulk concurrency.
- **`planner_memory` grows unboundedly** — every plan decision inserts a row, no archival. A modest 100 events/day = 36.5k rows/year. Affects every BI/memory query that scans recent rows.

---

## 11. Cross-checked Known-Issue List (from user)

| User-reported issue | Where it lives | Fix vector |
|---|---|---|
| invalid CrewAI/OpenAI tool schemas | All tools share `FakeToolArgs` (CB-15) — schema is valid but semantically degenerate. | Per-tool Pydantic argument models (or at minimum, descriptive required fields per tool) |
| subject_type None crashes | `subject_type.lower()` in 5+ sites (CB-14) | Guard in `listener.py` + defensive default in deep callsites + add to `process_event` event-shape validation |
| dashboard rerender resets typing | `index.html:setListHtml` guard incomplete (CB-16) | Pause `refreshTimer` on any input focus, not just two specific inputs; protect text inputs as a class, not by id |
| deprecated tool ontology | Three competing ontologies (AR-3, CB-12, CB-13) | Single source: derive intent→workflow/tool from a shared registry; remove `INTENT_FROM_BI` + `_intent_to_domain` |
| autonomous planner instability | `_plan_with_crewai` regex JSON parse (AR-5), no schema validation of model output | Validate model output with Pydantic; deterministic fallback on parse failure (already exists for exceptions, not for malformed JSON) |
| critical events bypass autonomous reasoning | By design (`autonomous_planner.py:391`) — keep | (Document, not fix) |
| some workers not loading .env | Verified all four entry points call `load_app_env()` | (Not a current bug; if a new entry point is added, add `from env_bootstrap import load_app_env; load_app_env()` at top) |
| mixed Turkish/English reasoning | `approval_service`, `safety_service`, `business_intelligence`, `business_state`, `autonomous_planner` reasons | Decide on a language for code-facing strings (English) vs. UI strings (Turkish from a single i18n table) |
| tool registry inconsistency | `TOOL_METADATA` vs `ALL_TOOLS_RAW` vs `CRITICAL_TASK_MAP` (AR-3, §5) | One registry: declare each tool once with name, args_schema, metadata, intents, critical_routes |
| approval spam | No dedup in `approval_service.create_approval_request` | Add `UNIQUE(user_id, event_id, proposal_hash)` index; existing-pending lookup before insert |
| replay issues | Cursor advance on exception (CB-6) + seasonal non-determinism (CB-11) + synthetic-event echo (§8) | Cursor always advances; tag synthetic events; freeze month for replays |
| infinite loops | Retry without backoff (CB-17), poisoned event re-run (CB-6), tool sandbox no real timeout (CB-1) | Exponential backoff in `fail_task → retrying`; cursor advance; real timeout via `concurrent.futures` |
| invalid context handling | `_plan_from_context` assumes ctx keys exist; BI ontology gaps (CB-12, CB-13) | Schema-validate ctx; map every BI insight type to an intent or explicit no-route |

---

## 12. Recommended Stabilization Order

This is sequencing only — implementation comes after explicit go-ahead per the user's instruction "ONLY AFTER FULL AUDIT: start stabilization work."

### Phase 0 — Safety nets (cheap, blocks future loss)
1. CB-6 Cursor advance on exception (one-line fix; biggest safety multiplier).
2. CB-3 Item-deleted check (one-line fix; currently corrupts cancellation logic).
3. CB-2 `list_rules` `include_disabled` (one-line fix; correctness of dashboard).
4. CB-4 `/tool-executions` user_id filter (one-line fix; tenant isolation).
5. CB-14 Guard `subject_type` at listener entry (one-block fix; eliminates the most-cited crash).

### Phase 1 — Real timeouts + dedup
6. CB-1 Real timeout in `tool_sandbox`.
7. CB-5 UNIQUE constraint on `workflow_instances(workflow_name, entity_type, entity_id, status)` for the IDEMPOTENT statuses (this is conditional uniqueness; SQLite supports partial indexes).
8. CB-17 Exponential backoff in `fail_task`.
9. Approval dedup (UNIQUE on `approval_requests(user_id, event_id, proposal_hash)`).

### Phase 2 — Single source of truth
10. Unify intent/domain ontology into one registry; delete `INTENT_FROM_BI` + `_intent_to_domain`.
11. Add per-tool argument schemas (CB-15).
12. Consolidate `subject_type/entity_type` normalization into one helper.

### Phase 3 — Dashboard
13. Pause refresh on focus class-wide (CB-16); add ETag-based GETs.

### Phase 4 — Replay + observability
14. Tag synthetic timeline events; freeze month for replay (CB-11).
15. Move `observability._emit` to also write a `routing_decisions` table for the dashboard.

### Phase 5 — Cleanup
16. Delete `ai_planner.py` or gate it behind a feature flag with explicit tests.
17. Remove `_intent_to_domain` dead entries; remove `_extract_workflow` heuristic.
18. Add composite indexes on hot multi-tenant queries.

---

## 13. Out-of-Scope but Worth Noting

- **README.md is empty** — no operator-facing docs.
- **`api_test_results.txt` (44k) and `full_system_test_results.txt` (99k)** — large committed test artifacts. Probably want `.gitignore` for these.
- **`__pycache__/` is committed** — should be `.gitignore`'d.
- **`.env` is committed** with a live-looking `OPENAI_API_KEY=sk-proj-...`. ⚠️ **Rotate this key immediately**; it should never be in git.
- **`raporum.txt` (99k)** — unclear purpose; likely an artifact from prior audit runs.
- **`Pillow` and `requests` in `pyproject.toml`** but only `requests` is used in the modules I read; `Pillow` may be a leftover.

---

## 14. What This Audit Did NOT Verify

These are agent-reported findings I have not personally line-confirmed. Before acting on them, re-read the cited lines.
- CB-8 `automation_log_service` user_id omission — re-read `automation_log_service.py`.
- CB-7, CB-9, CB-10 — re-read `crewai_worker.py`, `task_service.py`, `safety_service.py`.
- CB-11 seasonal heuristic — re-read `business_intelligence.py:218-284`.
- AR-9 FK absence — re-read full `db.py` CREATE statements.
- `index.html`'s exact rerender-typing repro — needs in-browser reproduction with the dev server running.

---

*End of original audit body.*

---

## 15. Rev-2 Status Update (post Phase 0–6 + HTTP-loop pass)

This section reflects code that has actually shipped since the original
audit was written. Verify with `git diff` and a fresh re-read if anything
below is acted on.

### 15.1 Findings — fixed

| ID | Title | What landed |
|---|---|---|
| CB-1 | tool_sandbox dead-code timeout | `concurrent.futures` real timeout + exponential backoff + circuit breaker; sandbox wired into `tools._fake_run` so every tool execution is protected |
| CB-2 | `list_rules(include_disabled=False)` | `WHERE enabled=1` added to the false branch |
| CB-3 | item-deleted parse corruption | `item_status = item["status"] if "status" in item.keys() else "active"` + `(item_status or "active") == "deleted"` |
| CB-4 | `/tool-executions` cross-tenant | `user_id` query param + `WHERE t.user_id = ?` filter; `/users` gated behind `ALLOW_DEBUG_ENDPOINTS=1` |
| CB-5 | workflow dedup race | Partial unique index `idx_workflow_active_unique` on `(user_id, name, entity_type, entity_id) WHERE status IN ('scheduled','running')` + `IntegrityError` race-handler in `create_workflow` |
| CB-6 | listener cursor not advanced on exception | `finally: set_cursor(...)` in the listener loop |
| CB-7 | empty tool list `ValueError` | `NoToolsResolvedError` → clean task cancellation (no retry storm) |
| CB-8 | automation_log user_id default-1 | `user_id` propagated through every public function + callers updated (workflow_service, task_service) |
| CB-11 | seasonal heuristic non-determinism | `_event_month(event)` reads from `event.ts` / payload — replay-safe |
| CB-12 | shipping_delay / inventory_risk unmapped | now in `ontology.BI_INSIGHT_TO_INTENT` |
| CB-13 | `_intent_to_domain` dead entries | replaced with `ontology.domain_for_intent`; dead keys removed |
| CB-14 | `subject_type` None crash | guard at listener entry (already shipped in Rev 1 prep) |
| CB-15 | semantically degenerate tool args | seven per-tool Pydantic schemas (`InstagramCampaignArgs`, `BannerGeneratorArgs`, …) |
| CB-17 | no retry backoff | `ai_tasks.next_retry_at` + exponential `_retry_delay_seconds`; worker poll honors it |
| AR-3 | three competing ontologies | unified `ontology.INTENTS` + `BI_INSIGHT_TO_INTENT`; old maps replaced or thin-wrapped |
| AR-5 | regex JSON parse | `plan_validator.parse_planner_output` — markdown-fence aware + balanced-brace scanner + Pydantic validation |

### 15.2 New critical finding — CB-19

**CB-19 — Self-referential HTTP inside the API process ✅ FIXED**

`fake_data_generator.py`, `business_activity_simulator.py`, and
`product_import_service.py` used `requests.post("http://127.0.0.1:8000/internal/...")`
to write through the very FastAPI process that imported them (via
`/api/internal/seed-data` and `/api/internal/products/import`). Symptom:
under load, the seed flow would deadlock — every seed write tied up a
worker thread on its own server.

**Fix:** new `internal_service.py` module exposes pure functions for every
`/internal/*` operation. `main.py` `/internal/*` route handlers become
thin wrappers. The three offending modules now call the service layer
directly with no HTTP. A guard helper `assert_not_internal_http_loop(url)`
raises `InternalHTTPLoopError` if any future code path tries to HTTP into
this process (active only when `INTERNAL_SERVICE_IN_PROCESS=1`, set in
main.py at import time).

### 15.3 Findings — partial

| ID | Title | Status |
|---|---|---|
| CB-16 | dashboard typing-reset | Pending — Phase 8 not yet shipped |
| AR-4 | `ai_planner.py` legacy | Still gated behind `CRITICAL_FALLBACK=0`; not deleted |
| AR-9 | no FK constraints | No `REFERENCES` clauses; not addressed |
| AR-10 | single SQLite ceiling | Indexes added (Phase 1), but storage shape unchanged |

### 15.4 New scaffolding shipped

- `ontology.py` — single source of truth for intent/domain/tools.
- `event_envelope.py` — typed envelope with `from_legacy()` adapter; tool-emit events tagged `source=tool_emit` and skipped as synthetic.
- `plan_validator.py` — Pydantic schema + canonical naming + duplicate suppression + safety score.
- `narrative_synth.py` — deterministic signal-extraction → Turkish operator-tone narrative + ontology-filtered recommendations.
- `internal_service.py` — service layer that replaces the HTTP loop.
- `orchestration_traces` table + `observability.fetch_traces` + `/api/internal/traces*` endpoints.
- `planner_outcomes` table + `record_planner_outcome()` wired into approval + task completion.
- 11 composite indexes on `ai_tasks`, `workflow_instances`, `automation_logs`, `planner_memory`, `tool_executions`, `approval_requests`, `planner_proposals`, `orchestration_traces`.

### 15.5 Still open (work not yet started)

- **CB-16** dashboard rerender / typing reset (Phase 8).
- **CB-18** `internal()` empty BASE in index.html — only matters for non-same-origin deploys.
- **AR-9** FK constraints.
- **AR-7** dashboard observability log feed — table now exists (Phase 7); UI work pending (Phase 8).
- Multi-agent dispatcher (Phase 10) — interface scaffolding only.
- Real-tool adapter scaffolding (Phase 11).
- Production hardening pass (Phase 12) — CORS, auth gating, rate limiting, intent-level cooldowns.

---

## 16. Rev-3 Platform-Vision Gap Analysis

The platform is being repositioned from "AI orchestration runtime" to
"AI-native commerce/social operations platform". This section measures the
distance between what is built and the 12-capability target.

### 16.1 Capability Status

| # | Capability | Status | Evidence / Gap |
|---|---|---|---|
| 1 | Social media connect (Instagram/Facebook) | **PARTIAL** | Drafts via `tools.InstagramCampaignTool`; no OAuth, no credential storage, no real publish, no FB support, no per-account selection |
| 2 | Product management | **SHIPPED** | `/internal/create-product`, items schema in `main.py:init_db`, viral detection in `business_intelligence.py:180-195` |
| 3 | Calendar / scheduling center | **PARTIAL** | Workflow-level `scheduled_at` + task `next_retry_at` exist; no operator calendar, no recurring schedules, no campaign timeline view |
| 4 | AI content generation | **SHIPPED** (mock) | All seven tools fire from `tools.py`; per-tool Pydantic args (CB-15 fix); deterministic mocks emit timeline events |
| 5 | Campaign orchestration | **PARTIAL** | Workflows + planner detect opportunities; missing lifecycle states (draft → review → live → paused → archived), per-campaign performance tracking, A/B testing |
| 6 | Workflow center | **SHIPPED** | `workflow_service.py`, `workflow_worker.py`, dashboard wiring |
| 7 | Approval center | **SHIPPED** (single-level) | `approval_service.py` gates external publishing; no escalation, no SLA, no delegation, no comment threads |
| 8 | AI customer interaction | **PARTIAL** | `SupportResponseTool` drafts replies; sentiment detection in BI; no real channel (DM/email/Q&A), no thread state, no escalation routing |
| 9 | AI business intelligence | **SHIPPED** | `business_intelligence.py` (12+ signal types), `cross_event_reasoner.py` with temporal weighting |
| 10 | AI operator chat | **SHIPPED** | `business_chat.py` + `business_query_router.py` + `business_retrieval_service.py` — real data, 13 routed intents |
| 11 | Memory / learning center | **SHIPPED** | `planner_memory`, `planner_outcomes`, `recall_similar_campaigns` with outcome-weighted ranking |
| 12 | Operational health dashboard | **SHIPPED** | `index.html` AI Operations Center with KPIs, reasoning feed, humanized timeline, approvals, memory |

### 16.2 Top 5 Platform-Vision Gaps

**Gap A — No real social-auth / credential storage**
- *Today:* Instagram tool draft-only; no OAuth, no token persistence.
- *Needed:* `social_credentials` table (encrypted token blob, scope, expires_at, account handle); `oauth_flow.py` with provider stubs (instagram, facebook); credential mgmt endpoints + UI. Tools become "if real-credential present → would publish; else → draft only".
- *Files:* new `social_credentials.py`, `db.py` schema add, `tools.py` adapter wiring, `orchestration_api.py` endpoints.

**Gap B — No campaign lifecycle / performance tracking**
- *Today:* Campaigns are ad-hoc workflow rows.
- *Needed:* `campaigns` table with state machine `draft → scheduled → live → paused → completed → archived`. Per-campaign metrics (impressions, CTR, conversions, spend). BI emits `campaign_performance` signals keyed to specific campaigns.
- *Files:* new `campaign_service.py`, `db.py` schema, `business_intelligence.py` campaign-level detectors, dashboard panel.

**Gap C — No escalation / multi-level approval routing**
- *Today:* Binary approve/reject by any operator.
- *Needed:* Per-approval `assigned_to`, `due_at`, `escalation_path`, SLA watcher, delegation, audit trail of who approved + when.
- *Files:* `approval_service.py` extensions, `approver_roles` table, listener watchdog, dashboard approval thread UI.

**Gap D — No real customer channels (DM/email/Q&A)**
- *Today:* `customer_questions` table from fake events; SupportResponseTool replies into nothing.
- *Needed:* `channel_adapters/` package (instagram_dm, email, trendyol_qa, shopify_msg); `customer_threads` table; sync worker pulling live channels; thread context piped into `business_retrieval_service`.
- *Files:* new `channel_adapters/`, new `customer_interaction_service.py`, `customer_threads` table.

**Gap E — No real e-commerce platform sync**
- *Today:* Products from `fake_data_generator` or mock CSV import.
- *Needed:* `platform_connectors/` (shopify, woocommerce, trendyol); sync worker; `product_sync_log`; webhook receivers for live stock/order updates.
- *Files:* new `platform_connectors/`, sync worker process, webhook endpoints.

### 16.3 Top 3 Production-Readiness Risks

**Risk α — Tenant isolation is `user_id`-only**
- No org/workspace hierarchy, no role split, no API key per user, no encrypted secrets. SQLite single-file with WAL ceiling ~10 writes/sec. A compromised `user_id` exposes everything that user owns.
- Needed: `orgs` + `org_members` + `api_keys` + `secrets_encrypted` tables; per-request auth gate that resolves `org_id` and `role`; encrypted-at-rest credentials (Fernet with KMS-equivalent key).

**Risk β — No financial instrumentation**
- AI cost is not tracked per task. Retries can silently burn OpenAI credit.
- Needed: `billing_events` table; `billing_service.py` records cost per AI call; quota-aware safety service; per-org spend caps + Stripe/billing stub.

**Risk γ — No SLA / delegation on approvals**
- Real commerce requires legal accountability. Currently the approval column is "approved_by" string only.
- Needed: as in Gap C — assigned-to, due-by, escalation, comment threads, immutable audit trail.

### 16.4 Remaining Duplication / Dead Code Paths

Already-known duplications (still present after Rev-2 stabilization):

- **Content/campaign creation has two paths** — rule engine (`semantic_parser` → `action_engine` → `workflow_service`) and autonomous planner (`autonomous_planner` → `planner_runtime` → `workflow_service`). Both produce workflows with overlapping metadata; no shared adapter. Recommend a thin `workflow_assembler.py` to converge.
- **Event vocab in three places** — `event_router.CRITICAL_EVENT_PREFIXES/EXACT`, `semantic_parser.KNOWN_EVENTS`, `rule_manager.KNOWN_WORKFLOWS`. Adding a new event still requires three edits. Recommend `event_ontology.py` (orthogonal to the business `ontology.py`).
- **Business-state aggregation re-runs per event** — `business_state.py`, `business_intelligence.py`, `cross_event_reasoner.py` re-query overlapping rollups. A 30s-TTL in-memory cache invalidated on timeline write would cut DB load significantly.

Dead code still safe to delete:

- `ai_planner.py` (only fires when `CRITICAL_FALLBACK=1`; default `0`). Recommend delete with one-line note in evolution plan.
- `rule_manager._extract_workflow` regex helper (active path uses `semantic_parser`).

*End of Rev-3 gap analysis.*

---

*End of audit.*
