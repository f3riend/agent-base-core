# FRONTEND_REBUILD_PLAN.md

> Frontend evolution plan for the AI Operations Center.
>
> Sibling docs: `PROJECT_AUDIT.md`, `ARCHITECTURE_EVOLUTION_PLAN.md`.
>
> The current `index.html` (39 KB, ~840 lines) is the v2 dashboard rebuilt
> in the previous turn — single-page vanilla-JS app, Tailwind CDN, three
> columns, class-wide focus protection, hash-diff partial renders. This plan
> documents what v2 already does well and lays out v3: the next sections
> (calendar, customer interactions, campaign center, product intelligence)
> and the structural changes they need.

---

## 0. Principles

1. **No build step.** Vanilla JS + Tailwind CDN + Inter font. The dashboard is one HTML file. Justification: this codebase has no JS toolchain, the data is local-network, and adding webpack/vite would not pay off until we have >10 contributors.
2. **Class-wide focus protection.** Every refresh path checks `App._canRefresh()` which is `false` while any text input/textarea/contenteditable is focused (and for 1.2s after blur). This kills typing-reset class-wide (CB-16). All new panels follow.
3. **Hash-diff partial renders.** Each panel computes a JSON hash of its data; `_setIfChanged()` only invokes the render when the hash changed. No full-page rerenders.
4. **Humanization layer.** Raw technical labels (`plan_invalid`, `workflow_name`, `processed_by_rule_engine`, `synthetic_skip`) are translated through `humanizeIntent` / `humanizeWorkflow` / `humanTraceTag` before display.
5. **One source of polling truth.** `App._startPolling()` owns the cadence table. New panels register their cadence there.
6. **Component-ish without a framework.** Each panel has a `refreshX()` + `_renderX()` pair. State lives in `App.state`. DOM updates go through `_setIfChanged()`. No virtual DOM, no observables; just discipline.
7. **Operator tone everywhere.** No raw IDs in primary copy. No JSON in primary copy. Source data lives behind a `<details>` accordion ("kaynak veriyi göster").

---

## 1. Current State (v2, shipped previous turn)

```
┌─────────────────────────────────────────────────────────────────────┐
│  Header — title + live dot + operational pressure pill + Yenile/Seed │
├─────────────────────────────────────────────────────────────────────┤
│  KPI strip (6 cards): satış birimi, stok sağlığı, aktif iş akışı,    │
│                       bekleyen onay, trend ürün, dead-letter        │
├──────────────────┬─────────────────────────┬──────────────────────┤
│  AI Operatörü    │  İş Sinyalleri          │  AI Düşünce Akışı     │
│  (chat panel)    │  (insight cards)        │  (reasoning feed)     │
│                  │                         │                       │
│  Kural Editörü   │  Operasyonel Zaman      │  İş Akışı Merkezi     │
│                  │  Tüneli (humanized)     │   + Onay kuyruğu      │
│                  │                         │                       │
│                  │                         │  AI Hafızası          │
└──────────────────┴─────────────────────────┴──────────────────────┘
```

**What's shipped:**
- Six KPI cards driven by `/dashboard-metrics` + `/orchestration-health`.
- AI Operator chat with rich response bubbles — markdown-ish renderer (`**bold**`, `` `code` ``, line breaks), recommendation chips, source-data accordion.
- Quick-ask suggestions (7 preset prompts) — one click sends.
- Insight cards: severity bar, confidence %, recommended action, evidence.
- Humanized timeline: emoji icon + tone class + Turkish description.
- AI reasoning feed: live from `/api/internal/traces`, tag-colored chips.
- Workflow center: status chips (planlandı/çalışıyor/tamamlandı/iptal).
- Approval queue: one-click approve/reject, risk chip, confidence %.
- AI memory patterns: per-intent learned trends.
- Rule/strategy editor: NL → DSL preview + apply.

**Polling cadences:**
- KPI 12 s · Insights 18 s · Timeline 9 s · Reasoning 7 s · Workflows 11 s · Approvals 11 s · Memory 22 s · Pressure 14 s · Chat NEVER auto-refreshes.

---

## 2. v3 Target Layout (next iteration)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Header (compact) — title, live, pressure, org switcher, Yenile         │
├─────────────────────────────────────────────────────────────────────────┤
│  KPI strip                                                               │
├───────────┬──────────────────────────────┬───────────────────────────┤
│           │                              │                           │
│  Left     │  Main column                 │  Right rail               │
│  rail     │  (tabbed area)               │                           │
│           │                              │                           │
│  AI       │  ┌──────────────────────┐    │  AI Düşünce Akışı         │
│  Chat     │  │ tabs:                │    │                           │
│           │  │  Operatör · Takvim · │    │  İş Akışı Merkezi         │
│           │  │  Kampanya · Müşteri  │    │   + Onay kuyruğu          │
│  Kural    │  │  Ürün Zekası         │    │                           │
│  editor   │  └──────────────────────┘    │  AI Hafızası              │
│           │  (active tab content)        │                           │
└───────────┴──────────────────────────────┴───────────────────────────┘
```

Why tabs in the main column instead of more panels stacked vertically:
- The 12 capabilities don't all fit on one screen with adequate breathing room.
- Each tab is its own context; an operator focused on calendar shouldn't see workflow churn in their peripheral vision.
- Tabs preserve focus state per tab (input + scroll restored on return).
- Adding a tab is one new render function + one nav entry.

---

## 3. New Tabs / Panels for v3

### 3.1 Tab: Operatör (default)
Today's middle column (insight cards + humanized timeline) becomes the default tab. Operator-tone homepage.

### 3.2 Tab: Takvim (NEW)

Backend: `/api/internal/calendar?start=…&end=…` shipped in this turn's `scheduling_service.py`.

UI:
- Month-grid view (7 columns × 5/6 rows). Each cell shows day number + up to 3 entry chips colored by `kind` (content / campaign / workflow / recurring).
- Click a day → side drawer with the day's full schedule: time, kind chip, description, edit/cancel buttons.
- "Yeni planla" button → modal with form: kind, channel (Instagram/Banner/SSS/...), datetime picker, recurrence (`once`, `daily`, `weekly`, `monthly`).
- Drag-to-reschedule (Phase 2 of this tab; Phase 1 = click+pick).
- Empty days show subtle "yeni planla" hint on hover.

Rendering rule: month grid is a single HTML table; only changed cells re-render via `_setIfChanged` keyed by day_id+entry_count+hash(entries).

### 3.3 Tab: Kampanya (deferred to Phase C of evolution plan)

Stub for now — placeholder card explaining "Campaign lifecycle launching next: draft → live → archived states with per-campaign metrics."

When backend `campaign_service` lands:
- Active campaigns list (status chip, channel, scheduled_at, started_at)
- Per-campaign mini-trend: impressions/clicks line chart (CSS gradient sparkline; no chart library)
- Detail drawer: state-machine controls (pause / resume / archive), workflow lineage, attached approvals.

### 3.4 Tab: Müşteri (NEW)

Backend: `/api/internal/customer-threads` shipped in this turn's `customer_interaction_service.py`.

UI:
- Thread list (left of tab pane): channel chip, customer ref, last message preview, sentiment dot, status (açık / yanıt bekliyor / kapandı).
- Filter chips: tüm / açık / eskalasyon / kapandı.
- Click thread → main pane shows message history (customer bubbles vs. AI draft bubbles vs. operator-sent bubbles) + draft response card with approve/edit/send/escalate actions.
- Escalation creates an approval; the queue on the right rail shows it.
- New-message ingestion is via the backend (real channel sync is a Phase D dependency).

### 3.5 Tab: Ürün Zekası (NEW)

Backend: existing — `/api/internal/items`, `/trending-products`, `/business-insights`.

UI:
- Product grid: thumbnail, name, store, sales chip, stock chip (low_stock_count colored), trend chip if in trending list.
- Click product → drawer with: per-product timeline filter, attached workflows, BI signals for this product.
- "Stok riski" filter chip surfaces low-stock products with sales > 0 (high urgency).
- Search box stays focused thanks to class-wide focus guard.

---

## 4. Sectional Specifications

Each new section follows the same contract:

```js
App.refreshX();    // fetches, hashes, conditionally renders
App._renderX();    // pure DOM update from data
```

Polling: register in `_startPolling()`. Chat-style panels NEVER auto-refresh.

Empty-state copy: each panel must have a Turkish empty-state line explaining what would populate it. No empty grids.

---

## 5. Design Tokens

Centralized in the `<style>` block of `index.html`. Adding a new panel reuses these:

| Token | Value | Use |
|---|---|---|
| `--bg` | `#f6f7fb` | Page background |
| `--panel` | `#ffffff` | Cards, drawers, modals |
| `--line` | `#e8eaf0` | Borders |
| `--ink` | `#0f172a` | Primary text |
| `--muted` | `#6b7280` | Secondary text |
| `--soft` | `#f3f4f7` | Inset surfaces (chat log, code blocks) |
| `--accent` | `#4f46e5` | Primary action (Sor, Uygula, send-button states) |
| `--accent-soft` | `#eef2ff` | Hover/active background for accent items |
| `--ai` | `#6366f1` | "AI-emitted" chips, ai tone in timeline |
| `--good` | `#16a34a` | success, positive sentiment |
| `--warn` | `#ca8a04` | warning, medium severity |
| `--bad` | `#dc2626` | failure, high severity, negative sentiment |

Chips: `.chip`, `.chip-ai`, `.chip-good`, `.chip-warn`, `.chip-bad`, `.chip-neutral`.

Severity bars: `.sev-high`, `.sev-medium`, `.sev-low`.

Tone classes (timeline): `.tone-positive`, `.tone-negative`, `.tone-warning`, `.tone-ai`, `.tone-neutral`.

---

## 6. State / Routing Contract

`App.state` is the only source of truth:

```js
App.state = {
  kpi, insightCards, humanizedTimeline,
  reasoning, workflows, approvals, memory,
  chat, pressure,
  // v3 additions:
  calendar,         // { window: {start, end}, entries: [...] }
  customerThreads,  // { items: [...], focusedThreadId }
  campaigns,        // (Phase C)
  products,         // (Ürün Zekası)
  activeTab,        // "operator" | "calendar" | "campaign" | "customer" | "product"
};
```

Tab switching is a state change → re-render of main column only. The left rail (chat + rule editor) and right rail (reasoning + workflows + memory) persist across tabs — they're the operator's permanent context.

---

## 7. Networking Contract

Every fetch goes through `App._get(path)` / `App._post(path, body)`. Both prefix with `/api/internal`. No external API base. No third-party hosts in network calls. Loop guard (see `internal_service.assert_not_internal_http_loop`) operates on the backend; the frontend never calls 127.0.0.1 directly.

Errors are caught locally and rendered into the panel's own empty/error state — never a global alert.

---

## 8. Focus Protection Audit

Class-wide focus guard is `document.addEventListener("focusin"/"focusout")`. Refresh paths consult `App._canRefresh()` which short-circuits while focused AND for 1.2s after blur.

**Mandatory rules for every new render fn:**
1. Call `if (!this._canRefresh()) return` before fetching.
2. Use `_setIfChanged(key, value, renderFn)` so unchanged data does not touch the DOM.
3. Never set `innerHTML` on a container that has descendants currently focused — `_canRefresh` prevents this transitively, but the rule is the policy.
4. Modals/drawers must restore focus on close.

---

## 9. Accessibility (lightweight)

- Every interactive element has visible text — no icon-only buttons in primary actions.
- Focus rings preserved (Tailwind defaults; do not blanket-disable).
- Color is reinforced by text label (severity = "yüksek" + red bar; status = "tamamlandı" + green chip).
- `prefers-reduced-motion` respected — animations (`.fade-in`, `.live-dot`) get `@media (prefers-reduced-motion: reduce) { animation: none; }`.
- Tab navigation works without mouse: arrow keys move between tab buttons.

---

## 10. v3 Implementation Order

Aligned with the evolution plan's Phase A/B (this turn) and Phase C/D/E (later turns).

1. **Calendar tab** (this turn / next turn): consume `/api/internal/calendar`, `/schedules`. Month grid, side drawer, create modal.
2. **Customer tab** (this turn / next turn): consume `/api/internal/customer-threads`. Thread list, message panel, draft action.
3. **Ürün Zekası tab** (next turn): consume existing `/items`, add per-product drawer.
4. **Kampanya tab** (Phase C of evolution): wait for `campaign_service`.
5. **Org switcher in header** (Phase E of evolution).
6. **Drag-to-reschedule calendar** (later, optional).

---

## 11. Anti-Patterns to Avoid

- **Polling timer per panel firing on different cadences mixing into one DOM update batch.** → Each panel has its own `setIfChanged`; they don't share render fns.
- **`innerHTML += '<…>'` inside a render loop.** → Build the string once, set once.
- **Inline event handlers without `escapeHtml` on user-derived strings.** → `escapeHtml` + `escapeQuote` shipped; use them.
- **Reading `App.state` inside an `await`.** → Hash-diff happens before state mutation, so state and DOM stay consistent.
- **Reactive frameworks.** → Stay on vanilla until something forces React.
- **Toast/alert popups for every backend response.** → Use inline error state in the panel.
- **Spinner cascades.** → Empty/skeleton states only at first load; subsequent loads update silently.

---

## 12. Migration Path to a Framework (if forced)

If the operator-experience surface grows past 12 tabs or a non-developer needs to extend it:
- React + Vite + Tailwind, identical token palette, identical state shape, identical fetch contract.
- Backend stays exactly the same (already JSON-API-only).
- Each existing panel becomes a component; existing render functions translate ~1:1.

But: no framework migration until forced. The current architecture handles the planned 12 sections.

---

*End of frontend plan.*
