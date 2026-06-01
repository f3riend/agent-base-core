import re

# =========================================================
# PARSE VALUE
# =========================================================

def parse_value(value):
    value = value.strip()

    if value.lower() == "true":
        return True

    if value.lower() == "false":
        return False

    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]

    try:
        return int(value)
    except ValueError:
        pass

    try:
        return float(value)
    except ValueError:
        pass

    return value


# =========================================================
# PARSE CONDITION
# =========================================================

def parse_condition(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    operators = ["==", "!=", ">=", "<=", ">", "<"]

    for op in operators:
        if op in line:
            left, right = line.split(op, 1)
            return {
                "field": left.strip(),
                "operator": op,
                "value": parse_value(right.strip()),
            }

    return None


def _normalize_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    return line


def _parse_delay(value: str) -> int:
    value = value.strip().lower()
    value = re.sub(r"\s+", "", value)
    match = re.match(r"^(\d+)(d|day|days)?$", value)
    if match:
        return int(match.group(1))
    return int(value)


def _parse_rule_block(block: str) -> dict | None:
    block = block.strip()
    if not block:
        return None

    lines = []
    for raw_line in block.splitlines():
        normalized = _normalize_line(raw_line)
        if normalized:
            lines.append(normalized)

    if not lines:
        return None

    rule = {
        "name": None,
        "when": None,
        "conditions": [],
        "workflow": None,
        "delay": 0,
        "cancel_workflow": None,
    }

    current_mode = None

    for line in lines:
        upper = line.upper()

        if upper.startswith("RULE"):
            rule["name"] = line[4:].strip()
            current_mode = None
            continue

        if upper == "WHEN":
            current_mode = "WHEN"
            continue

        if upper == "IF":
            current_mode = "IF"
            continue

        if upper == "THEN":
            current_mode = "THEN"
            continue

        if current_mode == "WHEN":
            rule["when"] = line.strip()

        elif current_mode == "IF":
            condition = parse_condition(line)
            if condition:
                rule["conditions"].append(condition)

        elif current_mode == "THEN":
            lower = line.lower()

            if lower.startswith("workflow "):
                rule["workflow"] = line.split(None, 1)[1].strip()

            elif lower.startswith("delay "):
                delay_raw = line.split(None, 1)[1].strip()
                rule["delay"] = _parse_delay(delay_raw)

            elif lower.startswith("cancel_workflow "):
                rule["cancel_workflow"] = line.split(None, 1)[1].strip()

    if rule["name"] and rule["when"]:
        return rule

    return None


def parse_rules_content(content: str) -> list[dict]:
    """Parse DSL string (DB-backed rules use this — not files)."""
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"(?=^RULE\b)", content, flags=re.MULTILINE)
    parsed_rules = []

    for block in blocks:
        rule = _parse_rule_block(block)
        if rule:
            parsed_rules.append(rule)

    return parsed_rules


def parse_rules(file_path="rules.txt"):
    """Optional file import — runtime uses rule_service cache."""
    with open(file_path, "r", encoding="utf-8-sig") as f:
        content = f.read()
    return parse_rules_content(content)


# =========================================================
# GET VALUE
# =========================================================

def get_nested_value(context, field):
    parts = field.split(".")
    current = context

    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None

    return current


# =========================================================
# EVALUATE
# =========================================================

def evaluate_condition(condition, context):
    actual = get_nested_value(context, condition["field"])
    expected = condition["value"]
    operator = condition["operator"]

    print(
        f"[DEBUG] evaluating_condition="
        f"{condition['field']} {operator} {expected} "
        f"(actual={actual!r})"
    )

    if actual is None:
        return False

    try:
        if operator == "==":
            return actual == expected
        if operator == "!=":
            return actual != expected
        if operator == ">":
            return actual > expected
        if operator == "<":
            return actual < expected
        if operator == ">=":
            return actual >= expected
        if operator == "<=":
            return actual <= expected
    except TypeError:
        return False

    return False


# =========================================================
# MATCH
# =========================================================

def find_matching_rules(
    event_name,
    context,
    rules: list[dict] | None = None,
    user_id: int | None = None,
    debug=True,
):
    """
    Match against pre-compiled rules from rule_service cache.
    Do NOT parse DSL each poll — pass rules or user_id.
    """
    if rules is None:
        if user_id is not None:
            from rule_service import get_compiled_rules
            rules = get_compiled_rules(user_id)
        else:
            from rule_service import get_compiled_rules
            from db import DEFAULT_USER_ID
            rules = get_compiled_rules(DEFAULT_USER_ID)

    matched = []

    if debug:
        print(f"[DEBUG] event_name={event_name}")
        print(f"[DEBUG] context={context}")
        print(f"[DEBUG] evaluating {len(rules)} compiled rule(s)")

    for rule in rules:
        if rule["when"] != event_name:
            continue

        valid = True

        for condition in rule["conditions"]:
            if not evaluate_condition(condition, context):
                valid = False
                break

        if valid:
            matched.append(rule)

    if debug:
        names = [r["name"] for r in matched]
        print(f"[DEBUG] matched_rules={names}")

    return matched
