"""
Natural Language Rule Management System.

Flow:
  Natural Language → AI/heuristic generator → rules DB → rule_service cache → rule_engine

DB is the primary source of truth. rules.txt is optional export/import only.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Any

from db import DEFAULT_USER_ID, db_connection, execute_query, init_db, now_iso
from rule_engine import parse_condition, parse_rules, parse_rules_content
from rule_service import (
    export_to_file,
    get_compiled_rules,
    invalidate_cache,
    list_rules,
    save_rules_batch,
)
from semantic_parser import (
    CONFIDENCE_APPLY_THRESHOLD,
    KNOWN_EVENTS,
    KNOWN_WORKFLOWS,
    SemanticParseResult,
)

RULES_PATH = os.environ.get("RULES_PATH", "rules.txt")


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    parsed_rules: list[dict] = field(default_factory=list)


@dataclass
class RuleChangeResult:
    success: bool
    action: str
    natural_language: str
    generated_rules: list[dict] = field(default_factory=list)
    dsl_preview: str = ""
    full_content_preview: str = ""
    conflicts: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    validation: ValidationResult | None = None
    history_id: int | None = None
    message: str = ""
    semantic: SemanticParseResult | None = None


def read_rules_file(path: str = RULES_PATH) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8-sig") as f:
        return f.read()


def write_rules_file(content: str, path: str = RULES_PATH) -> None:
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    if content and not content.endswith("\n"):
        content += "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def format_condition(condition: dict) -> str:
    value = condition["value"]
    if isinstance(value, str):
        value_str = value
    elif isinstance(value, bool):
        value_str = "true" if value else "false"
    else:
        value_str = str(value)
    return f"{condition['field']} {condition['operator']} {value_str}"


def format_rule_block(rule: dict) -> str:
    lines = [
        f"RULE {rule['name']}",
        "WHEN",
        rule["when"],
    ]

    if rule.get("conditions"):
        lines.append("IF")
        for cond in rule["conditions"]:
            lines.append(format_condition(cond))

    lines.append("THEN")

    if rule.get("workflow"):
        lines.append(f"workflow {rule['workflow']}")

    delay = rule.get("delay", 0)
    if delay:
        lines.append(f"delay {delay}d")

    if rule.get("cancel_workflow"):
        lines.append(f"cancel_workflow {rule['cancel_workflow']}")

    return "\n".join(lines)


def format_rules_file(rules: list[dict]) -> str:
    blocks = [format_rule_block(r) for r in rules]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def validate_dsl_content(content: str) -> ValidationResult:
    errors = []

    if not content.strip():
        return ValidationResult(valid=True, parsed_rules=[])

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        with open(tmp_path, "r", encoding="utf-8") as f:
            parsed = parse_rules_content(f.read())
    except Exception as exc:
        errors.append(f"parse_error: {exc}")
        return ValidationResult(valid=False, errors=errors)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if "RULE" in content and not parsed:
        errors.append("no_valid_rules_parsed")

    names = []
    for rule in parsed:
        if not rule.get("name"):
            errors.append("rule_missing_name")
        elif rule["name"] in names:
            errors.append(f"duplicate_rule_name_in_content: {rule['name']}")
        else:
            names.append(rule["name"])

        if not rule.get("when"):
            errors.append(f"rule_missing_when: {rule.get('name')}")

        if not rule.get("workflow") and not rule.get("cancel_workflow"):
            errors.append(
                f"rule_missing_action: {rule.get('name')} "
                "(need workflow or cancel_workflow)"
            )

        if rule.get("when") and rule["when"] not in KNOWN_EVENTS:
            errors.append(
                f"unknown_event: {rule['when']} "
                f"(known: {sorted(KNOWN_EVENTS)})"
            )

        if rule.get("workflow") and rule["workflow"] not in KNOWN_WORKFLOWS:
            errors.append(
                f"unknown_workflow: {rule['workflow']} "
                f"(known: {sorted(KNOWN_WORKFLOWS)})"
            )

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        parsed_rules=parsed,
    )


def rules_are_semantically_equal(a: dict, b: dict) -> bool:
    return (
        a.get("name") == b.get("name")
        and a.get("when") == b.get("when")
        and a.get("workflow") == b.get("workflow")
        and a.get("cancel_workflow") == b.get("cancel_workflow")
        and a.get("delay", 0) == b.get("delay", 0)
        and a.get("conditions") == b.get("conditions")
    )


def detect_duplicates(
    new_rules: list[dict],
    existing_rules: list[dict],
) -> list[str]:
    duplicates = []

    existing_names = {r["name"] for r in existing_rules}
    existing_semantic = list(existing_rules)

    for rule in new_rules:
        if rule["name"] in existing_names:
            duplicates.append(
                f"duplicate_name: rule '{rule['name']}' already exists"
            )

        for existing in existing_semantic:
            if rules_are_semantically_equal(rule, existing):
                duplicates.append(
                    f"duplicate_semantic: '{rule['name']}' matches "
                    f"existing '{existing['name']}'"
                )

    return duplicates


def detect_conflicts(
    new_rules: list[dict],
    existing_rules: list[dict],
) -> list[str]:
    conflicts = []
    all_rules = existing_rules + new_rules

    by_when: dict[str, list[dict]] = {}
    for rule in all_rules:
        by_when.setdefault(rule["when"], []).append(rule)

    for event, rules in by_when.items():
        creators = [
            r for r in rules
            if r.get("workflow") and not r.get("conditions")
        ]
        if len(creators) > 1:
            names = [r["name"] for r in creators]
            conflicts.append(
                f"conflict_unconditional_workflows on {event}: {names}"
            )

        workflows = {}
        for rule in rules:
            wf = rule.get("workflow")
            if not wf:
                continue
            key = (event, wf, json.dumps(rule.get("conditions", []), sort_keys=True))
            if key in workflows:
                conflicts.append(
                    f"conflict_duplicate_workflow_trigger: {wf} on {event} "
                    f"({workflows[key]} vs {rule['name']})"
                )
            workflows[key] = rule["name"]

    new_names = {r["name"] for r in new_rules}
    for new_rule in new_rules:
        for existing in existing_rules:
            if existing["name"] in new_names:
                continue
            if (
                new_rule["when"] == existing["when"]
                and new_rule.get("workflow")
                and existing.get("workflow")
                and new_rule["workflow"] == existing["workflow"]
                and new_rule.get("conditions") != existing.get("conditions")
            ):
                conflicts.append(
                    f"conflict_overlapping_workflow: {new_rule['name']} and "
                    f"{existing['name']} both trigger {new_rule['workflow']} "
                    f"on {new_rule['when']} with different conditions"
                )

    return conflicts


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")[:48] or "generated_rule"


def _extract_delay_days(text: str) -> int:
    match = re.search(
        r"after\s+(\d+)\s*(day|days|d)\b",
        text,
        re.IGNORECASE,
    )
    if match:
        return int(match.group(1))

    match = re.search(r"(\d+)\s*(day|days|d)\s+(later|delay)", text, re.IGNORECASE)
    if match:
        return int(match.group(1))

    return 0


def _extract_event(text: str) -> str | None:
    lower = text.lower()

    patterns = [
        (r"store\s+(is\s+)?created", "store.created"),
        (r"store\s+(is\s+)?rejected", "store.rejected"),
        (r"store\s+(is\s+)?updated", "store.updated"),
        (r"store\s+(is\s+)?deleted", "store.deleted"),
        (r"product\s+(is\s+)?created", "product.created"),
        (r"stock\s+(is\s+)?updated", "stock.updated"),
        (r"order\s+(is\s+)?created", "order.created"),
        (r"banner\s+(is\s+)?created", "banner.created"),
        (r"low\s+stock", "stock.updated"),
    ]

    for pattern, event in patterns:
        if re.search(pattern, lower):
            return event

    return None


def _extract_workflow(text: str) -> str | None:
    lower = text.lower()

    if re.search(r"instagram|launch\s+post|launch\s+campaign", lower):
        return "welcome_instagram_post"

    if re.search(r"low\s+stock|stock\s+alert|inventory", lower):
        return "low_stock_alert"

    return None


def _extract_cancel_workflow(text: str) -> str | None:
    lower = text.lower()

    if "cancel" not in lower:
        return None

    if re.search(r"welcome|instagram|launch", lower):
        return "welcome_instagram_post"

    if re.search(r"low\s+stock|stock\s+alert", lower):
        return "low_stock_alert"

    if re.search(r"workflow", lower):
        return "welcome_instagram_post"

    return None


def _extract_conditions(text: str, event: str) -> list[dict]:
    conditions = []
    lower = text.lower()

    stock_match = re.search(r"stock\s*(<|<=|>|>=)\s*(\d+)", lower)
    if stock_match:
        op = stock_match.group(1)
        val = int(stock_match.group(2))
        conditions.append({
            "field": "item.stock",
            "operator": op,
            "value": val,
        })
    elif "low stock" in lower and event == "stock.updated":
        conditions.append({
            "field": "item.stock",
            "operator": "<",
            "value": 10,
        })

    if re.search(r"active\s*==\s*true|store\s+is\s+active", lower):
        conditions.append({
            "field": "store.active",
            "operator": "==",
            "value": True,
        })

    return conditions


def _split_nl_sentences(natural_language: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", natural_language.strip())
    return [p.strip() for p in parts if p.strip()]


def generate_rules_from_nl_heuristic(
    natural_language: str,
    rule_name: str | None = None,
) -> list[dict]:
    """Deterministic NL → rule dict conversion (offline-capable)."""
    sentences = _split_nl_sentences(natural_language)
    generated = []
    used_names = set()

    for idx, sentence in enumerate(sentences):
        lower = sentence.lower()
        event = _extract_event(sentence)
        if not event:
            continue

        delay = _extract_delay_days(sentence)
        cancel_wf = _extract_cancel_workflow(sentence)
        workflow = None if cancel_wf else _extract_workflow(sentence)

        if not workflow and not cancel_wf:
            continue

        if rule_name and len(sentences) == 1:
            name = rule_name
        elif cancel_wf:
            name = _slugify(f"cancel_{cancel_wf}_{event}")
        elif workflow:
            name = _slugify(f"{workflow}_{event}")
        else:
            name = _slugify(f"rule_{event}_{idx}")

        base = name
        counter = 1
        while name in used_names:
            name = f"{base}_{counter}"
            counter += 1
        used_names.add(name)

        rule = {
            "name": name,
            "when": event,
            "conditions": _extract_conditions(sentence, event),
            "workflow": workflow,
            "delay": delay,
            "cancel_workflow": cancel_wf,
        }
        generated.append(rule)

    return generated


def generate_rules_from_nl_ai(
    natural_language: str,
    existing_rules: list[dict],
) -> list[dict]:
    """Legacy NL → DSL rule conversion — native OpenAI (CrewAI kaldırıldı, Tur 2).

    Yeni operatör NL girişleri `nl_rule_parser` üzerinden geçiyor; bu
    legacy DSL path'i sadece eski rules.txt formatını korumak için
    duruyor."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set for AI rule generation")

    from openai import OpenAI

    existing_summary = json.dumps(
        [
            {
                "name": r["name"],
                "when": r["when"],
                "workflow": r.get("workflow"),
                "cancel_workflow": r.get("cancel_workflow"),
                "delay": r.get("delay", 0),
                "conditions": r.get("conditions", []),
            }
            for r in existing_rules
        ],
        indent=2,
    )

    user_prompt = (
        "Convert the following natural language into JSON rule objects.\n\n"
        f"Natural language:\n{natural_language}\n\n"
        f"Existing rules (preserve unless explicitly replaced):\n{existing_summary}\n\n"
        f"Allowed events: {sorted(KNOWN_EVENTS)}\n"
        f"Allowed workflows: {sorted(KNOWN_WORKFLOWS)}\n\n"
        'Return ONLY a JSON object with key "rules" — an array. Each rule:\n'
        '- name (snake_case string)\n'
        '- when (event string)\n'
        '- conditions (array)\n'
        '- workflow (string or null)\n'
        '- delay (integer days)\n'
        '- cancel_workflow (string or null)'
    )

    client = OpenAI(timeout=15)
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": (
                "You convert Turkish or English natural language automation "
                "instructions into structured rule DSL JSON. Never execute "
                "anything. Preserve existing rules unless replaced. Use only "
                "the allowed events and workflows."
            )},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=800,
    )
    raw = (completion.choices[0].message.content or "").strip()
    try:
        parsed_obj = json.loads(raw)
        if isinstance(parsed_obj, dict) and isinstance(parsed_obj.get("rules"), list):
            result = json.dumps(parsed_obj["rules"])
        else:
            result = raw
    except json.JSONDecodeError:
        result = raw

    match = re.search(r"\[[\s\S]*\]", result)
    if not match:
        raise ValueError("AI did not return valid JSON array")

    raw_rules = json.loads(match.group())

    parsed = []
    for item in raw_rules:
        conditions = []
        for cond in item.get("conditions", []):
            if isinstance(cond, dict) and "field" in cond:
                conditions.append({
                    "field": cond["field"],
                    "operator": cond["operator"],
                    "value": cond["value"],
                })
            elif isinstance(cond, str):
                parsed_cond = parse_condition(cond)
                if parsed_cond:
                    conditions.append(parsed_cond)

        parsed.append({
            "name": item["name"],
            "when": item["when"],
            "conditions": conditions,
            "workflow": item.get("workflow"),
            "delay": int(item.get("delay", 0)),
            "cancel_workflow": item.get("cancel_workflow"),
        })

    return parsed


def generate_rules_from_nl(
    natural_language: str,
    rule_name: str | None = None,
    use_ai: bool | None = None,
    user_id: int = DEFAULT_USER_ID,
) -> tuple[list[dict], SemanticParseResult]:
    """
    Rule DSL generation via semantic_parser (tenant rules).
    Runtime creative orchestration uses autonomous_planner separately.
    """
    from semantic_parser import parse_natural_language

    existing = get_compiled_rules(user_id)

    semantic_result = parse_natural_language(
        natural_language,
        rule_name=rule_name,
        existing_rules=existing,
        user_id=user_id,
    )

    if use_ai is None:
        use_ai = os.environ.get("AI_RULE_GENERATOR_ENABLED", "0") == "1"

    if use_ai and os.environ.get("OPENAI_API_KEY"):
        try:
            ai_rules = generate_rules_from_nl_ai(natural_language, existing)
            if ai_rules:
                semantic_result.rules = ai_rules
                semantic_result.confidence = max(
                    semantic_result.confidence, 0.8
                )
                semantic_result.requires_confirmation = (
                    semantic_result.confidence < CONFIDENCE_APPLY_THRESHOLD
                )
        except Exception as exc:
            print(f"[RULE MANAGER] AI enhancement failed: {exc}")

    if not semantic_result.rules:
        legacy = generate_rules_from_nl_heuristic(natural_language, rule_name)
        if legacy:
            semantic_result.rules = legacy
            semantic_result.confidence = max(semantic_result.confidence, 0.5)

    print(
        f"[SEMANTIC] confidence={semantic_result.confidence} "
        f"intents={semantic_result.intents_detected} "
        f"requires_confirmation={semantic_result.requires_confirmation}"
    )

    return semantic_result.rules, semantic_result


def merge_rules(
    existing_rules: list[dict],
    new_rules: list[dict],
    mode: str = "upsert",
) -> list[dict]:
    """
    mode=upsert  → replace rules with same name, append others
    mode=append → append only new names
    mode=replace→ replace entire file with new_rules
    """
    if mode == "replace":
        return list(new_rules)

    merged = {r["name"]: r for r in existing_rules}

    for rule in new_rules:
        if mode == "append" and rule["name"] in merged:
            continue
        merged[rule["name"]] = rule

    return list(merged.values())


def save_rule_history(
    natural_language: str,
    action: str,
    generated_rules: list[dict],
    full_snapshot: str,
    conflicts: list[str],
    validation: ValidationResult,
    applied: bool,
) -> int:
    init_db()
    with db_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO rule_history (
                rule_name, natural_language, generated_dsl,
                full_rules_snapshot, action, conflicts_json,
                validation_status, validation_errors, applied, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ", ".join(r["name"] for r in generated_rules) if generated_rules else None,
                natural_language,
                format_rules_file(generated_rules) if generated_rules else "",
                full_snapshot,
                action,
                json.dumps(conflicts),
                "valid" if validation.valid else "invalid",
                json.dumps(validation.errors),
                1 if applied else 0,
                now_iso(),
            ),
        )
        history_id = cursor.lastrowid
    return history_id


def get_rule_history(limit: int = 20, user_id: int = DEFAULT_USER_ID) -> list[dict]:
    init_db()
    rows = execute_query(
        """
        SELECT * FROM rule_history
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    return [dict(row) for row in rows]


class RuleManager:
    def __init__(
        self,
        user_id: int = DEFAULT_USER_ID,
        rules_path: str = RULES_PATH,
    ):
        self.user_id = user_id
        self.rules_path = rules_path

    def read(self) -> str:
        """Export current DB rules as DSL text."""
        return export_to_file(self.user_id, self.rules_path)

    def read_parsed(self) -> list[dict]:
        return get_compiled_rules(self.user_id)

    def validate(self, content: str | None = None) -> ValidationResult:
        content = content if content is not None else self.read()
        return validate_dsl_content(content)

    def preview(
        self,
        natural_language: str,
        rule_name: str | None = None,
        mode: str = "upsert",
        use_ai: bool | None = None,
    ) -> RuleChangeResult:
        return self._process(
            natural_language=natural_language,
            rule_name=rule_name,
            mode=mode,
            use_ai=use_ai,
            dry_run=True,
            action_label="preview",
        )

    def dry_run(
        self,
        natural_language: str,
        rule_name: str | None = None,
        mode: str = "upsert",
        use_ai: bool | None = None,
    ) -> RuleChangeResult:
        return self._process(
            natural_language=natural_language,
            rule_name=rule_name,
            mode=mode,
            use_ai=use_ai,
            dry_run=True,
            action_label="dry_run",
        )

    def apply(
        self,
        natural_language: str,
        rule_name: str | None = None,
        mode: str = "upsert",
        use_ai: bool | None = None,
    ) -> RuleChangeResult:
        return self._process(
            natural_language=natural_language,
            rule_name=rule_name,
            mode=mode,
            use_ai=use_ai,
            dry_run=False,
            action_label="apply",
        )

    def _process(
        self,
        natural_language: str,
        rule_name: str | None,
        mode: str,
        use_ai: bool | None,
        dry_run: bool,
        action_label: str,
    ) -> RuleChangeResult:
        existing_rules = self.read_parsed()
        existing_content = self.read()

        try:
            generated_rules, semantic = generate_rules_from_nl(
                natural_language,
                rule_name=rule_name,
                use_ai=use_ai,
                user_id=self.user_id,
            )
        except Exception as exc:
            validation = ValidationResult(valid=False, errors=[str(exc)])
            history_id = save_rule_history(
                natural_language=natural_language,
                action=action_label,
                generated_rules=[],
                full_snapshot=existing_content,
                conflicts=[],
                validation=validation,
                applied=False,
            )
            return RuleChangeResult(
                success=False,
                action=action_label,
                natural_language=natural_language,
                validation=validation,
                history_id=history_id,
                message=f"generation_failed: {exc}",
            )

        if not generated_rules:
            validation = ValidationResult(
                valid=False,
                errors=["no_rules_generated_from_natural_language"],
            )
            history_id = save_rule_history(
                natural_language=natural_language,
                action=action_label,
                generated_rules=[],
                full_snapshot=existing_content,
                conflicts=[],
                validation=validation,
                applied=False,
            )
            return RuleChangeResult(
                success=False,
                action=action_label,
                natural_language=natural_language,
                validation=validation,
                history_id=history_id,
                semantic=semantic,
                message=semantic.message if semantic else "Could not interpret natural language",
            )

        if semantic.requires_confirmation and action_label == "apply":
            validation = ValidationResult(
                valid=False,
                errors=[
                    f"confidence_too_low: {semantic.confidence} "
                    f"(need {CONFIDENCE_APPLY_THRESHOLD})",
                    *semantic.ambiguities,
                    *semantic.missing_thresholds,
                ],
            )
            history_id = save_rule_history(
                natural_language=natural_language,
                action=action_label,
                generated_rules=generated_rules,
                full_snapshot=existing_content,
                conflicts=semantic.ambiguities,
                validation=validation,
                applied=False,
            )
            return RuleChangeResult(
                success=False,
                action=action_label,
                natural_language=natural_language,
                generated_rules=generated_rules,
                dsl_preview=format_rules_file(generated_rules),
                conflicts=[],
                duplicates=[],
                validation=validation,
                history_id=history_id,
                semantic=semantic,
                message=(
                    "Blocked: low confidence or ambiguous intent — "
                    "confirm thresholds and intent before applying"
                ),
            )

        duplicates = detect_duplicates(generated_rules, existing_rules)
        conflicts = detect_conflicts(generated_rules, existing_rules)

        merged_rules = merge_rules(existing_rules, generated_rules, mode=mode)
        new_content = format_rules_file(merged_rules)
        validation = validate_dsl_content(new_content)

        dsl_preview = format_rules_file(generated_rules)

        blocking = not validation.valid

        if action_label == "apply":
            if conflicts:
                blocking = True
            if duplicates and mode == "append":
                blocking = True

        history_id = save_rule_history(
            natural_language=natural_language,
            action=action_label,
            generated_rules=generated_rules,
            full_snapshot=new_content,
            conflicts=conflicts + duplicates,
            validation=validation,
            applied=False,
        )

        if blocking:
            return RuleChangeResult(
                success=False,
                action=action_label,
                natural_language=natural_language,
                generated_rules=generated_rules,
                dsl_preview=dsl_preview,
                full_content_preview=new_content,
                conflicts=conflicts,
                duplicates=duplicates,
                validation=validation,
                history_id=history_id,
                semantic=semantic,
                message="Blocked: validation failed or conflicts/duplicates detected",
            )

        if not dry_run:
            save_rules_batch(
                self.user_id,
                generated_rules,
                natural_language=natural_language,
            )
            invalidate_cache(self.user_id)
            with db_connection() as conn:
                conn.execute(
                    "UPDATE rule_history SET applied=1 WHERE id=?",
                    (history_id,),
                )

        return RuleChangeResult(
            success=True,
            action=action_label,
            natural_language=natural_language,
            generated_rules=generated_rules,
            dsl_preview=dsl_preview,
            full_content_preview=new_content,
            conflicts=conflicts,
            duplicates=duplicates,
            validation=validation,
            history_id=history_id,
            semantic=semantic,
            message=(
                "Preview only — not written to DB"
                if dry_run
                else f"DB updated ({len(generated_rules)} rule(s)) user_id={self.user_id}"
            ),
        )


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Natural Language Rule Management"
    )
    parser.add_argument(
        "command",
        choices=["preview", "dry-run", "apply", "validate", "list", "history"],
    )
    parser.add_argument("text", nargs="?", default="")
    parser.add_argument("--name", dest="rule_name", default=None)
    parser.add_argument(
        "--mode",
        choices=["upsert", "append", "replace"],
        default="upsert",
    )
    parser.add_argument("--ai", action="store_true")
    parser.add_argument("--file", dest="rules_path", default=RULES_PATH)
    parser.add_argument("--user-id", type=int, default=DEFAULT_USER_ID)

    args = parser.parse_args()
    manager = RuleManager(
        user_id=args.user_id,
        rules_path=args.rules_path,
    )

    if args.command == "list":
        rules = manager.read_parsed()
        print(format_rules_file(rules))
        return

    if args.command == "validate":
        result = manager.validate()
        print(json.dumps({
            "valid": result.valid,
            "errors": result.errors,
            "rule_count": len(result.parsed_rules),
        }, indent=2))
        return

    if args.command == "history":
        rows = get_rule_history()
        print(json.dumps(rows, indent=2, default=str))
        return

    if not args.text:
        print("Error: natural language text required")
        return

    if args.command == "preview":
        result = manager.preview(
            args.text, rule_name=args.rule_name, mode=args.mode, use_ai=args.ai
        )
    elif args.command == "dry-run":
        result = manager.dry_run(
            args.text, rule_name=args.rule_name, mode=args.mode, use_ai=args.ai
        )
    else:
        result = manager.apply(
            args.text, rule_name=args.rule_name, mode=args.mode, use_ai=args.ai
        )

    print(json.dumps({
        "success": result.success,
        "action": result.action,
        "message": result.message,
        "conflicts": result.conflicts,
        "duplicates": result.duplicates,
        "validation": {
            "valid": result.validation.valid if result.validation else False,
            "errors": result.validation.errors if result.validation else [],
        },
        "dsl_preview": result.dsl_preview,
        "history_id": result.history_id,
        "semantic": result.semantic.to_dict() if result.semantic else None,
    }, indent=2))


if __name__ == "__main__":
    main()
