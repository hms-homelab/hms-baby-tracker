"""Parity tests for the contraction AI prompt builder (app.assessment.build_prompt).

Mirrors the n8n "Contraction AI Assessment" Build Prompt Code node: the <2-row
skip message, the gap math, the intensity map/labels/breakdown, and the exact
prompt wording. A fixed `now` keeps the localTime / data deterministic.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.assessment import build_prompt, NO_DATA_MSG

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 5, 28, 4, 0, 0, tzinfo=timezone.utc)


def _rows(*specs):
    """specs: list of (minutes_before_now, subtype)."""
    out = []
    for mins, sub in specs:
        out.append({
            "event_type": "contraction",
            "event_subtype": sub,
            "note": None,
            "logged_at": (NOW - timedelta(minutes=mins)).isoformat(),
        })
    return out


def test_skip_under_two():
    res = build_prompt(_rows((5, "contraction")), TZ, now=NOW)
    assert res["skip"] is True
    assert res["assessment"] == NO_DATA_MSG


def test_skip_empty():
    res = build_prompt([], TZ, now=NOW)
    assert res["skip"] is True
    assert res["assessment"] == NO_DATA_MSG


def test_gaps_and_unrated_breakdown():
    # 3 contractions at 0, 5, 15 min ago -> gaps 5 and 10 min.
    res = build_prompt(_rows((0, "contraction"), (5, "contraction"), (15, "contraction")),
                       TZ, now=NOW)
    assert res["skip"] is False
    p = res["prompt"]
    assert "3 contractions in last 2 hours" in p
    assert "Average gap: 7.5 minutes" in p
    assert "Average intensity: not rated/4 (n/a)" in p
    assert '"unrated": 3' in p
    assert "Shortest gap: 5.0 min, longest: 10.0 min" in p
    assert p.endswith("Max 2 sentences.")


def test_intensity_map_and_label():
    # intensities strong(3) + intense(4) -> avg 3.5 -> round() = 4 -> label 'intense'
    res = build_prompt(_rows((0, "strong"), (5, "intense"), (15, "mild")),
                       TZ, now=NOW)
    p = res["prompt"]
    assert "Average intensity: 2.7/4 (strong)" in p  # (3+4+1)/3 = 2.666 -> 2.7, label idx round(2.7)-1=2 -> 'strong'
    assert '"strong": 1' in p and '"intense": 1' in p and '"mild": 1' in p
