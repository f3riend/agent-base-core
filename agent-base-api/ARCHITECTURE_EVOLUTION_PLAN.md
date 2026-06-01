# ARCHITECTURE_EVOLUTION_PLAN.md

> Forward-looking plan for evolving the runtime into a multi-tenant AI-native
> commerce/social operations platform.
>
> Sibling docs:
> - `PROJECT_AUDIT.md` — what exists today, what's broken, what changed in Rev 1/2/3.
> - `ARCHITECTURE_CLEANUP_PLAN.md` — phased stabilization plan (Phases 0–12), already largely shipped.
> - `FRONTEND_REBUILD_PLAN.md` — operator-facing UX evolution.
>
> This plan covers what's NOT in the cleanup plan: net-new capabilities,
> infrastructure decisions, and the sequencing that gets the platform to a
> real commerce/social runtime without rewriting what already works.

---

## 0. Invariants (do not break)

The runtime has earned these properties. Every evolution step must preserve them.

1. **AI proposes, runtime executes safely.** AI never bypasses the workflow engine, approval gates, safety quotas, or persistence. New capabilities add to this seam, never around it.
2. **Deterministic core.** Routing, validation, idempotency, and replay are deterministic. Non-determinism (LLM output, wall-clock month) is fenced behind validation/replay-safe accessors. See CB-11 fix.
3. **Event-sourced timeline.** Every business effect produces a timeline event. Tools emit through `fake_tool_timeline` / `internal_service`. Replay reconstructs.
4. **Modular monolith.** One Python process, one repo, three workers (listener, workflow_worker, crewai_worker), one API server. No microservices yet. No Kubernetes yet.
5. **No in-process self-HTTP.** `internal_service.assert_not_internal_http_loop` is enforced. Future code paths follow the same pattern.
6. **Observability is mandatory.** Every orchestration decision writes to `orchestration_traces` (DB) and stdout. The dashboard reads from the table, not from stdout.
7. **Multi-tenant by column.** Every new table carries `user_id` (and, when Phase E lands, `org_id`). No silent default-1 fallbacks at the deep call sites.

---

## 1. Capability Roadmap (functional)

Letters indicate sequence: A → G. Each unlocks the next. Earlier letters are more foundational and less risky.

### Phase A — Calendar / scheduling center (NEW, this turn)

The platform is also an operational planning tool. Today's `workflow_instances.scheduled_at` is an internal field; the operator has no calendar.

**New module:** `scheduling_service.py`

```python
schedule_content_post(user_id, *, channel, when, payload, ...) -> ScheduleEntry
schedule_campaign(user_id, *, campaign_id, when, ...) -> ScheduleEntry
schedule_recurring(user_id, *, kind, rrule, payload) -> ScheduleEntry
list_calendar_window(user_id, *, start, end) -> list[CalendarEntry]
move_schedule(schedule_id, new_when) -> ScheduleEntry
cancel_schedule(schedule_id, reason) -> bool
```

**New table:** `scheduled_entries` — `id, user_id, kind, channel, scheduled_at, payload_json, recurrence_rule, status (pending|fired|cancelled|failed), created_at, fired_at, workflow_id NULL`.

**Worker change:** `workflow_worker` (or a new `scheduler_worker`) polls `scheduled_entries WHERE scheduled_at <= utcnow AND status='pending'` and creates the matching workflow via `workflow_service.create_workflow`.

**Endpoints:**
- `GET /api/internal/calendar?start=…&end=…` → calendar grid (one entry per day with summary).
- `GET /api/internal/schedules?status=pending&limit=…` → list.
- `POST /api/internal/schedules` → create.
- `PATCH /api/internal/schedules/{id}` → reschedule.
- `DELETE /api/internal/schedules/{id}` → cancel.

**Why first:** Calendar UI unlocks campaigns and content-publishing UX. Built on existing scheduling primitives — no new infrastructure.

### Phase B — Customer Interaction Center (NEW, this turn)

`SupportResponseTool` drafts but no thread state. Fix: model the **thread**, not the single message.

**New module:** `customer_interaction_service.py`

```python
open_thread(user_id, *, channel, customer_ref, initial_message) -> Thread
ingest_message(thread_id, *, message, from_role) -> ThreadMessage
draft_response(thread_id) -> DraftResponse  # routes through approval if risky
escalate(thread_id, *, reason, level="manager") -> Thread  # creates approval
list_threads(user_id, *, status="open", limit=…)
mark_resolved(thread_id, reason="resolved")
```

**New tables:**
- `customer_threads` — id, user_id, channel (instagram_dm|email|trendyol_qa|chat|inline), customer_ref, status (open|awaiting_human|resolved|escalated), sentiment_score, opened_at, closed_at, last_message_at.
- `customer_messages` — id, thread_id, from_role (customer|ai_draft|operator|system), text, sentiment, created_at, approval_id NULL.

**Approval reuse:** Risky drafts (high-confidence-negative sentiment, refund mention, legal keywords) route through `approval_service` with risk_level="high".

**Endpoints:** `GET /api/internal/customer-threads`, `POST /customer-threads`, `POST /customer-threads/{id}/draft`, `POST /customer-threads/{id}/escalate`, `POST /customer-threads/{id}/messages`.

**Why second:** Tightly composes with approval center + sentiment BI. Visible value: operator sees "3 müşteri eskaleyi bekliyor".

### Phase C — Campaign lifecycle service

**New module:** `campaign_service.py` — `Campaign` entity with state machine (`draft → scheduled → live → paused → completed → archived`).

**New tables:** `campaigns` (id, user_id, name, channel, intent, status, scheduled_at, started_at, ended_at, budget, metadata_json), `campaign_metrics` (campaign_id, ts, impressions, clicks, conversions, spend).

**Integration:**
- Autonomous planner's `discount_promotion`/`growth_marketing` workflows attach a `campaign_id` to their metadata.
- Workflow worker reads it; tool emits update campaign metrics.
- BI emits `campaign_performance` signals tied to specific campaigns instead of generic `engagement_spike`.

### Phase D — Social-auth + credential store

**New module:** `social_credentials.py` — encrypted token storage (`cryptography.Fernet`, key from `APP_SECRET_KEY` env).

**New table:** `social_credentials` — id, user_id, provider, account_handle, encrypted_token_blob, scope, expires_at, refreshed_at, status.

**OAuth stubs:** `social_oauth/instagram.py`, `social_oauth/facebook.py` — interface ready, but real flows behind a feature flag (`SOCIAL_OAUTH_ENABLED=0` by default).

**Tool wiring:** `InstagramCampaignTool` becomes credential-aware. If a real credential is present → POST to the real Graph API; else → draft-only (current behavior).

### Phase E — Org / role / multi-tenant hardening

**New tables:** `orgs`, `org_members(org_id, user_id, role: owner|admin|editor|viewer)`, `api_keys(id, org_id, hashed_key, scope, last_used_at)`.

**Schema migration:** every row gets `org_id`. Existing `user_id` columns stay; a user belongs to exactly one default org until the UI surfaces switching.

**Request middleware:** authenticate → resolve `org_id` + role → inject into a context var that every service reads. Endpoints reject if the request's `user_id` doesn't belong to the resolved `org_id`.

**Secret encryption:** the same Fernet key encrypts credentials in Phase D AND any future API keys, webhook secrets, etc.

### Phase F — Real e-commerce platform sync

**New package:** `platform_connectors/`
- `shopify.py`, `woocommerce.py`, `trendyol.py`, `amazon.py` — each implementing a common `PlatformConnector` protocol (`sync_products`, `sync_orders`, `handle_webhook`).
- Real adapters live behind a feature flag (`PLATFORM_INTEGRATIONS_ENABLED=0`).

**New tables:** `product_sync_log`, `external_orders` (mapped to internal orders by external_id).

**Webhook receivers:** `/webhooks/shopify`, `/webhooks/woocommerce` — verify signature, enqueue ingestion event.

### Phase G — Billing / cost instrumentation

**New module:** `billing_service.py`
- Records per-AI-task cost (`billing_events` table).
- Quota-aware safety service (`safety_service` calls into billing for "is org over its spend cap?").
- Stripe stub (`payment_integration.py`) — no real charges in test mode.

---

## 2. Infrastructure Roadmap (non-functional)

Sequenced AFTER functional Phase A–G so we don't rebuild on shifting foundations.

### Infra-1 — Distributed-ready abstractions

Without leaving SQLite, introduce queue/stream abstractions so future Redis/Kafka swap is a 1-file change.

- `queue.py` — `Queue` protocol (`push`, `pop`, `ack`, `nack`). Default impl = SQLite table; future = Redis Streams.
- `bus.py` — pub/sub for cross-process notifications (today: polling; future: Redis pub/sub).

Workers consume via the protocol. No code outside `queue.py`/`bus.py` imports `redis` or `kafka`.

### Infra-2 — Postgres migration path

The single SQLite ceiling will bite around 50–100 concurrent writes/sec. When it does:

- Make every `db.py` call go through a thin `Repository` per table.
- Repositories accept an `engine` arg (SQLAlchemy Core, no ORM bloat).
- Switching from `sqlite+aiosqlite:///listener.db` to `postgresql+asyncpg://…` is a config change.
- This is a refactor, not a feature; not until usage forces it.

### Infra-3 — Worker scale-out

Today three workers, one process each. Scale-out requires:

- Worker identity (each worker registers itself, takes a lease on rows it processes).
- Heartbeat table + lease expiry (recover crashed workers).
- Optional: replace the SQLite-polling pattern with a real queue (Infra-1).

### Infra-4 — Observability backends

Today `orchestration_traces` is a SQLite table. When scale demands:
- Mirror writes to a `traces.ndjson` log (cheap).
- Optional: ship to OpenTelemetry / Honeycomb / Grafana Loki via an exporter.
- No rewrite of call sites — `observability._emit` is the seam.

---

## 3. Consolidation Work (technical debt)

Tracked here so it doesn't fall off after the platform features land.

| Item | What | When |
|---|---|---|
| **Workflow assembler** | One adapter that converts (rule, plan) → workflow metadata; used by both `action_engine` and `planner_runtime` | After Phase B |
| **Event ontology module** | Single source for event prefixes + KNOWN_EVENTS + KNOWN_WORKFLOWS (today: 3 hardcoded maps) | After Phase C |
| **Business-state cache** | 30s TTL on `build_business_state`, invalidated on timeline write | After Infra-3 |
| **Delete `ai_planner.py`** | Dead unless `CRITICAL_FALLBACK=1`; documented | Before Phase E |
| **Delete `rule_manager._extract_workflow`** | Regex helper for an unreachable code path | Before Phase E |
| **FK constraints** | `db.py` declares the schema; adding REFERENCES is a one-line-per-table win | During Phase E (touches every table) |

---

## 4. Sequencing — Recommended Order

Foundation first, externality-facing last so we don't expose a half-built system.

1. **Phase A** — Calendar (this turn).
2. **Phase B** — Customer interaction center (this turn).
3. Frontend integration of A + B (next turn).
4. **Phase C** — Campaign lifecycle.
5. **Phase E** — Org/role hardening. (Brought forward of Phase D — multi-tenant correctness must precede real social-auth or we leak credentials.)
6. **Phase G** — Billing instrumentation.
7. **Phase D** — Social-auth + credentials.
8. **Phase F** — Real platform connectors.
9. **Infra-1 → Infra-4** as load demands.

Rationale:
- A + B unlock visible operator UX without external dependencies.
- C cleans up the campaign mess that today lives in workflow metadata.
- E + G must precede D + F so we don't ship real integrations on top of leaky multi-tenancy.

---

## 5. What This Plan Explicitly Will NOT Do

- **No microservices.** The product is a modular monolith.
- **No Kubernetes.** Single VM / single container is the deployment target until usage forces otherwise.
- **No real ML model training.** AI is API-calls + deterministic logic. Learning stays as confidence drift (±0.05).
- **No replacement of SQLite** until concurrent write rate forces it.
- **No vector DB** — `recall_similar_campaigns` stays SQL `LIKE`+heuristic; embedding column reserved for the future.
- **No replacement of CrewAI** — CrewAI is the execution intelligence layer, not the orchestration owner. The orchestration is ours.
- **No giant frontend framework** — vanilla JS, Tailwind CDN, partial-render pattern as documented in `FRONTEND_REBUILD_PLAN.md`. Adding React/Vue would not justify the build pipeline cost at this scale.

---

## 6. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| SQLite write contention at moderate scale | Medium | High | Composite indexes shipped (Phase 1); migrate to Postgres via Repository pattern when needed |
| AI cost runaway (retries, failed plans) | Medium | High | Phase G billing instrumentation; tool circuit breaker shipped (CB-1) |
| Real social publish without proper approval thread | High if Phase D ships before B/E | Critical | Sequence: E + G + thread UI BEFORE real publish enabled |
| Multi-tenant data leak (CB-4 was just one example) | Medium | Critical | Phase E hardening; per-endpoint `org_id` filter audit; integration tests |
| Approval SLA missing → operator misses a critical review | Medium | High | Approval extensions (assigned_to, due_at) in Phase B side-effect |
| Customer-facing message produced by AI but sent unreviewed | Low (gated today) | Critical | Approval routing in Phase B; legal review keyword list |

---

## 7. Open Questions for the User

When ready to discuss:

- **Org model** — single workspace per user, or multi-workspace? Affects Phase E schema shape.
- **Billing model** — usage-based per AI call, or tiered? Affects Phase G.
- **Primary social channel target** — Instagram only, or IG+FB+TikTok? Affects Phase D investment level.
- **Real-vs-fake toggle** — environment variable per provider, or a per-org capability flag? Affects Phase D + F shape.

---

*End of evolution plan.*
