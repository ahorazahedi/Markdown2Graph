"""User-tunable settings stored in the app_state DB.

Distinct from `app.config.Settings` (which reads .env and is process-wide
infra config: hosts, keys, dimensions). These are runtime knobs the operator
flips from the UI — retries, batch sizes, timeouts that don't warrant a
restart.

Each setting has a typed default; reads coerce stored JSON back to that type
so callers can rely on `int`, `float`, `bool` etc. directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from ..repositories.app_state_repository import AppStateRepository

Kind = Literal["int", "float", "bool", "str"]


@dataclass(frozen=True)
class SettingSpec:
    key: str
    kind: Kind
    default: Any
    label: str
    description: str
    min: float | None = None
    max: float | None = None
    group: str = "general"


# Single source of truth.
SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        key="extraction_retry_count",
        kind="int", default=2, min=0, max=10,
        label="Extraction retries per chunk",
        description=(
            "How many additional attempts to make when the LLM's JSON-structured "
            "output for a chunk fails to parse or returns no nodes. 0 disables "
            "retries; the chunk is logged as an error and the run continues."
        ),
        group="extraction",
    ),
    SettingSpec(
        key="extraction_retry_backoff_seconds",
        kind="float", default=1.5, min=0, max=30,
        label="Initial retry backoff (seconds)",
        description=(
            "Delay before the first retry. Each subsequent retry doubles this "
            "delay (exponential backoff)."
        ),
        group="extraction",
    ),
    SettingSpec(
        key="extraction_min_nodes_for_success",
        kind="int", default=0, min=0, max=100,
        label="Min nodes to consider chunk successful",
        description=(
            "If the LLM returns fewer than this many entities for a chunk, treat "
            "the call as failed and retry. 0 disables this heuristic — any "
            "non-error response counts as success."
        ),
        group="extraction",
    ),
)

_SPEC_BY_KEY: dict[str, SettingSpec] = {s.key: s for s in SPECS}


def _coerce(spec: SettingSpec, raw: Any) -> Any:
    try:
        if spec.kind == "int":   return int(raw)
        if spec.kind == "float": return float(raw)
        if spec.kind == "bool":  return bool(raw)
        if spec.kind == "str":   return str(raw)
    except Exception:
        return spec.default
    return raw


class SettingsService:
    def __init__(self, state: AppStateRepository | None = None):
        self.state = state or AppStateRepository()

    def get(self, key: str) -> Any:
        spec = _SPEC_BY_KEY.get(key)
        if spec is None:
            return self.state.get_setting(key)
        raw = self.state.get_setting(key, spec.default)
        return _coerce(spec, raw)

    def set(self, key: str, value: Any) -> Any:
        spec = _SPEC_BY_KEY.get(key)
        if spec is None:
            self.state.set_setting(key, value)
            return value
        coerced = _coerce(spec, value)
        if spec.min is not None and coerced < spec.min:
            coerced = type(spec.default)(spec.min)
        if spec.max is not None and coerced > spec.max:
            coerced = type(spec.default)(spec.max)
        self.state.set_setting(key, coerced)
        return coerced

    def specs(self) -> list[dict]:
        out = []
        for s in SPECS:
            out.append({
                "key": s.key,
                "kind": s.kind,
                "default": s.default,
                "value": self.get(s.key),
                "label": s.label,
                "description": s.description,
                "min": s.min,
                "max": s.max,
                "group": s.group,
            })
        return out

    def bulk_set(self, mapping: dict) -> dict:
        out = {}
        for k, v in mapping.items():
            out[k] = self.set(k, v)
        return out
