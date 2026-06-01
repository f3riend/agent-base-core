# ARCHITECTURE_CLEANUP_PLAN.md

> Concrete cleanup + maturity plan for the AI-native Business Operating System.
> Pairs with `PROJECT_AUDIT.md` — the audit identifies the **what**, this plan defines the **order**, **scope**, and **file-level shape** of the fixes.
> No code has been changed yet at the moment this document was generated. Phases are gated by explicit user approval.

---

## 0. Operating Principles

1. **Stabilize first, mature second.** Maturity work that depends on broken foundations (e.g. semantic memory on top of broken planner) is wasted. The plan front-loads bug fixes that the rest of the work assumes to be correct.
2. **Audit-first, then surgical edits.** Each phase touches a bounded set of files. No phase ships a rewrite of the whole runtime.
3. **AI proposes, runtime executes.** Every change preserves this invariant. The autonomous planner never gains the ability to call workflow_service / task_service / tools directly. Approval and safety gates remain authoritative.
4. **Replay safety is a feature.** Anything that introduces wall-clock or model non-determinism must declare a deterministic fallback (see CB-11, AR-5).
5. **Multi-tenant by default.** Every new query takes `user_id`. Every new table has a `user_id` column. Every new endpoint filters by `user_id`. No silent defaults that paper over a missing kwarg.
6. **Observability is not optional.** Every new code path emits a structured trace via `observability._emit` AND (for orchestration-level decisions) a row in a queryable table.
7. **No new external integrations.** Fake tools stay fake. Integration interfaces (Phase 11) define the **shape** real adapters would slot into, but the test runtime never reaches outside the process.

---

## 1. Phase Map

| Phase | Goal | Files touched | Risk |
|---|---|---|---|
| **0** | Critical safety bugs from audit | `tool_sandbox.py`, `rule_service.py`, `workflow_service.py`, `orchestration_api.py`, `automation_log_service.py`, `tools.py`, `tool_schema_validator.py` | Low — single-line fixes mostly |
| **1** | Dedup + idempotency | `db.py`, `workflow_service.py`, `approval_service.py`, `task_service.py`, `crewai_worker.py` | Medium — touches schema |
| **2** | Unified ontology / intent registry | new `ontology.py`, `autonomous_planner.py`, `agent_registry.py`, `semantic_parser.py` | Medium — replaces 3 hardcoded maps |
| **3** | Event envelope standardization | new `event_envelope.py`, `listener.py`, `main.py` internal endpoints, `timeline_processing.py` | Medium — additive, no breaking writes |
| **4** | Conversational synthesis (business_chat) | `business_chat.py`, new `narrative_synth.py` | Low — narrative layer only |
| **5** | Planner entropy control | `autonomous_planner.py`, new `plan_validator.py` | Medium |
| **6** | Semantic memory + learning | `planner_memory.py`, `planner_learning.py`, `db.py` | Medium |
| **7** | Observability traces (DB-backed) | `observability.py`, new `traces` table in `db.py`, `orchestration_api.py` | Low |
| **8** | Dashboard UX repair | `index.html` | Medium — many small changes |
| **9** | BI + cross-event reasoning expansion | `business_intelligence.py`, `cross_event_reasoner.py`, `business_state.py` | Medium |
| **10** | Multi-agent foundation | `agent_runtime.py`, `agent_registry.py` | Low — interface scaffolding |
| **11** | Real-tool interface scaffolding | new `tool_adapters/` package | Low — interface-only |
| **12** | Production hardening | composite indexes in `db.py`, cooldown improvements, rate limiting, structured retry | Medium |

Phases 0–3 are **stabilization** and should land before anything else. Phases 4–7 are **AI operator experience**. Phases 8–9 are **operator-facing visibility**. Phases 10–12 are **forward scaffolding**.

---

## 2. Phase 0 — Critical Safety Bugs

These are the bugs verified to still be present per the audit re-check. All are surgical.

### 2.1 `tool_sandbox.py` — Real timeout (CB-1)

**Current bug:** `t0 = time.monotonic()` is sampled inside the retry loop, then compared to `time.monotonic()` on the very next line. Elapsed is always ~0µs; the timeout never fires.

**Change:**
- Use `concurrent.futures.ThreadPoolExecutor` to run `run_fn()` with a true wall-clock timeout via `future.result(timeout=timeout_sec)`.
- On `TimeoutError`, cancel the future, log a `TOOL_SANDBOX_TIMEOUT` event, and proceed to retry / final-fail path.
- Exponential backoff between retries: `0.5 * 2**attempt` capped at 8s.
- Add a per-tool circuit breaker: if a tool fails N times in a rolling window, emit `TOOL_CIRCUIT_OPEN` and short-fail subsequent calls for a cooldown.
- Track failure metrics in-memory (no DB write in the hot path).

### 2.2 `rule_service.py:121-132` — `list_rules(include_disabled=False)` (CB-2)

**Current bug:** Both branches execute the identical query; the flag has no effect.

**Change:** When `include_disabled=False`, append `WHERE enabled=1` to the SQL. Single-line fix.

### 2.3 `workflow_service.py:297` — Item-deleted parse corruption (CB-3)

**Current bug:** `if item["status"] if "status" in item.keys() else "active" == "deleted":` evaluates to a malformed ternary.

**Change:** `if (item.get("status") or "active") == "deleted":`. Single-line fix.

### 2.4 `orchestration_api.py:/tool-executions` (CB-4) + `/users`

**Current bug:** `/tool-executions` returns rows for every tenant. `/users` exposes all rows.

**Change:**
- Add `user_id: int = Query(1)` to `/tool-executions` and filter `WHERE t.user_id = ?`.
- `/users` is debug-only — gate behind `ALLOW_DEBUG_ENDPOINTS` env or remove from default registration.

### 2.5 `automation_log_service.py` — `user_id` propagation (CB-8)

**Current bug:** `_insert_log()` never receives `user_id`; DB default = 1 catches everything.

**Change:** Thread `user_id` through every public function (`log_workflow_created`, `log_workflow_cancelled`, `log_ai_task_created`, `log_tool_executed`) and `_insert_log`. Update every caller. Tenant isolation requires this.

### 2.6 `tools.py` + `tool_schema_validator.py` — Per-tool argument schemas (CB-15)

**Current bug:** All seven tools share `FakeToolArgs(input_text: str = "")`. CrewAI sees seven tools with identical, semantically empty signatures.

**Change:** Define one Pydantic args model per tool with required fields appropriate to its purpose:
- `InstagramCampaignArgs(headline: str, hook: str | None, target_audience: str | None, hashtags: list[str] = [])`
- `BannerGeneratorArgs(headline: str, subline: str | None, cta: str)`
- `CouponGeneratorArgs(label: str, percent: int = 10, expires_in_days: int = 7)`
- `FaqUpdateArgs(topic: str, question: str, answer: str)`
- `SupportResponseArgs(customer_question: str, tone: str = "friendly")`
- `TrendAnalysisArgs(focus: str, lookback_days: int = 7)`
- `LowStockNotificationArgs(item_name: str, current_stock: int)`

Each tool's `_run` accepts its specific fields. `validate_all_tools` continues to verify schemas; the new schemas are still OpenAI-valid but now semantically meaningful.

### 2.7 Verification

After Phase 0:
- Re-run `full_system_test.py` to confirm no regressions.
- Smoke-test: emit a known critical event, confirm timeline → workflow → ai_task → tool_execution → automation_log all carry the correct `user_id`.
- Trigger a deliberately slow tool (sleep 60s); confirm sandbox kills at 30s, returns `status=failed, error="tool timeout"`.

---

## 3. Phase 1 — Dedup + Idempotency

### 3.1 `db.py` — Partial unique index on `workflow_instances` (CB-5)

**Change:** Add at end of `init_db()`:
```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_active_unique
ON workflow_instances (user_id, workflow_name, entity_type, entity_id)
WHERE status IN ('scheduled', 'running');
```
This makes "an active workflow with this signature already exists" a DB-enforced constraint, killing the race window in `workflow_exists()` SELECT-then-INSERT.

`workflow_service.create_workflow` catches `sqlite3.IntegrityError` from this index and treats it as "already-exists" (return the existing id).

### 3.2 `db.py` — Approval request dedup

**Change:** Add `proposal_hash` column to `approval_requests`. Compute as SHA-1 of `(user_id, event_id, workflow_name, sorted(tools))`. Add partial unique index on `(user_id, proposal_hash)` where `status='pending'`. `approval_service.create_approval_request` catches the integrity error and returns the existing pending request.

### 3.3 `task_service.py` — Retry backoff (CB-17)

**Current bug:** `fail_task` flips `pending → retrying` with no sleep; the CrewAI worker picks it up on the next 5s poll.

**Change:** Add a `next_retry_at` column to `ai_tasks` (NULL = ready). `fail_task` sets `next_retry_at = utcnow + 2^retry_count * 30s` (cap 30 min). `crewai_worker` polls `WHERE status IN ('pending','retrying') AND (next_retry_at IS NULL OR next_retry_at <= utcnow)`.

### 3.4 `crewai_worker.py` — Empty tool defensive path (CB-7)

**Change:** If `_resolve_tools_for_task` returns `[]`, fail the task cleanly with `error="no_tools_resolved"` and `status=failed` (no retry). Do not raise `ValueError`; do not silently fall back to a hardcoded tool. The hardcoded fallback hides ontology bugs.

### 3.5 `db.py` — Composite indexes for hot paths

Add (one CREATE per line):
- `idx_ai_tasks_status_user ON ai_tasks (status, user_id)`
- `idx_workflows_status_scheduled ON workflow_instances (status, scheduled_at)`
- `idx_workflows_user ON workflow_instances (user_id, status)`
- `idx_logs_user_created ON automation_logs (user_id, created_at DESC)`
- `idx_planner_memory_user_entity ON planner_memory (user_id, entity_type, entity_id, created_at DESC)`
- `idx_tool_exec_task ON tool_executions (task_id)`
- `idx_approval_user_status ON approval_requests (user_id, status, created_at DESC)`

---

## 4. Phase 2 — Unified Ontology

The audit's AR-3 names this directly: three competing ontologies in `semantic_parser.INTENT_ONTOLOGY`, `autonomous_planner.INTENT_FROM_BI`, `agent_registry._intent_to_domain`. These maps were authored independently and have drifted — see CB-12 (shipping_delay / inventory_risk unmapped) and CB-13 (dead entries).

### 4.1 New module: `ontology.py`

A single declarative registry:
```python
INTENTS = {
    "low_stock_alert":        {"domain": "inventory",  "default_tools": ["low_stock_notification_tool"], "critical": True},
    "discount_promotion":     {"domain": "marketing",  "default_tools": ["instagram_campaign_tool","coupon_generator_tool"]},
    "growth_marketing":       {"domain": "marketing",  "default_tools": ["instagram_campaign_tool","banner_generator_tool"]},
    "marketing_campaign":     {"domain": "marketing",  "default_tools": ["instagram_campaign_tool"]},
    "reputation":             {"domain": "support",    "default_tools": ["support_response_tool","faq_update_tool"]},
    "customer_support":       {"domain": "support",    "default_tools": ["support_response_tool"]},
    "shipping_response":      {"domain": "logistics",  "default_tools": ["support_response_tool","faq_update_tool"]},
    "inventory_review":       {"domain": "inventory",  "default_tools": ["low_stock_notification_tool"]},
    "insights":               {"domain": "analytics",  "default_tools": ["trend_analysis_tool"]},
    "store_welcome":          {"domain": "support",    "default_tools": ["support_response_tool"]},
}

BI_INSIGHT_TO_INTENT = {
    "campaign_opportunity":    "discount_promotion",
    "price_drop_promotion":    "discount_promotion",
    "viral_product":           "growth_marketing",
    "engagement_spike":        "growth_marketing",
    "customer_dissatisfaction":"reputation",
    "reputation_risk":         "reputation",
    "seasonal_opportunity":    "marketing_campaign",
    "sales_drop":              "insights",
    "repeat_failures":         "insights",
    "shipping_delay":          "shipping_response",  # was unmapped
    "inventory_risk":          "inventory_review",   # was unmapped
}
```

Plus helper functions:
- `intent_for_insight(insight_type: str) -> str`
- `domain_for_intent(intent: str) -> str`
- `default_tools_for_intent(intent: str) -> list[str]`
- `is_known_intent(intent: str) -> bool`

### 4.2 Callers to migrate

- `autonomous_planner.INTENT_FROM_BI` — delete; import from ontology.
- `agent_registry._intent_to_domain` — delete; import from ontology.
- `semantic_parser` — keep its `INTENT_ONTOLOGY` (it produces rule-matched intents which are conceptually different), but cross-check that every output intent is in `ontology.INTENTS` (validation, not replacement).

### 4.3 Dead code removal

- `ai_planner.py` is unreachable under default config (AR-4). Either delete or move to a `legacy/` subfolder with a feature flag and a one-line README explaining it.
- Decision for this plan: **delete after Phase 0–2 are stable**. Removing dead code reduces audit surface.

---

## 5. Phase 3 — Event Envelope Standardization

The user's brief calls out drift in event vocab and shape. Listener events come from `fake_ai_api.db.timeline`; tool events come from `fake_tool_timeline.emit_tool_event`; planner-generated events go nowhere uniform.

### 5.1 New module: `event_envelope.py`

```python
@dataclass
class EventEnvelope:
    id: int
    type: str           # e.g. "order.shipped"
    category: str       # e.g. "commerce"
    source: str         # e.g. "fake_platform" | "tool_emit" | "planner"
    priority: str       # "critical" | "normal" | "background"
    tenant_id: int      # multi-tenant; was scattered as "user_id"
    causation_id: int | None    # the event_id that *caused* this one
    correlation_id: str         # workflow- or session-level
    payload: dict
    meta: dict
    created_at: str

    def as_row(self) -> dict: ...
    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "EventEnvelope": ...
    @classmethod
    def from_legacy(cls, row: sqlite3.Row) -> "EventEnvelope":
        """Adapt the existing fake_ai_api.db.timeline rows."""
```

### 5.2 Timeline writes use it

- `main.py:/internal/create-*` adapters serialize via `EventEnvelope.as_row()` before INSERT.
- `fake_tool_timeline.emit_tool_event` produces envelopes too — and crucially **tags them** `source="tool_emit"`, `causation_id=<originating event id>` so the listener can skip them in primary-path planning (kills the synthetic-event-echo problem from audit §8).

### 5.3 Listener adapts

`listener.process_event(event)` calls `EventEnvelope.from_legacy(event)` on entry. Downstream code sees a typed envelope; no more `event["group"]` / `event["subject"]["type"]` scattered string indexing.

### 5.4 Backward compatibility

Existing rows in `fake_ai_api.db.timeline` already have most columns; the envelope reads from them. No destructive migration required for the test runtime.

---

## 6. Phase 4 — Conversational Synthesis (Business Operator Tone)

### 6.1 Current state

`business_chat.answer_question` concatenates fragments per-intent. Reads as a debug dump (audit §11 confirms this is a real symptom).

### 6.2 Target

The assistant should sound like an **ops strategist**, not a structured-text emitter. The user's brief gives the canonical example: "Son birkaç gündür satışlarda belirgin bir düşüş var gibi görünüyor. Özellikle negatif yorumların artması ..."

### 6.3 Implementation

New module: `narrative_synth.py`. Pure pipeline, no AI model required (so deterministic by default):
```python
def synthesize_narrative(
    intent: str,
    state: dict,
    bi: dict,
    cross: dict,
    memory: dict,
    workflows: list[dict],
) -> dict:
    """Returns {narrative: str, recommendations: list[str], confidence: float}."""
```
Internal pipeline:
1. **Signals extraction** — collect typed signals: sales_trend, sentiment_trend, shipping_health, inventory_health, campaign_health, engagement_health.
2. **Correlation pass** — pair signals using a small handwritten rule set (sales↓ + reviews↓ → "customer satisfaction concern"; sales↓ + campaign_recent → "campaign underperforming"; etc.).
3. **Narrative templates** — Turkish first-person operator templates parametrized by signal values. Templates declare both a primary observation and a soft recommendation list ("istersen ... hazırlayabilirim").
4. **Recommendation gating** — only suggest tools/workflows that are actually in the registry and ontology.

If `OPENAI_API_KEY` is set and `BUSINESS_CHAT_USE_AI=1`, an optional CrewAI Agent rewrites the deterministic narrative into more fluent prose. The deterministic narrative is always computed first; AI only restyles. This keeps the runtime explainable and replayable.

### 6.4 `business_chat.answer_question` rewrite

Becomes a thin orchestrator:
```python
def answer_question(question, user_id=1):
    intent = _match_intent(question)
    state = build_business_state(user_id)
    bi = analyze(...)
    cross = reason_across_events(user_id)
    memory = get_memory_summary_for_api(user_id, limit=10)
    workflows = _fetch_workflows(user_id)
    out = synthesize_narrative(intent, state, bi, cross, memory, workflows)
    return { ...same shape as today, but `answer` = out["narrative"] }
```

---

## 7. Phase 5 — Planner Entropy Control

### 7.1 New module: `plan_validator.py`

```python
def validate_plan(plan: dict, ontology) -> tuple[bool, list[str]]:
    """
    Checks:
      - workflow_name is snake_case, length 3..56
      - decision in {create_workflow, cancel_workflow, noop}
      - tools is a list of registered tool names
      - business_intent is in ontology.INTENTS or explicitly "general_marketing"
      - confidence in [0,1]
      - priority in {low, medium, high}
      - requires_approval bool
    Returns (ok, errors).
    """

def canonicalize_workflow_name(intent: str, entity_type: str, entity_id: int) -> str:
    """Deterministic workflow naming so duplicate intents don't produce duplicate workflows."""

def is_duplicate_proposal(plan, recent_memory) -> bool:
    """Suppress identical plans within a short window."""

def safety_score(plan, ctx) -> float:
    """0..1 multiplier on confidence based on tool risk, history, approval state."""
```

### 7.2 `autonomous_planner.py` integration

- After `_plan_with_crewai`/`_plan_from_context`, call `validate_plan`. If invalid, log `PLANNER_PLAN_INVALID` and fall through to `_noop`.
- Use `canonicalize_workflow_name` so `discount_promo_v1` vs `discount_promo_v2` don't bypass dedup.
- Use `is_duplicate_proposal` against `build_memory_context` before recording.
- Apply `safety_score` as multiplier on `confidence`.
- Replace `re.search(r"\{[\s\S]*\}", result)` (AR-5) with robust parser: `try: json.loads(result)`; fallback to `json.loads(_strip_markdown_fences(result))`; fallback to `_extract_json_object(result)` (balanced-brace scanner, not regex); final fallback to `_noop("malformed plan")`.

### 7.3 Plan output validated with Pydantic

```python
class PlannerOutput(BaseModel):
    decision: Literal["create_workflow","cancel_workflow","noop"]
    workflow_name: str | None
    reason: str
    tools: list[str] = []
    priority: Literal["low","medium","high"] = "medium"
    confidence: float = Field(ge=0, le=1)
    requires_approval: bool = False
    business_intent: str
    delay: int = 0
```
Pydantic catches malformed AI output before it ever reaches `apply_proposal`.

---

## 8. Phase 6 — Semantic Memory + Long-Term Learning

### 8.1 `planner_memory.py` expansion

Today: one row per decision in `planner_memory`. Mostly structured log.

Add tables (declared in `db.py`):
```sql
CREATE TABLE IF NOT EXISTS reasoning_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    event_id INTEGER,
    plan_id INTEGER,         -- references planner_memory.id (logical, no FK)
    trace_type TEXT,         -- 'reasoning' | 'signal' | 'tool_selection' | 'memory_recall'
    summary TEXT,
    details_json TEXT,
    embedding_blob BLOB,     -- reserved, nullable, no vector DB yet
    created_at TEXT NOT NULL
);
CREATE INDEX idx_traces_user_event ON reasoning_traces (user_id, event_id, created_at DESC);

CREATE TABLE IF NOT EXISTS planner_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    plan_id INTEGER,
    workflow_id INTEGER,
    outcome TEXT,            -- 'approved'|'rejected'|'auto_applied'|'completed_success'|'completed_failed'
    feedback TEXT,
    measured_at TEXT NOT NULL
);
CREATE INDEX idx_outcomes_user ON planner_outcomes (user_id, measured_at DESC);
```

### 8.2 Interfaces

- `record_reasoning_trace(user_id, event_id, plan_id, trace_type, summary, details)` — called from `observability.log_*` functions when they also want persistence.
- `record_planner_outcome(user_id, plan_id, workflow_id, outcome, feedback=None)` — called from approval flow, workflow worker completion, AI task failure.
- `recall_similar(user_id, query_text, k=5)` — today: SQL `LIKE` + heuristic ranking; tomorrow: vector index. The function signature is the abstraction. **No vector DB this phase.**

### 8.3 `planner_learning.py` adaptation

Drive confidence adjustment from `planner_outcomes` instead of (only) `planner_memory.outcome`. Bound stays ±0.05; sample threshold stays ≥3.

---

## 9. Phase 7 — Observability Traces (DB-backed)

### 9.1 New table

```sql
CREATE TABLE IF NOT EXISTS orchestration_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER DEFAULT 1,
    event_id INTEGER,
    workflow_id INTEGER,
    task_id INTEGER,
    trace_tag TEXT,                  -- e.g. AI_REASONING, BUSINESS_SIGNAL, TOOL_SELECTION
    summary TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_traces_tag_created ON orchestration_traces (trace_tag, created_at DESC);
CREATE INDEX idx_traces_user_created ON orchestration_traces (user_id, created_at DESC);
```

### 9.2 `observability.py` becomes dual-write

`_emit(tag, payload, *, persist=False, user_id=None, event_id=None, workflow_id=None, task_id=None)` writes stdout AND, if `persist=True`, a row in `orchestration_traces`. Existing call sites are kept as-is (persist defaults False); planner / routing / tool-selection sites flip to `persist=True`.

### 9.3 New API endpoints (in `orchestration_api.py`)

- `GET /api/internal/traces?user_id=1&tag=AI_REASONING&limit=50`
- `GET /api/internal/traces/by-event/{event_id}`

Dashboard surfaces a "Reasoning Feed" panel from these endpoints.

---

## 10. Phase 8 — Dashboard UX Repair

### 10.1 The typing-loss class fix (CB-16)

Current: only `chatInput` + `ruleInput` are guarded; refresh repeatedly clobbers other inputs.

Change: a class-level focus tracker.
```js
const inputFocus = { active: false, el: null, debounce: null };
document.addEventListener("focusin", (e) => {
  if (e.target.matches("input[type=text], textarea, select, [contenteditable]")) {
    inputFocus.active = true;
    inputFocus.el = e.target;
  }
});
document.addEventListener("focusout", () => {
  inputFocus.active = false;
  inputFocus.el = null;
});
function shouldSkipRefresh() { return inputFocus.active; }
```
`setListHtml`, `setText`, and the 12s `setInterval` all check `shouldSkipRefresh()` first. When a refresh is skipped, schedule a single deferred refresh 1500ms after focus is lost.

### 10.2 Partial section updates

Today: every poll re-renders every section. Move each section to its own fetch function and re-render only on a content delta (already partially there via `htmlCache`; harden by hashing the JSON response, not the rendered HTML).

### 10.3 Reasoning feed panel

Add a "AI Düşünce Akışı" panel rendered from `/api/internal/traces?tag=AI_REASONING` — the operator can see *why* the system did what it did, in real time.

### 10.4 Approval center

Today's approvals UI is buried; surface counts at the top of the dashboard, with a single-click approve/reject and a comment field for `feedback`.

### 10.5 Business operator chat

Already in `index.html`; bind to the new `business_chat.answer_question` output, which now produces narrative text. Render with `white-space: pre-line`. Mark messages as ephemeral; do not refresh-rerender the chat history.

---

## 11. Phase 9 — BI + Cross-Event Reasoning Expansion

### 11.1 `business_intelligence.py`

- Replace `datetime.utcnow().month` (CB-11) with `month = (event_ts or utcnow).month`. Replay-safe: the event's timestamp is the source of truth, not wall-clock.
- Add detectors:
  - `_detect_shipping_health(user_id)` — % of orders in last 7d with shipping_delay flag.
  - `_detect_sentiment_trend(user_id)` — slope of negative_review count over time.
  - `_detect_engagement_anomaly(user_id)` — banner CTR z-score.
  - `_detect_campaign_performance(user_id)` — for each recent campaign, success/failure outcomes from `planner_outcomes`.
  - `_detect_faq_clusters(user_id)` — group customer_question events by token similarity (simple bag-of-words for now).
  - `_detect_viral_product(user_id)` — already partially in `detect_trending_products`; reuse.
- `analyze()` returns an additional `narrative_hints` list — short prose hints that `narrative_synth` can stitch.

### 11.2 `cross_event_reasoner.py`

- Add temporal weighting: events in the last hour weigh 1.0; last day 0.6; last week 0.2.
- Add causal hypothesis builder: pre-defined small set of (cause, effect) pairs the reasoner looks for. Output `hypotheses: list[{cause, effect, confidence, evidence}]`.
- Add state transition output: `business_state_change: "stable" | "concerning" | "deteriorating" | "improving"`.

### 11.3 `business_state.py`

Today: snapshot of sales/inventory/engagement/campaigns. Add: `tool_performance` (from `tool_executions`), `workflow_health` (success rate), and a single composite `health_score: 0..100` for the dashboard.

---

## 12. Phase 10 — Multi-Agent Foundation

The user is explicit: **don't ship a fully autonomous multi-agent system this phase**. Only prepare the interfaces.

### 12.1 `agent_runtime.py` becomes the dispatcher

```python
class AgentRegistry:
    def get(self, domain: str) -> Agent: ...
    def list(self) -> list[Agent]: ...

class Agent(Protocol):
    name: str
    domain: str
    def can_handle(self, intent: str) -> bool: ...
    def propose(self, ctx: PlannerContext) -> ProposalDraft | None: ...

class MarketingAgent(Agent): ...     # stub
class SupportAgent(Agent): ...        # stub
class InventoryAgent(Agent): ...      # stub
class AnalyticsAgent(Agent): ...      # stub
class ExecutiveAgent(Agent): ...      # composer (sees all proposals)
```

Today's autonomous planner calls `agent_runtime.dispatch(ctx)` which is just `MarketingAgent.propose(ctx)` under the hood. The other agents return `None`. When future phases activate them, the dispatch order is a small change.

### 12.2 Shared context

`PlannerContext` becomes a typed dataclass produced by `build_planner_context()` once and passed by reference; today's nested-dict approach is brittle.

---

## 13. Phase 11 — Real-Tool Interface Scaffolding

Per the user's brief: "Keep fake tools working. BUT: prepare clean interfaces for future Instagram / Trendyol / Shopify / WooCommerce / analytics APIs."

### 13.1 New package: `tool_adapters/`

```
tool_adapters/
    __init__.py
    base.py          # AdapterProtocol — connect / publish / fetch / health_check
    fake.py          # what we have today, refactored under the protocol
    instagram.py     # raise NotImplementedError("real integration disabled")
    trendyol.py      # ditto
    shopify.py       # ditto
    woocommerce.py   # ditto
```

`tools.py` reads from `tool_adapters.fake` for the runtime; the real adapters exist as type-checkable shells. No real network calls anywhere.

---

## 14. Phase 12 — Production Hardening

- **Approval cooldown** — `approval_service` keeps a per-user rolling window; if more than N approvals/h are open, queue and emit a `APPROVAL_THROTTLED` trace.
- **Workflow idempotency** — handled by Phase 1 unique index.
- **Replay safety** — covered by Phase 3 (envelope) + Phase 9 (frozen month) + Phase 5 (validated AI output).
- **Rate limiting** — `safety_service.validate_autonomous_execution` already enforces hourly/daily quota; add a cooldown on the **intent**, not the workflow_name (CB-10), keyed on `(user_id, entity_type, entity_id, intent)`.
- **CORS** — remove `allow_origins=["*"]` for non-local builds; gate on `ALLOW_OPEN_CORS` env.
- **Auth** — keep test mode untouched; document explicitly that `/internal/*` must not be exposed.

---

## 15. What This Plan Explicitly Will NOT Do

- **No vector database.** Embedding column is reserved (BLOB, nullable) for the future. No FAISS, no Chroma, no pgvector. Today's recall is SQL `LIKE` + heuristic.
- **No real external integrations.** The adapter package is interface-only.
- **No full multi-agent autonomy.** The agent dispatcher is a stub for shape, not behavior.
- **No new ML training loop.** Learning stays as bounded confidence adjustment (±0.05). No model fine-tuning, no policy network.
- **No replacement of CrewAI.** CrewAI stays as the execution intelligence layer.
- **No rewrite of `main.py`.** It's 2071 lines and works; touched only for the internal event endpoints during Phase 3.
- **No move off SQLite.** Composite indexes + partial unique indexes are the scaling intervention. Migration to Postgres is a separate plan.

---

## 16. Implementation Sequence (Recommended)

The user explicitly asked for stabilization first. I propose this order:

1. **Phase 0** — all bug fixes (low risk, high leverage). 
2. **Phase 1** — dedup + idempotency (depends on Phase 0).
3. **Phase 2** — ontology unify (depends on Phase 0; unlocks Phase 4, 5, 9).
4. **Phase 7** — observability traces (independent; needed for Phase 4 + 8).
5. **Phase 4** — conversational synthesis (depends on Phase 2, 7).
6. **Phase 5** — planner entropy control (depends on Phase 2).
7. **Phase 6** — semantic memory + learning (depends on Phase 2, 7).
8. **Phase 9** — BI + cross-event expansion (depends on Phase 6).
9. **Phase 3** — event envelope (additive; can land any time after Phase 1; reduces risk for Phase 9).
10. **Phase 8** — dashboard UX (depends on Phase 7 for the reasoning feed).
11. **Phase 10** — multi-agent scaffolding (depends on Phase 2).
12. **Phase 11** — adapter scaffolding (independent).
13. **Phase 12** — production hardening (depends on most prior phases).

Each phase ships independently; each phase ends with the relevant subsection of `full_system_test.py` passing.

---

*End of original plan.*

---

## 17. Revision log — what has actually shipped

### Shipped: Phase 0 — Critical safety bugs
- `tool_sandbox.py`: real `concurrent.futures` timeout, exponential backoff, in-memory circuit breaker, sandbox now wired into every tool execution via `tools._fake_run`.
- `rule_service.list_rules`: `WHERE enabled=1` filter on the false branch.
- `workflow_service.py:297`: item-deleted parse fix.
- `orchestration_api`: `/tool-executions` tenant filter, `/users` gated behind `ALLOW_DEBUG_ENDPOINTS`.
- `automation_log_service`: `user_id` threaded through every public fn; all callers updated.
- `tools.py`: seven per-tool Pydantic argument schemas (Instagram, Banner, Coupon, FAQ, Support, Trend, LowStock).

### Shipped: Phase 1 — Dedup + idempotency
- Partial unique index on `workflow_instances(user_id, name, entity_type, entity_id) WHERE status IN ('scheduled','running')`.
- Partial unique index on `approval_requests(user_id, proposal_hash) WHERE status='pending'`.
- `ai_tasks.next_retry_at` column + `_retry_delay_seconds` in `task_service.fail_task`.
- `get_pending_tasks` filters by `next_retry_at`.
- `crewai_worker.NoToolsResolvedError` → clean task cancellation (no retry storm).
- Eleven composite indexes on hot multi-tenant queries.

### Shipped: Phase 2 — Unified ontology
- New `ontology.py` is now the single source of truth.
- `autonomous_planner.INTENT_FROM_BI` deleted; uses `ontology.intent_for_insight`.
- `agent_registry._intent_to_domain` kept as thin wrapper around `ontology.domain_for_intent`.
- `shipping_delay` → `shipping_response`, `inventory_risk` → `inventory_review` (CB-12 fixed).

### Shipped: Phase 3 — Event envelope
- New `event_envelope.py` with typed `EventEnvelope` and `from_legacy()` adapter.
- `fake_tool_timeline.emit_tool_event` tags emitted events `source="tool_emit"`, `priority="background"`, `causation_id`.
- Listener recognises `envelope.is_synthetic` and short-circuits with `synthetic_skip` reason — kills tool-event-echo replay risk.

### Shipped: Phase 4 — Conversational synthesis
- New `narrative_synth.py`: deterministic signal-extraction → correlation → Turkish operator-tone narrative + ontology-filtered recommendations.
- `business_chat.py` rewritten as thin orchestrator; output matches the user's "GOOD" example template.
- Optional `BUSINESS_CHAT_USE_AI=1` for an AI restyle pass on top of the deterministic narrative.

### Shipped: Phase 5 — Planner entropy control
- New `plan_validator.py`: Pydantic `PlannerOutput` schema, balanced-brace JSON parser (replaces AR-5 regex), `canonicalize_workflow_name`, `is_duplicate_proposal`, `safety_score`.
- `autonomous_planner.create_plan` validates → canonicalizes → suppresses duplicates → multiplies confidence by safety score.

### Shipped: Phase 6 — Semantic memory + learning
- New `planner_outcomes` table (with composite indexes) + `record_planner_outcome()`.
- Outcomes wired into approval approve/reject + `task_service.complete_task`.
- `recall_similar_campaigns` re-ranks by outcome weight (successful memories rank higher).

### Shipped: Phase 7 — Observability traces
- New `orchestration_traces` table + indexes.
- `observability._emit` is now dual-write — stdout always, DB persistence when `persist=True`.
- All seven `log_*` functions persist by default with `user_id`/`event_id`/`workflow_id` propagation.
- New API: `GET /api/internal/traces`, `/traces/tags`, `/traces/by-event/{id}`, `/orchestration-health`.

### Shipped: Phase 9 — BI + cross-event reasoning
- `business_intelligence._event_month` — replay-safe: reads month from `event.ts` first, wall-clock last (CB-11 fixed).
- `cross_event_reasoner.py` rewritten with temporal weighting (1h=1.0, 1d=0.6, 7d=0.2, older=0.05) and evidence-tagged hypotheses.
- New: `business_state_transition` field (`stable | concerning | deteriorating | improving`) aggregated from weighted hypotheses.

### Shipped: Phase NEW — Internal HTTP loop fix (CB-19)
- New `internal_service.py` with 12 pure functions matching every `/internal/*` operation.
- `main.py` `/internal/*` route handlers reduced to thin wrappers (~5 lines each).
- `fake_data_generator.py`, `business_activity_simulator.py`, `product_import_service.py` no longer import `requests` — every write goes through the in-process service layer.
- `assert_not_internal_http_loop(url)` helper raises `InternalHTTPLoopError` when in-process code tries to HTTP into 127.0.0.1.
- `INTERNAL_SERVICE_IN_PROCESS=1` set by `main.py` at import; CLI scripts run unaffected.

### Not yet shipped (deferred)
- **Phase 8 — Dashboard UX repair** (CB-16 typing reset; AI reasoning feed UI panel).
- **Phase 10 — Multi-agent dispatcher scaffolding**.
- **Phase 11 — Real-tool adapter scaffolding**.
- **Phase 12 — Production hardening** (CORS gating, auth gating, intent-level cooldown).

*End of plan revision log.*
