"""
Business intent interpreter for natural language → rule DSL.

Evolves beyond template matching via semantic ontology, inference, and confidence scoring.
Does NOT execute workflows or bypass the rule DSL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Known platform vocabulary (extended for business intents)
# ---------------------------------------------------------------------------

KNOWN_EVENTS = {
    "store.created",
    "store.rejected",
    "store.updated",
    "store.deleted",
    "product.created",
    "stock.updated",
    "order.created",
    "order.updated",
    "banner.created",
    "banner.updated",
}

KNOWN_WORKFLOWS = {
    "welcome_instagram_post",
    "low_stock_alert",
    "owner_notification",
    "support_escalation",
    "apology_coupon",
    "marketing_campaign",
    "pause_campaigns",
    "inventory_alert",
    "sales_drop_alert",
    "banner_performance_alert",
}

CONFIDENCE_APPLY_THRESHOLD = 0.55
CONFIDENCE_GENERATE_THRESHOLD = 0.38


@dataclass
class SemanticParseResult:
    rules: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    ambiguities: list[str] = field(default_factory=list)
    missing_thresholds: list[str] = field(default_factory=list)
    requires_confirmation: bool = False
    intents_detected: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "confidence": round(self.confidence, 3),
            "ambiguities": self.ambiguities,
            "missing_thresholds": self.missing_thresholds,
            "requires_confirmation": self.requires_confirmation,
            "intents_detected": self.intents_detected,
            "message": self.message,
            "rule_count": len(self.rules),
        }


# ---------------------------------------------------------------------------
# Intent ontology: keywords → inferred automation
# ---------------------------------------------------------------------------

INTENT_ONTOLOGY: list[dict[str, Any]] = [
    {
        "intent_id": "low_stock",
        "keywords": [
            r"low\s+stock",
            r"stock\s+is\s+low",
            r"inventory\s+low",
            r"out\s+of\s+stock",
            r"alert\s+inventory",
            r"inventory\s+alert",
            r"stock\s+alert",
            r"replenish",
        ],
        "event": "stock.updated",
        "workflow": "low_stock_alert",
        "rule_name": "low_stock_alert",
        "conditions": [{"field": "item.stock", "operator": "<", "value": 10}],
        "entity": "item",
        "base_confidence": 0.82,
    },
    {
        "intent_id": "high_sales",
        "keywords": [
            r"high\s+sales",
            r"sales\s+spike",
            r"sales\s+increase",
            r"trend(?:s|ing)?\s+upward",
            r"product\s+trend",
            r"best\s+seller",
        ],
        "event": "stock.updated",
        "workflow": "marketing_campaign",
        "rule_name": "product_trend_marketing",
        "conditions": [{"field": "item.sales", "operator": ">", "value": 50}],
        "entity": "item",
        "base_confidence": 0.72,
    },
    {
        "intent_id": "sales_drop",
        "keywords": [
            r"sales\s+(?:suddenly\s+)?drop",
            r"sales\s+decline",
            r"sales\s+fall",
            r"sales\s+decrease",
            r"notify\s+(?:the\s+)?owner",
            r"revenue\s+drop",
        ],
        "event": "order.created",
        "workflow": "sales_drop_alert",
        "rule_name": "sales_drop_notify",
        "conditions": [{"field": "item.sales", "operator": "<", "value": 5}],
        "entity": "item",
        "base_confidence": 0.68,
        "threshold_fields": ["item.sales"],
    },
    {
        "intent_id": "negative_review",
        "keywords": [
            r"negative\s+review",
            r"bad\s+review",
            r"poor\s+review",
            r"review\s+is\s+negative",
            r"support\s+workflow",
            r"support\s+escalat",
            r"customer\s+complaint",
        ],
        "event": "store.updated",
        "workflow": "support_escalation",
        "rule_name": "negative_review_support",
        "conditions": [],
        "entity": "store",
        "base_confidence": 0.7,
        "ambiguity_note": "No dedicated review event; mapped to store.updated",
    },
    {
        "intent_id": "delayed_order",
        "keywords": [
            r"order\s+(?:is\s+)?delayed",
            r"delayed\s+shipping",
            r"shipping\s+delay",
            r"late\s+delivery",
            r"apology\s+coupon",
            r"send\s+coupon",
        ],
        "event": "order.created",
        "workflow": "apology_coupon",
        "rule_name": "delayed_order_coupon",
        "conditions": [],
        "entity": "order",
        "base_confidence": 0.74,
    },
    {
        "intent_id": "banner_performance",
        "keywords": [
            r"banner\s+performance",
            r"campaign\s+performance",
            r"pause\s+campaign",
            r"stop\s+campaign",
            r"performance\s+drop",
            r"ctr\s+drop",
        ],
        "event": "banner.created",
        "workflow": "pause_campaigns",
        "rule_name": "banner_performance_pause",
        "conditions": [],
        "entity": "banner",
        "base_confidence": 0.71,
    },
    {
        "intent_id": "store_welcome",
        "keywords": [
            r"store\s+(?:is\s+)?created",
            r"new\s+store",
            r"instagram\s+launch",
            r"launch\s+(?:post|campaign)",
            r"welcome\s+campaign",
        ],
        "event": "store.created",
        "workflow": "welcome_instagram_post",
        "rule_name": "welcome_campaign",
        "conditions": [],
        "entity": "store",
        "base_confidence": 0.85,
    },
    {
        "intent_id": "store_rejected",
        "keywords": [
            r"store\s+(?:is\s+)?rejected",
            r"cancel\s+(?:the\s+)?workflow",
            r"cancel\s+campaign",
        ],
        "event": "store.rejected",
        "workflow": None,
        "cancel_workflow": "welcome_instagram_post",
        "rule_name": "cancel_welcome",
        "conditions": [],
        "entity": "store",
        "base_confidence": 0.88,
    },
    {
        "intent_id": "customer_question",
        "keywords": [
            r"customer\s+question",
            r"buyer\s+question",
            r"inquiry",
            r"faq",
        ],
        "event": "store.updated",
        "workflow": "support_escalation",
        "rule_name": "customer_question_support",
        "conditions": [],
        "entity": "store",
        "base_confidence": 0.62,
    },
    {
        "intent_id": "coupon_workflow",
        "keywords": [
            r"coupon",
            r"discount\s+code",
            r"promo\s+code",
        ],
        "event": "order.created",
        "workflow": "apology_coupon",
        "rule_name": "coupon_workflow",
        "conditions": [],
        "entity": "order",
        "base_confidence": 0.65,
    },
]


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")[:48] or "generated_rule"


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|(?:\s+)(?=(?:if|when)\s+)", text.strip(), flags=re.I)
    return [p.strip() for p in parts if p.strip()]


def _extract_delay_days(text: str) -> int:
    match = re.search(r"after\s+(\d+)\s*(day|days|d)\b", text, re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)\s*(day|days|d)\s+(?:later|delay)", text, re.I)
    if match:
        return int(match.group(1))
    return 0


def _extract_explicit_threshold(text: str) -> list[dict]:
    """Extract numeric thresholds from text."""
    conditions = []
    lower = text.lower()

    patterns = [
        (r"stock\s*(<|<=|>|>=)\s*(\d+)", "item.stock"),
        (r"inventory\s*(<|<=|>|>=)\s*(\d+)", "item.stock"),
        (r"sales\s*(<|<=|>|>=)\s*(\d+)", "item.sales"),
        (r"(\d+)\s*(?:units?|items?)\s+(?:left|remaining)", "item.stock"),
    ]

    for pattern, field in patterns:
        match = re.search(pattern, lower)
        if match:
            if len(match.groups()) == 2:
                op, val = match.group(1), int(match.group(2))
            else:
                op, val = "<", int(match.group(1))
            conditions.append({"field": field, "operator": op, "value": val})

    return conditions


def _match_intents(sentence: str) -> list[tuple[dict, float]]:
    """Return matched ontology entries with scores."""
    lower = sentence.lower()
    matches = []

    for entry in INTENT_ONTOLOGY:
        score = 0.0
        hits = 0
        for pattern in entry["keywords"]:
            if re.search(pattern, lower):
                hits += 1
                score = max(score, entry["base_confidence"])

        if hits:
            score = min(0.98, score + 0.03 * (hits - 1))
            matches.append((entry, score))

    return sorted(matches, key=lambda x: -x[1])


def _infer_from_existing_rules(
    sentence: str,
    existing_rules: list[dict],
) -> Optional[tuple[dict, float]]:
    """Boost inference using tenant's existing rule patterns."""
    lower = sentence.lower()
    best = None
    best_score = 0.0

    for rule in existing_rules:
        when = rule.get("when", "")
        workflow = rule.get("workflow", "")
        score = 0.0

        if workflow and workflow.replace("_", " ") in lower:
            score += 0.25
        if "stock" in lower and "stock" in when:
            score += 0.2
        if "sales" in lower and rule.get("conditions"):
            for c in rule["conditions"]:
                if "sales" in c.get("field", ""):
                    score += 0.25

        if score > best_score:
            best_score = score
            best = rule

    if best and best_score >= 0.2:
        entry = {
            "intent_id": "existing_pattern",
            "event": best["when"],
            "workflow": best.get("workflow"),
            "cancel_workflow": best.get("cancel_workflow"),
            "rule_name": best["name"] + "_variant",
            "conditions": list(best.get("conditions", [])),
            "base_confidence": 0.5 + best_score,
        }
        return entry, 0.5 + best_score

    return None


def _build_rule_from_intent(
    entry: dict,
    sentence: str,
    rule_name: Optional[str],
    used_names: set[str],
) -> dict:
    conditions = list(entry.get("conditions", []))
    explicit = _extract_explicit_threshold(sentence)
    if explicit:
        conditions = explicit

    name = rule_name or entry.get("rule_name", entry["intent_id"])
    base = _slugify(name)
    name = base
    counter = 1
    while name in used_names:
        name = f"{base}_{counter}"
        counter += 1
    used_names.add(name)

    return {
        "name": name,
        "when": entry["event"],
        "conditions": conditions,
        "workflow": entry.get("workflow"),
        "delay": _extract_delay_days(sentence),
        "cancel_workflow": entry.get("cancel_workflow"),
        "_intent": entry.get("intent_id"),
        "_confidence": entry.get("base_confidence", 0.5),
    }


def _legacy_explicit_event_parse(sentence: str) -> Optional[dict]:
    """Fallback for explicit event names in text."""
    lower = sentence.lower()
    event_map = [
        (r"store\.created|store\s+created", "store.created"),
        (r"stock\.updated|stock\s+updated", "stock.updated"),
        (r"order\.created|order\s+created", "order.created"),
        (r"product\.created|product\s+created", "product.created"),
    ]
    for pattern, event in event_map:
        if re.search(pattern, lower):
            return {"event": event, "confidence": 0.9}
    return None


def parse_natural_language(
    natural_language: str,
    rule_name: Optional[str] = None,
    existing_rules: Optional[list[dict]] = None,
    user_id: int = 1,
) -> SemanticParseResult:
    """
    Main entry: business intent → rule dicts + confidence metadata.
    """
    existing_rules = existing_rules or []
    sentences = _split_sentences(natural_language)

    if not sentences:
        return SemanticParseResult(
            confidence=0.0,
            ambiguities=["empty_input"],
            requires_confirmation=True,
            message="No text to parse",
        )

    generated: list[dict] = []
    used_names: set[str] = set()
    intent_scores: list[float] = []
    ambiguities: list[str] = []
    missing_thresholds: list[str] = []
    intents_detected: list[str] = []

    for sentence in sentences:
        matches = _match_intents(sentence)

        if not matches:
            existing_match = _infer_from_existing_rules(sentence, existing_rules)
            if existing_match:
                matches = [existing_match]

        if not matches:
            explicit = _legacy_explicit_event_parse(sentence)
            if explicit:
                ambiguities.append(
                    f"Partial explicit event only in: '{sentence[:60]}...'"
                )
            else:
                ambiguities.append(
                    f"No business intent recognized: '{sentence[:80]}'"
                )
            continue

        entry, score = matches[0]
        intents_detected.append(entry.get("intent_id", "unknown"))
        intent_scores.append(score)

        if entry.get("ambiguity_note"):
            ambiguities.append(entry["ambiguity_note"])

        if entry.get("threshold_fields") and not _extract_explicit_threshold(sentence):
            if not entry.get("conditions"):
                missing_thresholds.append(
                    f"{entry['intent_id']}: threshold not specified "
                    f"(e.g. {entry['threshold_fields'][0]} < N)"
                )
                score *= 0.85

        rule = _build_rule_from_intent(
            {**entry, "base_confidence": score},
            sentence,
            rule_name if len(sentences) == 1 else None,
            used_names,
        )

        if rule["when"] not in KNOWN_EVENTS:
            ambiguities.append(f"Inferred unknown event: {rule['when']}")
            score *= 0.7

        wf = rule.get("workflow")
        if wf and wf not in KNOWN_WORKFLOWS:
            ambiguities.append(f"Inferred workflow not registered: {wf}")

        if not rule.get("workflow") and not rule.get("cancel_workflow"):
            ambiguities.append(f"No action inferred for: {sentence[:50]}")
            continue

        generated.append(rule)

    if not generated:
        avg_confidence = 0.0
        requires_confirmation = True
        message = "Could not infer rules — clarification needed"
    else:
        avg_confidence = sum(intent_scores) / len(intent_scores)
        if missing_thresholds:
            avg_confidence *= 0.9
        if ambiguities:
            avg_confidence *= max(0.75, 1.0 - 0.05 * len(ambiguities))

        requires_confirmation = (
            avg_confidence < CONFIDENCE_APPLY_THRESHOLD
            or bool(missing_thresholds) and avg_confidence < 0.65
        )
        message = (
            "Ready to apply"
            if not requires_confirmation
            else "Low confidence — review before applying"
        )

    if avg_confidence < CONFIDENCE_GENERATE_THRESHOLD and not generated:
        requires_confirmation = True

    for rule in generated:
        rule.pop("_intent", None)
        rule.pop("_confidence", None)

    return SemanticParseResult(
        rules=generated,
        confidence=round(avg_confidence, 3),
        ambiguities=ambiguities,
        missing_thresholds=missing_thresholds,
        requires_confirmation=requires_confirmation,
        intents_detected=intents_detected,
        message=message,
    )
