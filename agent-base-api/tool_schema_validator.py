"""
OpenAI/CrewAI tool schema validation — required[] must match properties{}.
"""

from __future__ import annotations

from typing import Any, Type

from pydantic import BaseModel


def pydantic_to_openai_schema(model: Type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    props = schema.get("properties") or {}
    # OpenAI strict: required must only list keys without defaults
    required = []
    for key, spec in props.items():
        if "default" not in spec:
            required.append(key)
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


def validate_openai_tool_schema(schema: dict, tool_name: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    props = schema.get("properties") or {}
    if not isinstance(props, dict):
        return False, [f"{tool_name}: properties must be object"]

    required = schema.get("required") or []
    if not isinstance(required, list):
        return False, [f"{tool_name}: required must be array"]

    prop_names = set(props.keys())
    for r in required:
        if r not in prop_names:
            errors.append(f"{tool_name}: required '{r}' missing from properties")
        elif "default" in props.get(r, {}):
            errors.append(f"{tool_name}: required '{r}' has default (invalid)")

    return len(errors) == 0, errors


def _resolve_args_schema(tool: Any) -> Type[BaseModel] | None:
    schema = getattr(tool, "args_schema", None)
    if schema is None:
        schema = getattr(type(tool), "args_schema", None)
    if schema is None:
        return None
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema
    if isinstance(schema, BaseModel):
        return type(schema)
    return None


def validate_tool_instance(tool: Any, tool_name: str) -> tuple[bool, list[str]]:
    model_cls = _resolve_args_schema(tool)
    if model_cls is None:
        return False, [f"{tool_name}: missing args_schema"]

    openai_schema = pydantic_to_openai_schema(model_cls)
    return validate_openai_tool_schema(openai_schema, tool_name)


def validate_all_tools(tools_dict: dict) -> dict[str, Any]:
    valid = []
    invalid = []
    details = {}

    for name, tool in tools_dict.items():
        ok, errs = validate_tool_instance(tool, name)
        details[name] = {"valid": ok, "errors": errs}
        if ok:
            valid.append(name)
        else:
            invalid.append(name)

    return {
        "total": len(tools_dict),
        "valid_count": len(valid),
        "invalid_count": len(invalid),
        "valid": valid,
        "invalid": invalid,
        "details": details,
    }


def print_validation_summary(summary: dict):
    for name in summary["valid"]:
        print(f"[TOOL_SCHEMA_OK] {name}")
    for name in summary["invalid"]:
        errs = summary["details"][name]["errors"]
        print(f"[TOOL_SCHEMA_INVALID] {name}: {'; '.join(errs)}")


def get_valid_tools(tools_dict: dict) -> dict:
    summary = validate_all_tools(tools_dict)
    print_validation_summary(summary)
    valid = {k: v for k, v in tools_dict.items() if summary["details"][k]["valid"]}
    if not valid:
        print("[TOOL_SCHEMA] Uyarı: geçerli araç yok — ham set kullanılıyor")
        return tools_dict
    return valid
