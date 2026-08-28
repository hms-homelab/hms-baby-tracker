"""One definition of the wall clock (SDD-006).

Every time string the app prints goes through `clock()`: the journal's `time`
field (`db.py`), Home Assistant notifications (`ingest.py`), pump/feed reminders
(`scheduler.py`), the AI summary's MQTT payload (`summary.py`) and the
contraction assessment (`assessment.py`). They used to each carry their own
copy of the AM/PM arithmetic, which is how `assessment.py` ended up with a
leading zero the other four had already dropped (issue #2).

The format is an add-on option rather than a browser setting because the journal
time is built HERE, server-side, and arrives at the SPA already formatted. See
SDD-006 §3.4.
"""
from __future__ import annotations

import datetime as dt

TWELVE = "12h"
TWENTY_FOUR = "24h"
VALID = (TWELVE, TWENTY_FOUR)


def clock(d: dt.datetime, fmt: str = TWELVE) -> str:
    """Wall clock of an ALREADY-LOCALIZED datetime.

    12h -> "8:30 PM"  (no leading zero on the hour, issue #2)
    24h -> "20:30"    (zero-padded, the convention the format implies)

    Anything other than "24h" renders 12h, so a hand-edited options.json cannot
    break the journal.
    """
    if fmt == TWENTY_FOUR:
        return f"{d.hour:02d}:{d.minute:02d}"
    return f"{d.hour % 12 or 12}:{d.minute:02d} {'PM' if d.hour >= 12 else 'AM'}"


def normalize(fmt: str | None) -> str:
    """Coerce an option value to a known format (unknown/blank -> 12h)."""
    return fmt if fmt in VALID else TWELVE
