"""The OpenAI price table: load it, refuse it when stale or incomplete, price a usage object, size a reservation.

Amounts are integer micro-dollars (1 USD = 1,000,000 micros). A rate in USD per million tokens times a token count is
exactly micro-dollars, so every price here is an exact decimal product rounded up to a whole micro-dollar, and no cap
comparison depends on float rounding. Rounding up only ever overstates a cost or a reservation.

Pricing follows docs/neural/spec.md Appendix B:
- a usage object: ordinary input at the input rate, cache writes at the cache-write rate (12-hour writes at the 12-hour
  rate), cached input at the cached rate, output (which already includes reasoning tokens) at the output rate, all at
  the tier actually served. When a usage object does not report cache writes, all uncached input is priced at the
  cache-write rate, since any of it may have been written.
- a worst case: estimated input x 1.15 at the highest input rate the tier could bill (input or cache write), plus
  max_output_tokens at the output rate, at the tier requested.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PRICES_PATH = ROOT / "config" / "openai_prices.yaml"
MAX_AGE = timedelta(hours=24)
FUTURE_SKEW = timedelta(minutes=10)  # a verified_at later than now by more than this is a typo, not a fresh table
RATE_KEYS = ("input", "cached_input", "cache_write", "output")
WORST_CASE_INPUT_FACTOR = Decimal("1.15")

# The response's service_tier field, mapped to the price table's tier names. A tier missing here or from the table
# cannot be priced, and the runner then keeps the whole reservation as spent.
SERVED_TIERS = {"flex": "flex", "default": "standard", "standard": "standard", "batch": "batch",
                "priority": "fast", "fast": "fast", "ultrafast": "ultrafast", "scale": "scale"}
# The tier a job asks for, mapped to the request's service_tier value; Batch requests carry none.
REQUEST_TIERS = {"flex": "flex", "standard": "default", "batch": None}


class PriceError(RuntimeError):
    """The price table is stale, incomplete or malformed, or a usage object cannot be priced."""


def load(path: Path = PRICES_PATH) -> dict:
    try:
        table = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PriceError(f"no price table at {path}; verify prices first (docs/neural/spec.md Appendix A)") from None
    if not isinstance(table, dict) or not isinstance(table.get("models"), dict):
        raise PriceError(f"{path} has no models table")
    return table


def verified_at(table: dict) -> datetime:
    raw = table.get("verified_at")
    try:
        when = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        raise PriceError(f"price table verified_at {raw!r} is not an ISO date and time") from None
    if when.tzinfo is None:
        raise PriceError(f"price table verified_at {raw!r} has no time zone")
    return when


def age(table: dict, now: datetime | None = None) -> timedelta:
    return (now or datetime.now().astimezone()) - verified_at(table)


def check_fresh(table: dict, now: datetime | None = None, max_age: timedelta = MAX_AGE) -> None:
    """Refuse a table verified more than 24 hours ago (or dated in the future)."""
    how_old = age(table, now)
    if how_old > max_age:
        raise PriceError(f"price table verified {how_old.total_seconds() / 3600:.1f} hours ago (limit "
                         f"{max_age.total_seconds() / 3600:.0f}); re-verify the prices (docs/neural/spec.md Appendix A)")
    if how_old < -FUTURE_SKEW:
        raise PriceError(f"price table verified_at {table.get('verified_at')!r} is in the future")


def rates(table: dict, model: str, tier: str) -> dict[str, Decimal]:
    """The model's rates at the tier, as exact decimals in USD per million tokens. Refuses a missing or bad entry."""
    entry = (table.get("models") or {}).get(model)
    if not isinstance(entry, dict):
        raise PriceError(f"price table has no model {model!r}")
    row = entry.get(tier)
    if not isinstance(row, dict):
        raise PriceError(f"price table has no {tier!r} tier for {model!r}")
    out = {}
    for key in RATE_KEYS + ("cache_write_12h",):
        value = row.get(key)
        if value is None and key == "cache_write_12h":
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise PriceError(f"price table {model} {tier} {key} is {value!r}, not a rate")
        out[key] = Decimal(str(value))
    return out


def require(table: dict, uses, now: datetime | None = None) -> None:
    """A spending command's check: fresh, and every (model, tier) it would use is priced."""
    check_fresh(table, now)
    for model, tier in uses:
        rates(table, model, tier)


def micros(amount: Decimal) -> int:
    """Round an exact micro-dollar amount up to a whole micro-dollar."""
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def usd(amount_micros: int) -> float:
    return amount_micros / 1_000_000


def served_tier(service_tier: str | None) -> str | None:
    return SERVED_TIERS.get(service_tier or "")


def _count(usage: dict, *path: str) -> int | None:
    value = usage
    for key in path:
        if not isinstance(value, dict) or value.get(key) is None:
            return None
        value = value[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PriceError(f"usage {'.'.join(path)} is {value!r}, not a token count")
    return value


def usage_cost_micros(usage: dict, r: dict[str, Decimal]) -> int:
    """Price one usage object (the Responses API's `usage`) at the given rates."""
    if not isinstance(usage, dict):
        raise PriceError("response has no usage object")
    inp, out = _count(usage, "input_tokens"), _count(usage, "output_tokens")
    if inp is None or out is None:
        raise PriceError("usage lacks input_tokens or output_tokens")
    cached = _count(usage, "input_tokens_details", "cached_tokens") or 0
    writes = _count(usage, "input_tokens_details", "cache_write_tokens")
    if writes is None:  # writes not broken out: every uncached token may have been written
        uncached = inp - cached
        if uncached < 0:
            raise PriceError("usage reports more cached tokens than input tokens")
        return micros(uncached * r["cache_write"] + cached * r["cached_input"] + out * r["output"])
    w12 = _count(usage, "input_tokens_details", "cache_write_tokens_details", "ttl_12h_tokens") or 0
    w30 = writes - w12
    ordinary = inp - cached - writes
    if ordinary < 0 or w30 < 0:
        raise PriceError("usage token counts do not add up")
    if w12 and "cache_write_12h" not in r:
        raise PriceError("usage reports 12-hour cache writes but the table has no 12-hour rate")
    cost = (ordinary * r["input"] + cached * r["cached_input"] + w30 * r["cache_write"]
            + w12 * r.get("cache_write_12h", Decimal(0)) + out * r["output"])
    return micros(cost)


def worst_case_micros(est_input_tokens: int, max_output_tokens: int, r: dict[str, Decimal]) -> int:
    """The reservation for one request: no response at these rates can cost more, given the input estimate holds."""
    if est_input_tokens <= 0 or max_output_tokens <= 0:
        raise PriceError("a worst case needs positive input and output token counts")
    top_input = max(r["input"], r["cache_write"])
    return micros(Decimal(est_input_tokens) * WORST_CASE_INPUT_FACTOR * top_input + max_output_tokens * r["output"])


def expected_micros(est_input_tokens: int, expected_output_tokens: int, r: dict[str, Decimal]) -> int:
    """Appendix B's expected cost with no cache reads: uncached input at the cache-write rate, output at its rate."""
    return micros(Decimal(est_input_tokens) * r["cache_write"] + expected_output_tokens * r["output"])
