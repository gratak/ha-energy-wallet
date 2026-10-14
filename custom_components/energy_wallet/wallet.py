"""Energy accounting: meter deltas, 15-minute settlement, period summaries.

Kept free of Home Assistant imports so it can be unit tested on its own.
Energy is attributed to the 15-minute slot in which the meter moved and
priced once the price for that slot is known.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

IMPORT = "import"
EXPORT = "export"

SLOT_SECONDS = 15 * 60
PENDING_MAX_AGE = timedelta(days=3)

PriceFn = Callable[[datetime], float | None]


def slot_start(ts: datetime) -> int:
    """UTC epoch seconds of the 15-minute slot containing ``ts``."""
    epoch = int(ts.timestamp())
    return epoch - epoch % SLOT_SECONDS


def _empty_day() -> dict[str, float]:
    return {"imp": 0.0, "exp": 0.0, "cost": 0.0, "rev": 0.0, "imp_unpriced": 0.0, "exp_unpriced": 0.0}


class Wallet:
    """Persistent accumulator for one grid connection."""

    def __init__(self, data: dict[str, Any] | None = None, retention_days: int = 800) -> None:
        data = data or {}
        self.days: dict[str, dict[str, float]] = data.get("days", {})
        self.pending: dict[str, dict[str, Any]] = data.get("pending", {})
        self.meters: dict[str, dict[str, Any]] = data.get("meters", {})
        self.totals: dict[str, float] = data.get("totals", {"cost": 0.0, "rev": 0.0, "imp": 0.0, "exp": 0.0})
        self.rce_daily_mean: dict[str, float] = data.get("rce_daily_mean", {})
        self.rcem_override: dict[str, float] = data.get("rcem_override", {})
        self.retention_days = retention_days

    def as_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "pending": self.pending,
            "meters": self.meters,
            "totals": self.totals,
            "rce_daily_mean": self.rce_daily_mean,
            "rcem_override": self.rcem_override,
        }

    def ingest_reading(
        self,
        kind: str,
        value_kwh: float,
        ts: datetime,
        resets_allowed: bool,
        max_power_kw: float,
    ) -> tuple[float, bool]:
        """Feed a cumulative meter reading; return (delta kWh, rejected).

        ``resets_allowed`` matches HA's ``total_increasing``: a drop means the
        meter restarted from zero. Otherwise a drop is treated as a glitch and
        energy is only counted above the highest value seen so far, so
        inverters that briefly report 0 do not double count.
        """
        meter = self.meters.get(kind)
        iso = ts.isoformat()
        if meter is None:
            self.meters[kind] = {"last": value_kwh, "high": value_kwh, "ts": iso}
            return 0.0, False

        last, high = meter["last"], meter["high"]
        if resets_allowed:
            delta = value_kwh if value_kwh < last else value_kwh - last
            high = value_kwh
        else:
            delta = max(0.0, value_kwh - high)
            high = max(high, value_kwh)

        hours = max(0.0, (ts - datetime.fromisoformat(meter["ts"])).total_seconds() / 3600)
        cap = max_power_kw * hours + 0.5
        rejected = delta > cap
        if rejected:
            delta = 0.0
            high = value_kwh
        meter.update(last=value_kwh, high=high, ts=iso)
        return delta, rejected

    def add_energy(self, kind: str, kwh: float, ts: datetime, day_key: str) -> None:
        """Record energy in its day and queue it for pricing."""
        if kwh <= 0:
            return
        field = "imp" if kind == IMPORT else "exp"
        self.days.setdefault(day_key, _empty_day())[field] += kwh
        self.totals[field] = self.totals.get(field, 0.0) + kwh
        slot = self.pending.setdefault(str(slot_start(ts)), {"day": day_key, "imp": 0.0, "exp": 0.0})
        slot[field] += kwh

    def settle(self, cost_fn: PriceFn, rev_fn: PriceFn, now: datetime) -> bool:
        """Price queued slots whose prices are known; return True if any changed."""
        changed = False
        for key in list(self.pending):
            slot = self.pending[key]
            start = datetime.fromtimestamp(int(key), tz=timezone.utc)
            day = self.days.setdefault(slot["day"], _empty_day())
            expired = now - start > PENDING_MAX_AGE

            for field, money, fn in (("imp", "cost", cost_fn), ("exp", "rev", rev_fn)):
                kwh = slot[field]
                if kwh <= 0:
                    continue
                price = fn(start)
                if price is not None:
                    day[money] += kwh * price
                    self.totals[money] = self.totals.get(money, 0.0) + kwh * price
                elif expired:
                    day[f"{field}_unpriced"] += kwh
                else:
                    continue
                slot[field] = 0.0
                changed = True

            if slot["imp"] <= 0 and slot["exp"] <= 0:
                del self.pending[key]
        return changed

    def record_rce_day(self, day_key: str, mean_rce: float) -> None:
        self.rce_daily_mean[day_key] = mean_rce

    def rcem(self, month_key: str) -> float | None:
        """RCEm for a month in PLN/kWh: override, else mean of daily RCE means."""
        if month_key in self.rcem_override:
            return self.rcem_override[month_key]
        values = [v for k, v in self.rce_daily_mean.items() if k.startswith(month_key)]
        if not values:
            return None
        return max(0.0, sum(values) / len(values) / 1000)

    def summary(self, prefix: str, sale_mode: str, multiplier: float = 1.0) -> dict[str, float]:
        """Totals for all days whose key starts with ``prefix`` (YYYY, YYYY-MM, YYYY-MM-DD)."""
        out = {k: 0.0 for k in ("imp", "exp", "cost", "rev", "imp_unpriced", "exp_unpriced")}
        for key, day in self.days.items():
            if not key.startswith(prefix):
                continue
            for field in ("imp", "exp", "cost", "imp_unpriced", "exp_unpriced"):
                out[field] += day.get(field, 0.0)
            if sale_mode == "rcem":
                rcem = self.rcem(key[:7])
                if rcem is None:
                    out["exp_unpriced"] += day.get("exp", 0.0)
                else:
                    out["rev"] += day.get("exp", 0.0) * rcem * multiplier
            else:
                # Hourly/15-min RCE revenue is stored without the multiplier,
                # so changing it in options applies retroactively.
                out["rev"] += day.get("rev", 0.0) * multiplier
        out["balance"] = out["rev"] - out["cost"]
        return out

    def prune(self, today_key: str) -> None:
        """Drop days and RCE means older than the retention window."""
        cutoff = (datetime.fromisoformat(today_key) - timedelta(days=self.retention_days)).date().isoformat()
        for store in (self.days, self.rce_daily_mean):
            for key in [k for k in store if k < cutoff]:
                del store[key]
