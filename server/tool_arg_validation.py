from __future__ import annotations

from typing import Any

try:
    from .tool_registry import TOOL_METADATA, canonical_tool_name
except ImportError:
    from tool_registry import TOOL_METADATA, canonical_tool_name  # type: ignore


READ_ONLY_DANGERS = {"safe"}


def _schema_payload(schema: dict[str, Any] | None) -> dict[str, Any]:
    return ((schema or {}).get("function") or {}).get("parameters") or {}


def _schema_properties(schema: dict[str, Any] | None) -> dict[str, Any]:
    props = _schema_payload(schema).get("properties")
    return props if isinstance(props, dict) else {}


def _schema_required(schema: dict[str, Any] | None) -> set[str]:
    required = _schema_payload(schema).get("required") or []
    return {str(item) for item in required}


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on", "да"}:
        return True
    if text in {"false", "0", "no", "n", "off", "нет"}:
        return False
    return bool(value)


def _coerce_value(value: Any, spec: dict[str, Any]) -> Any:
    expected = spec.get("type")
    if expected == "boolean":
        return _bool_value(value)
    if expected == "integer":
        if isinstance(value, bool):
            return int(value)
        return int(value)
    if expected == "number":
        if isinstance(value, bool):
            return float(int(value))
        return float(value)
    if expected == "string" and value is not None:
        return str(value)
    return value


def _is_read_only(tool_name: str) -> bool:
    canonical = canonical_tool_name(tool_name)
    meta = TOOL_METADATA.get(canonical) or {}
    return meta.get("danger", "safe") in READ_ONLY_DANGERS


def validate_and_sanitize_tool_args(
    tool_name: str,
    args: dict[str, Any] | None,
    schema: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str], dict[str, Any] | None]:
    """Validate/sanitize LLM tool args before dispatch.

    v1 is deliberately small: unknown read-only args are stripped, while
    write/process/runtime tools reject unknown args to avoid surprising effects.
    """
    raw_args = dict(args or {})
    warnings: list[str] = []

    if tool_name == "window_list":
        if "visible" in raw_args:
            visible = _bool_value(raw_args.pop("visible"))
            raw_args["include_invisible"] = not visible
            warnings.append("mapped arg visible to include_invisible")
        if "process_name" in raw_args:
            raw_args.pop("process_name", None)
            warnings.append("ignored unknown arg: process_name; use window.find for filtering")

    properties = _schema_properties(schema)
    allowed = set(properties)
    unknown = sorted(key for key in raw_args if key not in allowed)
    if unknown:
        if _is_read_only(tool_name):
            for key in unknown:
                raw_args.pop(key, None)
                warnings.append(f"ignored unknown arg: {key}")
        else:
            return {}, warnings, {
                "status": "validation_error",
                "error": "unknown_tool_arguments",
                "unknown_args": unknown,
                "tool_name": canonical_tool_name(tool_name),
            }

    missing = sorted(key for key in _schema_required(schema) if raw_args.get(key) in (None, ""))
    if missing:
        return {}, warnings, {
            "status": "validation_error",
            "error": "missing_required_tool_arguments",
            "missing_args": missing,
            "tool_name": canonical_tool_name(tool_name),
        }

    clean_args: dict[str, Any] = {}
    for key, value in raw_args.items():
        spec = properties.get(key) or {}
        try:
            clean_args[key] = _coerce_value(value, spec)
        except (TypeError, ValueError):
            return {}, warnings, {
                "status": "validation_error",
                "error": "invalid_tool_argument_type",
                "arg": key,
                "expected": spec.get("type"),
                "tool_name": canonical_tool_name(tool_name),
            }

    return clean_args, warnings, None
