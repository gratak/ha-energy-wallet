"""Guess which energy sensors measure grid import and export."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from typing import Any

ENERGY_UNITS = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0}

_IMPORT_RE = re.compile(
    r"(energy_?buy|purchas|grid_?import|import|from_?grid|grid_?consum|consumption_?from_?grid|pob[oó]r|bought)",
    re.I,
)
_EXPORT_RE = re.compile(
    r"(energy_?sell|sold|grid_?export|export|feed_?in|to_?grid|grid_?return|return|oddan|injected)",
    re.I,
)
# Counters that reset or cover a window are no good as a cumulative source.
_SKIP_RE = re.compile(
    r"(daily|today|yesterday|day_?before|monthly|month|last_?\d|7_?days|week|year|hourly|power|_cost|_price|station)",
    re.I,
)


@dataclass(frozen=True)
class Candidate:
    entity_id: str
    score: int


def _text(entity_id: str, attrs: Mapping[str, Any]) -> str:
    return f"{entity_id} {attrs.get('friendly_name', '')}".replace(" ", "_")


def is_energy_counter(attrs: Mapping[str, Any]) -> bool:
    return (
        attrs.get("unit_of_measurement") in ENERGY_UNITS
        and attrs.get("state_class") in ("total", "total_increasing")
    )


def rank_candidates(states: Iterable[tuple[str, Mapping[str, Any]]]) -> tuple[list[Candidate], list[Candidate]]:
    """Rank sensors as likely grid import / export cumulative counters."""
    imports: list[Candidate] = []
    exports: list[Candidate] = []
    for entity_id, attrs in states:
        if not entity_id.startswith("sensor.") or not is_energy_counter(attrs):
            continue
        text = _text(entity_id, attrs)
        if _SKIP_RE.search(text):
            continue
        base = 10 if attrs.get("device_class") == "energy" else 0
        base += 5 if "total" in text.lower() else 0
        base += 3 if "grid" in text.lower() else 0
        is_imp, is_exp = bool(_IMPORT_RE.search(text)), bool(_EXPORT_RE.search(text))
        if is_imp and not is_exp:
            imports.append(Candidate(entity_id, base + 20))
        elif is_exp and not is_imp:
            exports.append(Candidate(entity_id, base + 20))
    imports.sort(key=lambda c: (-c.score, c.entity_id))
    exports.sort(key=lambda c: (-c.score, c.entity_id))
    return imports, exports


def grid_entities_from_energy_prefs(prefs: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    """Pull the first grid import/export statistic from Energy dashboard prefs."""
    if not prefs:
        return None, None
    found: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for key in ("stat_energy_from", "stat_energy_to"):
                value = node.get(key)
                if isinstance(value, str) and value.startswith("sensor.") and key not in found:
                    found[key] = value
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for source in prefs.get("energy_sources", []):
        if isinstance(source, Mapping) and source.get("type") == "grid":
            walk(source)
    return found.get("stat_energy_from"), found.get("stat_energy_to")
