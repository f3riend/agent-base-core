# System Review — for external AI consultation

> **Purpose:** I'm building an AI-native business operations runtime. This
> document is for external AI systems to review and tell me what is missing,
> what is over-engineered, and what is at risk. Operator UX is Turkish;
> the rest is English. File:line citations are real and verifiable.
>
> **What I want from a reviewer:** concrete weaknesses, not encouragement.

---

## 1. System identity in one paragraph

A hybrid event-driven orchestration runtime that ingests business events
from a (currently fake) e-commerce platform, routes them through a
**deterministic rule engine** for critical paths and an **autonomous AI
planner** for creative/hybrid paths, gates AI proposals through an approval
+ safety layer, and executes them as workflows that spawn AI tasks consumed
by a CrewAI worker calling fake-but-typed tools. On top of that, there is a
multi-turn AI operator chat (LLM-backed, OpenAI gpt-4o-mini) with real
data retrieval, conversational memory, deliberation-stage UI, and an
Observability Center dashboard. The frontend is a single-file vanilla-JS
+ Tailwind-CDN dashboard. Multi-tenant by `user_id` + `org_id`. Production
target is "AI-native commerce/social operations platform" — not a chatbot,
not a Zapier clone.

---

## 2. Operating principles (deliberate choices)

These are commitments — please critique whether the trade-offs are right.

1. **AI proposes, runtime executes.** No code path lets the LLM directly
   trigger a tool or write to a production resource. Every AI decision
   produces a `Plan` object that flows through validation → approval gate
   (if external) → safety quota → workflow scheduler → AI task → CrewAI
   tool. The runtime is authoritative.

2. **Deterministic core.** Routing, idempotency, replay safety, and
   state machines are deterministic. Non-determinism (LLM output, wall
   clock) is fenced: planner output is validated via Pydantic; the
   seasonal heuristic reads month from `event.ts` not `utcnow()`.

3. **Event-sourced timeline.** Every business effect produces a row in
   the timeline (`fake_ai_api.db.timeline`). Tools emit events back into
   the timeline tagged `source="tool_emit"` so the listener can recognize
   synthetic echoes and skip them.

4. **Modular monolith, not microservices.** One repo, one Python process
   per role: 1 FastAPI server + 3 workers (listener, workflow_worker,
   crewai_worker). No Kubernetes. No Docker compose yet.

5. **In-process service layer, never self-HTTP.** Any code inside the
   FastAPI process that wants to act on internal resources calls the
   `internal_service.py` functions directly. There is a runtime guard
   (`assert_not_internal_http_loop`) that raises if any code in the API
   process tries to HTTP into 127.0.0.1.

6. **Observability is mandatory.** Every orchestration decision writes
   stdout AND a row to `orchestration_traces`. The dashboard reads from
   the table, not from stdout. Summaries are humanized at write time so
   the reasoning feed reads as operator language, not technical strings.

7. **Multi-tenant by column.** Every business table has `user_id`. The
   newer org layer adds `org_id`. Endpoints that filter by user keep
   working unchanged; endpoints that opt into org-wide widening read
   from `org_id` (via X-API-Key header or query param).

8. **Encryption-at-rest for credentials.** OAuth/API tokens are
   Fernet-encrypted (`cryptography`). The DB never contains plaintext.

---

## 3. Architecture topology

```
┌─────────────────────────────────────────────────────────────────────┐
│  FastAPI app (main.py + orchestration_api.py)                       │
│   - 16 fake commerce endpoints  (/internal/create-*)                 │
│   - 70+ dashboard/api endpoints (/api/internal/*)                    │
│   - serves index.html (vanilla JS + Tailwind CDN)                    │
└─────────────────────────────────────────────────────────────────────┘
       │ writes via in-process internal_service ───┐
       ▼                                            ▼
┌───────────────────────────┐         ┌───────────────────────────┐
│ fake_ai_api.db (SQLite)   │         │ listener.db (SQLite)      │
│ - stores, items, orders,  │         │ - rules, workflow_instances,│
│   reviews, campaigns      │         │   ai_tasks, tool_executions,│
│ - TIMELINE (event log)    │         │   planner_memory, traces,   │
│                           │         │   approvals, campaigns,     │
│                           │         │   orgs, api_keys,           │
│                           │         │   social_credentials, ...   │
└───────────────────────────┘         └───────────────────────────┘
       ▲                                            ▲
       │ polls TIMELINE                             │ polls workflow_instances
┌──────┴──────────────┐                  ┌─────────┴───────────────┐
│ listener.py         │ ── routes ───▶  │ workflow_worker.py      │
│ (rule + planner)    │                  │ + scheduling_service fire│
└─────────────────────┘                  └─────────┬───────────────┘
                                                    │ creates ai_tasks
                                                    ▼
                                          ┌─────────────────────┐
                                          │ crewai_worker.py    │
                                          │ (CrewAI + tools.py) │
                                          └─────────────────────┘
```

- **Processes:** 4 (API server, listener, workflow_worker, crewai_worker)
- **Databases:** 2 SQLite files (commerce simulator + orchestration runtime)
- **Frontend:** 1 file (`index.html`), polling every 7–22 seconds per panel
- **Repo:** **58 Python files, ~22,000 lines total**
- **Listener.db tables (31):** ai_tasks, api_keys, approval_requests,
  automation_logs, campaign_metrics, campaigns, chat_sessions, chat_turns,
  customer_messages, customer_threads, execution_cooldowns, items,
  listener_state, orchestration_traces, orders, org_members, orgs,
  planner_learning_stats, planner_memory, planner_outcomes,
  planner_proposals, rule_history, rules, safety_counters,
  scheduled_entries, social_credentials, stores, tool_executions, users,
  workflow_instances.

---

## 4. Module inventory by subsystem

| Subsystem | Key modules |
|---|---|
| **Event source (fake platform)** | `main.py` (2071 lines), `internal_service.py`, `fake_data_generator.py`, `business_activity_simulator.py`, `product_import_service.py` |
| **Ingest & routing** | `listener.py`, `event_router.py`, `event_envelope.py`, `resource_service.py`, `timeline_service.py`, `timeline_processing.py` |
| **Rule engine** | `rule_engine.py`, `rule_service.py`, `rule_manager.py`, `semantic_parser.py`, `action_engine.py`, `rules.txt` |
| **Autonomous planner** | `autonomous_planner.py`, `planner_runtime.py`, `planner_memory.py`, `planner_learning.py`, `plan_validator.py`, `cross_event_reasoner.py`, `business_intelligence.py`, `business_state.py`, `agent_registry.py`, `ontology.py` |
| **Workflow + tasks** | `workflow_service.py`, `workflow_worker.py`, `task_service.py` |
| **Tools / sandbox** | `tool_registry.py`, `tools.py`, `tool_schema_validator.py`, `tool_sandbox.py`, `crewai_worker.py`, `fake_tool_timeline.py` |
| **Approval & safety** | `approval_service.py`, `safety_service.py` |
| **AI cognition (chat)** | `business_chat.py`, `business_query_router.py`, `business_retrieval_service.py`, `ai_synthesizer.py`, `conversation_memory.py`, `narrative_synth.py`, `context_compressor.py` |
| **Lifecycle services** | `scheduling_service.py`, `customer_interaction_service.py`, `campaign_service.py` |
| **Multi-tenant & security** | `auth_service.py`, `social_credentials.py` |
| **Observability** | `observability.py`, `automation_log_service.py` |
| **Persistence** | `db.py`, schema in `init_db()` |
| **Surface** | `orchestration_api.py` (FastAPI router), `index.html` |

Audit + planning docs: `PROJECT_AUDIT.md` (the comprehensive bug/risk
audit — Rev 1/2/3), `ARCHITECTURE_CLEANUP_PLAN.md` (Phases 0–12),
`ARCHITECTURE_EVOLUTION_PLAN.md` (forward-looking platform plan A–G),
`FRONTEND_REBUILD_PLAN.md` (UX evolution).

---

## 5. End-to-end event lifecycle

```
1. Fake platform write
   POST /internal/create-order (or another internal endpoint)
   → internal_service.create_order()
   → fake_ai_api.db.timeline gets a new row

2. Listener polls (every 2s)
   → fetches new timeline rows
   → wraps each in EventEnvelope (typed view)
   → skips synthetic events (source="tool_emit", priority="background")
   → routes via event_router (critical | creative | hybrid | analytical | monitoring)

3a. CRITICAL path
   → rule_engine.find_matching_rules
   → action_engine.execute_rule_actions
   → workflow_service.create_workflow

3b. CREATIVE / HYBRID path
   → autonomous_planner.create_plan
       - builds rich context (BI insights, business_state, memory, cross-event)
       - calls _plan_with_crewai (LLM via CrewAI) or _plan_from_context (deterministic)
       - validates via plan_validator (Pydantic + canonical naming + duplicate
         suppression + safety_score)
       - if intent ∈ {discount_promotion, growth_marketing, marketing_campaign}:
         attaches campaign_id (campaign created in 'draft')
   → planner_runtime.handle_autonomous_event
       - if requires_approval → approval_service.create_approval_request (STOP)
       - else: safety_service.validate (hourly/daily quotas + cooldowns)
       - → workflow_service.create_workflow with metadata.plan

4. Workflow worker polls (every 5s)
   → fire_due_schedules() — scheduling_service entries become workflows
   → execute workflows with status=scheduled where scheduled_at <= now
   → validate (critical workflows do entity-state checks)
   → if metadata.campaign_id → campaign_service.launch_campaign (draft → live)
   → task_service.create_ai_task

5. CrewAI worker polls (every 5s)
   → checks next_retry_at (exponential backoff)
   → resolves tools via CRITICAL_TASK_MAP or get_tools_for_intent
   → crew.kickoff() — each tool's _run wrapped by tool_sandbox
       (real timeout via concurrent.futures, retry, circuit breaker)
   → tools emit synthetic timeline events tagged source="tool_emit"
   → on success: complete_task
       - records planner_outcome
       - if payload.campaign_id → campaign_service.complete_campaign

6. Outcomes feed memory + learning
   - planner_outcomes table
   - planner_learning_stats updates intent/tool confidence in [-0.05, +0.05]
   - dashboard reasoning feed shows the entire decision chain via
     orchestration_traces
```

---

## 6. AI cognition layer (the chat pipeline)

This is the part that took the most iteration. The flow per chat turn:

```
user types question
       │
       ▼
[1] conversation_memory.resolve_follow_up
    - if "peki neden", "onu öne çıkar", "anlat", "açıkla", "bunu" detected
      AND the session has an active_entity_label
      → rewrite the question with the inherited entity
    - else: passthrough
       │
       ▼
[2] business_query_router.route(resolved_question)
    - keyword scoring across 13 retrieval intents
    - Turkish phonological prefix matching ("stoğu" matches "stok",
      "satılan" matches "satı")
    - returns retriever result OR None (for open-ended questions)
       │
       ▼ (None case fallback)
[2b] business_chat._build_open_ended_retrieval
     - assembles business_state + cross_event_reasoning + BI snapshot
     - calls narrative_synth.synthesize_narrative for a structural answer
       │
       ▼
[3] business_retrieval_service.<intent_fn>
    e.g. top_stock_product, sales_drop_diagnosis, sentiment_status
    - reads REAL data from fake_ai_api.db / listener.db
    - returns {intent, answer, data, recommendations, confidence}
    - empty data → explicit "yetersiz veri" answer, NEVER fabricates
       │
       ▼
[4] ai_synthesizer.synthesize
    - composes deliberation stages from actual retrieval data
      e.g. "Stok kayıtlarını taradım — Logitech G Pro X 130 adetle önde."
    - composes LLM prompt with:
      • the structured `data` block
      • intent codes (NOT canned suggestion strings — that was the
        previous template-leak; the LLM is told what each intent means
        but never given a finished sentence to copy)
      • conversation history (last 4 turns from chat_turns)
      • global FORBIDDEN_PHRASES list seeded with known template
        patterns ("küçük bir indirim kampanyası", "kısa bir trend
        analizi", "İstersen şunları hazırlayabilirim", …)
      • session-specific anti-phrase list from prior turns
    - calls OpenAI Chat Completions (gpt-4o-mini)
    - on any failure → falls back to retrieval.answer (deterministic)
       │
       ▼
[5] conversation_memory.record_turn
    - persists (question, resolved_question, intent, entity, answer)
    - extracts phrase signatures from the answer for future
      anti-repetition
    - updates session's active_entity_label + active_intent
       │
       ▼
Response shape returned to dashboard:
    {answer, stages[], routed_intent, active_entity, is_followup,
     follow_up_rationale, mode (llm|deterministic_fallback),
     latency_ms, anti_repetition_active, recommendations[], data{},
     session_id}
```

**Notable design decisions in this layer:**
- The deterministic baseline (`retrieval.answer`) is NOT sent to the LLM
  anymore — earlier versions did, and the LLM kept echoing the template
  phrasing. Now the LLM gets only structured data and is forced to
  compose prose itself.
- Stages are *data-aware* — they report what was found ("X 130 adetle
  önde") not what's being looked up ("stoğa bakıyorum…"). Massive
  perception change for the operator.
- Anti-repetition is global (forbidden seed) + session (extracted from
  prior turns). First-turn protection is critical because fresh sessions
  used to leak the most templates.
- Multi-turn continuity is via `chat_sessions` table keyed by
  `session_id` (dashboard stores in localStorage).

---

## 7. Lifecycle services (separate from the runtime core)

These are bounded subsystems that wrap orchestration in operator concepts.

### Calendar / scheduling (`scheduling_service.py`)
- `scheduled_entries` table — kind, channel, scheduled_at, recurrence
  (once/daily/weekly/monthly), status (pending/fired/cancelled/failed).
- Operator schedules content posts, campaigns, workflows via API. The
  workflow_worker fires due entries via `fire_due_schedules()` each poll.
- Recurring schedules auto-create the next occurrence on fire.

### Customer interaction (`customer_interaction_service.py`)
- `customer_threads` + `customer_messages` — channel, customer_ref,
  status (open/awaiting_human/escalated/resolved), sentiment.
- `draft_response()` produces a deterministic Turkish reply. Risky drafts
  (negative sentiment OR keywords like "iade", "yasal", "sosyal medya")
  automatically route through `approval_service` with risk_level='high'
  and flip the thread to `awaiting_human`.
- `escalate()` flips the thread to escalated and raises a second approval.

### Campaign lifecycle (`campaign_service.py`)
- `campaigns` + `campaign_metrics` — state machine
  `draft → scheduled → live → paused → completed → archived`.
- The autonomous planner automatically creates campaigns in `draft` for
  trigger intents (discount_promotion, growth_marketing, marketing_campaign).
- `_execute_autonomous_workflow` flips to `live` when the workflow fires.
- `complete_task` flips to `completed` when the AI task finishes.
- `campaign_performance_summary` derives CTR, conv rate, CPA, CPC,
  budget consumption %, duration minutes from snapshot aggregates.

### Social credentials (`social_credentials.py`)
- `social_credentials` table with Fernet-encrypted token blobs.
- Key from `APP_SECRET_KEY` env. Lazy-loaded, cached per key value.
- `save_credential` upserts and re-encrypts; `get_credential` decrypts.
- `list_credentials` and `revoke_credential` work without the key
  (operator can always inspect/revoke even after key rotation).
- `InstagramCampaignTool._run` consults the credential layer: if a
  credential exists, it logs `REAL_PUBLISH_WOULD_HAPPEN`, marks
  `publish_mode="real_publish_would_happen"`, includes the
  `account_handle` and `credential_id` in the tool output. **Does not
  yet make a real Graph API call** — that's behind a future
  `SOCIAL_PUBLISH_LIVE` flag in `tool_adapters/` (not built yet).

### Multi-tenant auth (`auth_service.py`)
- `orgs` + `org_members` (role: owner/admin/editor/viewer) + `api_keys`
  (sha-256 hashed, `aios_…` prefix, raw shown once).
- FastAPI dependency `get_current_auth` resolves either via `X-API-Key`
  header or `user_id` query fallback. Returns AuthContext with org_id.
- Critical endpoints (`/workflows`, `/tasks`, `/approvals`) honor the
  resolved org by widening the filter from single user to all org members.
- **Legacy user_id=1 mode is preserved** — only opt-in via key or query
  activates org filtering.

---

## 8. What is REAL vs FAKE vs STUB

| Capability | State | Notes |
|---|---|---|
| Orchestration runtime | **Real** | Production-shape, deterministic |
| Approval gate | **Real** | Single-level, gates external publish |
| Workflow engine | **Real** | State machine, idempotency, partial unique indexes |
| AI task lifecycle | **Real** | Retry with exponential backoff (`next_retry_at`) |
| Tool sandbox | **Real** | concurrent.futures timeout, circuit breaker |
| Observability traces | **Real** | DB-persisted, humanized summaries |
| Multi-turn chat | **Real** | OpenAI gpt-4o-mini, conversation memory, anti-repetition |
| Memory / learning | **Real (lightweight)** | Confidence drift in [-0.05, +0.05], no vector retrieval yet |
| Calendar / scheduling | **Real** | DB-backed, recurring presets, worker fires due |
| Customer thread state | **Real** | DB-backed, sentiment classifier, approval routing |
| Campaign lifecycle | **Real** | State machine, metrics aggregation, ratio derivations |
| Org / role / API keys | **Real** | SHA-256 keys, opt-in, encrypted-at-rest path |
| Encrypted credential store | **Real** | Fernet via cryptography |
| Commerce simulator | **Fake** | `fake_ai_api.db` is a mock e-commerce platform |
| All 7 tools | **Fake** | Produce mock output + emit timeline events; no real API calls |
| Instagram publish | **Stub** | Detects credential, logs intent, no real Graph API call |
| Trendyol / Shopify / WooCommerce / Amazon | **Not started** | Schema slot exists; no adapters |
| Real customer channels (DM/email/Q&A) | **Not started** | `customer_interaction_service` is in-process only |
| Vector retrieval | **Not started** | `embedding_placeholder` column exists; not populated |
| Real-time streaming (SSE / WebSocket) | **Not started** | Polling-based dashboard |
| Billing / cost tracking | **Not started** | Safety quotas exist but no per-call cost recording |
| Real auth on /api/internal/* | **Not started** | Test mode; documented as Phase E concern |

---

## 9. Known limitations (the honest list)

I'm aware of these. Reviewers should weigh how serious each is.

1. **SQLite write contention.** WAL helps reads but every write
   serializes. Comfortable at the current poll cadences (2s/5s/12s) but
   the ceiling is ~50 writes/sec. Migration path: Repository pattern +
   SQLAlchemy Core, swap engine to Postgres. Not done.

2. **Polling everywhere.** Listener 2s, workers 5s, dashboard panels
   7–22s. End-to-end latency floor ~7s best case. SSE/WebSocket would
   improve perceived realtime but adds complexity.

3. **CrewAI worker is single-threaded.** One AI task at a time.
   Acceptable today; will bottleneck at scale.

4. **No real auth on `/api/internal/*`.** Documented explicitly. The
   `social_credentials` endpoint comment warns about this. Phase E hasn't
   gated it yet because the focus has been on capabilities first.

5. **Single-level approval.** No escalation chain, no SLA, no delegation,
   no assignment-to-user. Real commerce needs legal accountability.

6. **Anti-repetition is advisory.** Passed to the LLM in the prompt; the
   model usually complies but isn't enforced. Larger models (gpt-4o)
   would tighten this at higher cost.

7. **Chat memory is single-session.** `chat_sessions` is per session_id;
   cross-session learning happens via `planner_memory` /
   `planner_outcomes` but those are for orchestration decisions, not for
   the chat operator's preferences.

8. **No real social publishing.** `InstagramCampaignTool` detects
   credentials and logs intent but does not call the real Graph API. Same
   for any other channel. Adapters need writing, plus a feature flag.

9. **No real e-commerce platform sync.** Products / orders / stock /
   reviews all come from `fake_data_generator` or operator import. No
   webhook receivers; no Shopify/Trendyol/WooCommerce sync workers.

10. **No billing / cost instrumentation.** Safety service enforces
    hourly/daily autonomous quotas (30/h, 15/d) but doesn't track AI
    cost per task, per user, per org. A bad retry loop could burn OpenAI
    credit silently.

11. **Two parallel ontology maps remain.** `event_router` prefixes,
    `semantic_parser.KNOWN_EVENTS`, `rule_manager.KNOWN_WORKFLOWS` — the
    business `ontology.py` is consolidated, but the *event* vocab is
    still in three places. Adding a new event type requires three edits.

12. **No FK constraints declared.** `PRAGMA foreign_keys=ON` is set, but
    the schema doesn't declare REFERENCES anywhere. Orphan rows possible.
    The audit flagged this as AR-9 and it's still open.

13. **`ai_planner.py` is dead under default config** (`CRITICAL_FALLBACK=0`).
    Marked for deletion; still present.

14. **Tools are bound to fake_ai_api.db.** When real adapters land, the
    tool layer needs a credential-aware execution context. Today
    `_resolve_user_id()` exists on the mixin but isn't fully threaded.

15. **Conversation memory phrase signatures are heuristic.** Token-based,
    not embedding-based. Works for catching opener tics but won't catch
    semantically equivalent rephrasing.

---

## 10. Specific questions for the external AI reviewer

Pick any subset.

### Architecture / topology
1. The 4-process modular monolith with two SQLite DBs — is this still
   the right shape at the next 10x of usage, or should I migrate the
   workflow_worker and crewai_worker to a real queue (Redis Streams)
   before adding more capabilities?
2. The `internal_service.py` pattern (in-process function calls instead
   of self-HTTP) eliminated a real deadlock class. Are there other
   self-HTTP-loop antipatterns I should be looking for?
3. Two SQLite DBs (fake_ai_api.db + listener.db) with no cross-DB joins
   — was splitting the storage a good idea or should they consolidate?

### AI cognition
4. The chat pipeline (router → retrieval → synthesizer with explicit
   anti-template constraints) gets natural Turkish but it costs ~5–10s
   per turn on gpt-4o-mini. Is there a leaner LLM placement that
   preserves the "data-grounded" property without that latency?
5. Anti-repetition is advisory in the prompt. Is there a more reliable
   technique — maybe constrained decoding, response post-filtering,
   or a small reranker that rejects responses containing forbidden
   phrases and re-asks?
6. `conversation_memory.resolve_follow_up` uses keyword heuristics
   ("peki neden?", "onu öne çıkar", "anlat"). When should I move to a
   coreference / pronoun resolver instead, and is there a way that
   doesn't add another LLM call per turn?
7. The deliberation-stages animation is deterministic-from-data and
   bounded ~600ms. Some users find it adds value; others might find it
   "fake". What's the right way to evaluate this UX choice?

### Multi-tenant / security
8. Test-mode (`user_id=1` default, no auth) is convenient for the dev
   loop but means `/api/internal/*` is wide open. What's the lowest-cost
   shift to "real auth" — middleware that requires a key in production
   but passes through with a warning in dev?
9. API keys are SHA-256, raw-once. Reviewers: is there a clear
   weakness in this design beyond the obvious "no rotation flow"?
10. Org membership is single-list (a user can be in many orgs, but
    there's no concept of "active org" per session). Is that a real
    problem before I add a switcher, or is the "primary org = oldest
    membership" rule acceptable?

### Lifecycle composition
11. Campaign lifecycle is wired in three places (planner attaches,
    workflow worker launches, task completes). That's three boundaries
    where an inconsistency could leave a campaign stuck in `live`. Is
    there a cleaner orchestration pattern, or is the idempotency at each
    boundary sufficient?
12. `customer_interaction_service` is in-process only — no live channel
    sync. When I add `channel_adapters/`, what's the right pull cadence
    + concurrency model that doesn't violate the "deterministic
    orchestration" invariant?

### What I'm worried about that I haven't named
13. What am I underestimating? What is the system going to break on at
    100x today's event rate that I haven't called out above?
14. Where is the architecture making it *harder* to add a real feature
    that I think should be easy?
15. Is there a class of bug or risk that this design pattern
    historically produces, that I should be defending against now?

---

## 11. Compact module reference (most important files)

For reviewers who want to read the actual code instead of trusting the
summary:

| File | Purpose | LoC |
|---|---|---|
| `main.py` | FastAPI app + fake commerce platform | 2071 |
| `orchestration_api.py` | Dashboard/management API surface | ~750 |
| `autonomous_planner.py` | AI planner (heuristic + LLM paths) | ~530 |
| `workflow_service.py` | Workflow state machine + dispatch | ~480 |
| `db.py` | All listener.db schema + connection pool | ~580 |
| `business_chat.py` | Orchestrator for chat turns | ~200 |
| `business_retrieval_service.py` | 15 typed retrievers | ~600 |
| `business_query_router.py` | Intent detection + entity resolve | ~250 |
| `conversation_memory.py` | Session state + follow-up rewrite | ~330 |
| `ai_synthesizer.py` | LLM prompt + stages | ~380 |
| `observability.py` | Dual-write traces + humanization | ~330 |
| `auth_service.py` | Orgs / members / API keys | ~480 |
| `social_credentials.py` | Fernet credential vault | ~340 |
| `campaign_service.py` | Campaign state machine + metrics | ~430 |
| `scheduling_service.py` | Operator calendar | ~430 |
| `customer_interaction_service.py` | Threaded customer AI replies | ~470 |
| `internal_service.py` | In-process replacement for self-HTTP | ~420 |
| `event_envelope.py` | Typed event view | ~210 |
| `plan_validator.py` | Pydantic schema for AI planner output | ~250 |
| `index.html` | Single-page dashboard (vanilla JS + Tailwind) | ~840 |

---

*End of review document. Please tell me what's wrong with this.*
