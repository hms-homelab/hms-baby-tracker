"""Regression: numeric readings must not leak single-precision float noise.

The Postgres archive stores `value` as `real` (4-byte single precision), so a
clean 100.8 comes back as 100.80000305175781. Rendered raw that was showing 8-9
junk digits after the decimal in the UI/notifications. `_round_value` trims it
on read for every consumer (web, MQTT, title/message, summary)."""
from app.db import _round_value, _clean_row


def test_round_value_kills_single_precision_noise():
    # exactly what asyncpg returns from a `real` column holding 100.8
    assert _round_value(100.80000305175781) == 100.8
    assert str(_round_value(100.80000305175781)) == "100.8"
    # weight (kg) keeps its two real decimals
    assert _round_value(4.550000190734863) == 4.55


def test_round_value_leaves_clean_values_and_non_floats():
    assert _round_value(37.1) == 37.1
    assert _round_value(100.0) == 100.0
    assert _round_value(None) is None
    assert _round_value("n/a") == "n/a"  # value_unit / text columns untouched


def test_clean_row_only_touches_value():
    row = {"event_type": "temperature", "value": 100.80000305175781, "value_unit": "°F"}
    cleaned = _clean_row(row)
    assert cleaned["value"] == 100.8
    assert cleaned["value_unit"] == "°F"
    # rows without a numeric reading pass through unchanged
    assert _clean_row({"event_type": "feed", "value": None}) == {"event_type": "feed", "value": None}
