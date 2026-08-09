"""Server-side i18n (SDD-004).

Shares the SAME catalogs as the web UI (`web/i18n/*.json`), so a translator
edits one file per language and both surfaces follow. On top of the shipped
catalogs sits an optional per-install override layer written by the in-app
editor, which lives in `<data_dir>/i18n/` so it survives add-on updates.

Two consumers, two very different tolerances:

* `t()`   -> Home Assistant notifications. Full Unicode, emoji fine.
* `device()` -> the Baby Remote's OLED. That firmware ships a 5x7 ASCII font
  (`font5x7.h`: FONT_FIRST 0x20, FONT_LAST 0x7F) and `fb_char()` substitutes
  '?' for anything outside it. Because payloads are UTF-8, a single accented
  letter is TWO bytes and each byte fails the range check on its own, so
  "Tetee" spelled with accents would arrive as "T??t??e". Rows are 21 columns
  (128px / 6px cell) and overflow is silently dropped, not wrapped.

So device strings are folded to ASCII and gated at 21 characters, falling back
to English per key rather than shipping a clipped word to a glanceable screen.
"""
from __future__ import annotations

import json
import logging
import unicodedata
from pathlib import Path

log = logging.getLogger("baby.i18n")

I18N_DIR = Path(__file__).resolve().parent.parent / "web" / "i18n"

#: Baby Remote OLED row width, in characters. See the module docstring.
DEVICE_MAX = 21

#: Characters NFKD does not decompose into base + combining mark. Without these
#: step 3 would simply delete them ("strasse" would become "strae").
_FOLD_MAP = {
    "ß": "ss", "ẞ": "SS",
    "ø": "o", "Ø": "O",
    "æ": "ae", "Æ": "AE",
    "œ": "oe", "Œ": "OE",
    "đ": "d", "Đ": "D",
    "ł": "l", "Ł": "L",
    "ð": "d", "Ð": "D",
    "þ": "th", "Þ": "TH",
    "ı": "i", "·": " ", "–": "-", "—": "-",
    "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...",
}

_cache: dict[str, dict] = {}
_registry: list[dict] | None = None
_warned: set[str] = set()


def _read(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:  # malformed override must not take the add-on down
        log.warning("i18n: could not read %s: %s", path, e)
        return {}


def registry() -> list[dict]:
    """The `index.json` locale registry (code / name / english_name / flag /
    status / credit)."""
    global _registry
    if _registry is None:
        try:
            with (I18N_DIR / "index.json").open(encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            log.warning("i18n: could not read index.json: %s", e)
            data = []
        _registry = data if isinstance(data, list) else []
    return _registry


def entry(lang: str) -> dict | None:
    for e in registry():
        if e.get("code") == lang:
            return e
    return None


def english_name(lang: str) -> str:
    e = entry(lang)
    return (e or {}).get("english_name") or "English"


def available() -> list[str]:
    return [e.get("code") for e in registry() if e.get("code")]


def override_dir(data_dir) -> Path:
    return Path(data_dir) / "i18n"


def shipped(lang: str) -> dict:
    """The catalog baked into the image, untouched by the editor."""
    return _read(I18N_DIR / f"{lang}.json")


def overrides(lang: str, data_dir) -> dict:
    """The editor's per-install edits for `lang`."""
    return _read(override_dir(data_dir) / f"{lang}.json")


def merged(lang: str, data_dir=None) -> dict:
    """en shipped < lang shipped < lang override.

    English is always the base so a partial translation degrades to English
    rather than to a missing key.
    """
    out = dict(shipped("en"))
    if lang and lang != "en":
        out.update(shipped(lang))
    if data_dir is not None:
        out.update(overrides(lang, data_dir))
    return out


def load(lang: str, data_dir=None) -> dict:
    key = f"{lang}:{data_dir}"
    if key not in _cache:
        _cache[key] = merged(lang, data_dir)
    return _cache[key]


def invalidate() -> None:
    """Drop the memo cache. Called after the editor writes an override."""
    _cache.clear()


def _fmt(template: str, **vars) -> str:
    out = template
    for name, val in vars.items():
        out = out.replace("{" + name + "}", str(val))
    return out


def t(key: str, lang: str = "en", data_dir=None, **vars) -> str:
    """Translate, falling back to English then to the key itself."""
    cat = load(lang, data_dir)
    val = cat.get(key)
    if val is None:
        val = shipped("en").get(key)
    if val is None:
        if key not in _warned:
            _warned.add(key)
            log.warning("i18n: missing key %s", key)
        return key
    return _fmt(val, **vars)


def ascii_fold(s: str) -> str:
    """Reduce `s` to something the Baby Remote's 5x7 ASCII font can render.

    1. Explicit map for characters NFKD will not decompose (ss / o / ae / oe).
    2. NFKD, then drop combining marks, so 'e' with an acute becomes 'e'.
    3. Drop anything still above 0x7F. This also strips emoji, which the OLED
       cannot draw at all.
    """
    for src, dst in _FOLD_MAP.items():
        s = s.replace(src, dst)
    decomposed = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c for c in stripped if ord(c) <= 0x7F)


def _tidy(s: str) -> str:
    """Collapse the whitespace a fold can leave behind.

    Dropping an emoji leaves the space that separated it, so "<emoji> Voeding"
    would fold to " Voeding" and waste a column on a 21-wide row.
    """
    return " ".join(s.split())


def fits_device(text: str) -> bool:
    folded = _tidy(ascii_fold(text))
    return folded.isascii() and len(folded) <= DEVICE_MAX


def device(key: str, lang: str = "en", data_dir=None, **vars) -> str:
    """A `device.*` string, folded to ASCII and gated at 21 characters.

    Over budget, the English string is used FOR THE DEVICE ONLY; the web UI
    still shows the full translation. A remote that silently drops the last
    word is worse than one that reads English.
    """
    folded = _tidy(ascii_fold(t(key, lang, data_dir, **vars)))
    if len(folded) <= DEVICE_MAX:
        return folded
    warn_key = f"{lang}:{key}"
    if warn_key not in _warned:
        _warned.add(warn_key)
        log.warning(
            "i18n: %s in '%s' is %d chars after folding (max %d) — "
            "falling back to English on the device",
            key, lang, len(folded), DEVICE_MAX,
        )
    return _tidy(ascii_fold(t(key, "en", None, **vars)))
