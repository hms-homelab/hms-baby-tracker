"""i18n tests (SDD-004).

The point of this file is that a volunteer's translation cannot quietly break
the app. In particular:

* a half-finished catalog fails CI instead of rendering blanks,
* an accent in a `device.*` string fails CI instead of reaching the Baby
  Remote's ASCII-only font as '??',
* a too-long `device.*` string fails CI instead of being silently clipped
  mid-word on a 21-column screen,
* a typo'd key in the HTML or JS fails CI, without standing up a JS test
  runner (the repo has none).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import display, i18n, summary
from app.config import Config
from app.main import create_app

WEB = Path(__file__).resolve().parent.parent / "web"
I18N = WEB / "i18n"

REGISTRY = json.loads((I18N / "index.json").read_text(encoding="utf-8"))
LOCALES = [e["code"] for e in REGISTRY]
NON_SOURCE = [c for c in LOCALES if c != "en"]

# Worst-case interpolations: the longest value each placeholder can take at
# runtime. `time` is scheduler's "%-I:%M %p", `ago`/`eta` are "<h>h<m>m".
SAMPLE = {"ago": "12h59m", "eta": "12h59m", "time": "11:59 PM", "what": "breast"}


def catalog(lang: str) -> dict:
    return json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))


def keys(cat: dict) -> set[str]:
    return {k for k in cat if not k.startswith("_")}


# --- 1. catalog completeness -------------------------------------------------

@pytest.mark.parametrize("lang", NON_SOURCE)
def test_catalog_is_complete(lang):
    """A locale must carry exactly the English key set. Missing keys would show
    English mid-sentence; extra keys are dead weight or a typo."""
    en, other = keys(catalog("en")), keys(catalog(lang))
    assert not (en - other), f"{lang}.json missing: {sorted(en - other)}"
    assert not (other - en), f"{lang}.json has unknown keys: {sorted(other - en)}"


@pytest.mark.parametrize("lang", LOCALES)
def test_no_empty_values(lang):
    cat = catalog(lang)
    # `time.ago` is legitimately empty in French ("il y a" leads the phrase
    # instead of trailing it), so only device/UI-critical keys are checked.
    blank = [k for k, v in cat.items()
             if not k.startswith("_") and k != "time.ago" and not str(v).strip()]
    assert not blank, f"{lang}.json has empty values: {blank}"


# --- 2 & 3. the Baby Remote's ASCII + 21-column wall -------------------------

def _worst_subtype(lang: str) -> str:
    return max((i18n.t(f"device.sub.{s}", lang) for s in ("breast", "bottle", "solid")),
               key=len)


@pytest.mark.parametrize("lang", LOCALES)
def test_device_strings_are_ascii_and_fit(lang):
    """Every device.* string, rendered with worst-case values, must fold to
    pure ASCII and fit the OLED's 21 columns.

    font5x7.h is 0x20..0x7F and oled.c fb_char() replaces anything else with
    '?'; overflow past column 21 is dropped without error.
    """
    sample = dict(SAMPLE, what=_worst_subtype(lang))
    too_long, non_ascii = [], []
    for key in (k for k in catalog(lang) if k.startswith("device.")):
        folded = i18n._tidy(i18n.ascii_fold(i18n.t(key, lang, None, **sample)))
        if not folded.isascii():
            non_ascii.append((key, folded))
        if len(folded) > i18n.DEVICE_MAX:
            too_long.append((key, len(folded), folded))
    assert not non_ascii, f"{lang}: non-ASCII after folding: {non_ascii}"
    assert not too_long, f"{lang}: over {i18n.DEVICE_MAX} chars: {too_long}"


# --- 4. ascii_fold ----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Tétée il y a 12h59m", "Tetee il y a 12h59m"),   # two accents, four bytes
    ("beëindigd", "beeindigd"),
    ("straße", "strasse"),                             # NFKD will not split this
    ("Ø ø æ œ ł", "O o ae oe l"),
    ("Última toma", "Ultima toma"),
    ("Weeën", "Weeen"),
    ("plain ascii", "plain ascii"),
])
def test_ascii_fold(raw, expected):
    assert i18n.ascii_fold(raw) == expected


def test_ascii_fold_strips_emoji():
    assert i18n.ascii_fold("🍼 Voeding").strip() == "Voeding"
    assert i18n._tidy(i18n.ascii_fold("🍼 Voeding")) == "Voeding"


def test_multibyte_accent_would_have_produced_two_marks():
    """Documents WHY the fold exists: without it each byte of a 2-byte accent
    fails the firmware's range check independently."""
    assert len("é".encode("utf-8")) == 2
    assert i18n.ascii_fold("é") == "e"


# --- 5. width fallback ------------------------------------------------------

def test_over_long_device_string_falls_back_to_english(tmp_path, caplog):
    """An overlong device value must yield the English string rather than a
    clipped word, and say so once in the log."""
    (tmp_path / "i18n").mkdir()
    (tmp_path / "i18n" / "nl.json").write_text(
        json.dumps({"device.pumpDue": "Kolven is nu echt heel erg nodig"}),
        encoding="utf-8")
    i18n.invalidate()
    i18n._warned.clear()
    out = i18n.device("device.pumpDue", "nl", tmp_path)
    assert out == "Pump due now"
    assert len(out) <= i18n.DEVICE_MAX
    i18n.invalidate()


def test_device_uses_translation_when_it_fits(tmp_path):
    i18n.invalidate()
    assert i18n.device("device.pumpDue", "nl") == "Kolven nu"
    i18n.invalidate()


# --- 6. t() behaviour -------------------------------------------------------

def test_interpolation():
    assert i18n.t("sum.slept", "en", None, total="3h 12m") == "Slept 3h 12m today"


def test_missing_key_falls_back_to_english():
    # `alert.fever` exists in en; ask for it in a locale that has no catalog.
    assert i18n.t("alert.fever", "zz") == "Fever"


def test_unknown_key_returns_the_key():
    assert i18n.t("no.such.key.at.all", "en") == "no.such.key.at.all"


# --- 7. source / catalog sync ----------------------------------------------

def test_every_key_used_in_source_exists():
    """Catches a typo'd key in index.html or app.js with no JS test runner."""
    en = keys(catalog("en"))
    js = (WEB / "app.js").read_text(encoding="utf-8")
    ed = (WEB / "editor.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r'\bt\(\s*"([^"]+)"', js + ed))
    used |= set(re.findall(r'data-i18n(?:-placeholder|-title|-aria)?="([^"]+)"', html))
    # Plural bases resolve through key_one/key_other; "supplyCat." is built by
    # concatenation at the call site.
    plural_bases = {k.rsplit("_", 1)[0] for k in en if k.endswith(("_one", "_other"))}
    missing = {u for u in used
               if u not in en and u not in plural_bases and not u.endswith(".")}
    assert not missing, f"keys used in source but absent from en.json: {sorted(missing)}"


def test_no_orphaned_catalog_keys():
    """Every English key must be reachable from the SPA, the editor or Python."""
    en = keys(catalog("en"))
    blob = "".join((WEB / f).read_text(encoding="utf-8")
                   for f in ("app.js", "editor.js", "index.html"))
    py = "".join((Path(__file__).resolve().parent.parent / "app" / f).read_text(encoding="utf-8")
                 for f in ("display.py", "scheduler.py", "i18n.py"))
    orphans = []
    for k in sorted(en):
        if k.endswith(("_one", "_other")):
            continue
        if f'"{k}"' in blob or f'"{k}"' in py:
            continue
        # Built by concatenation: "supplyCat." + c, "device.sub." + subtype.
        if k.startswith(("supplyCat.", "device.sub.")):
            continue
        orphans.append(k)
    assert not orphans, f"catalog keys nothing references: {orphans}"


# --- 8, 9, 10. the editor's override layer ----------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MQTT_ENABLED", "0")
    i18n.invalidate()
    cfg = Config.load()
    cfg.mqtt_enabled = False
    with TestClient(create_app(cfg)) as c:
        yield c
    i18n.invalidate()


def test_override_precedence(tmp_path):
    """en shipped < lang shipped < lang override."""
    (tmp_path / "i18n").mkdir()
    (tmp_path / "i18n" / "nl.json").write_text(
        json.dumps({"sum.asleep": "Pitten"}), encoding="utf-8")
    i18n.invalidate()
    merged = i18n.merged("nl", tmp_path)
    assert merged["sum.asleep"] == "Pitten"          # override wins
    assert merged["sum.awake"] == "Wakker"           # shipped nl survives
    assert "editor.title" in merged                  # en base still present
    i18n.invalidate()


def test_catalog_endpoint_reports_layers(client):
    r = client.get("/api/i18n/catalog?lang=nl")
    assert r.status_code == 200
    rows = {row["key"]: row for row in r.json()["rows"]}
    assert rows["sum.asleep"]["en"] == "Asleep"
    assert rows["sum.asleep"]["shipped"] == "Slaapt"
    assert rows["sum.asleep"]["override"] is None
    assert rows["device.pumpDue"]["is_device"] is True
    assert rows["device.pumpDue"]["limit"] == i18n.DEVICE_MAX
    assert rows["sum.asleep"]["is_device"] is False


def test_catalog_rejects_unknown_language(client):
    assert client.get("/api/i18n/catalog?lang=zz").status_code == 400


def test_save_rejects_unknown_key(client):
    r = client.put("/api/i18n/nl", json={"overrides": {"totally.made.up": "x"}})
    assert r.status_code == 400
    assert r.json()["error"] == "unknown_key"


def test_save_rejects_overlong_device_string(client):
    """The browser blocks Save past 21; this is the guarantee behind it."""
    r = client.put("/api/i18n/nl", json={
        "overrides": {"device.pumpDue": "Kolven is nu echt heel erg nodig"}})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "device_too_long"
    assert body["key"] == "device.pumpDue"
    assert body["length"] > i18n.DEVICE_MAX


def test_accented_device_string_is_accepted_and_folded(client, tmp_path):
    """An accent is not an error: it folds. What matters is that what reaches
    the remote is pure ASCII, since its font would render 'ú' as two '?'."""
    r = client.put("/api/i18n/nl", json={"overrides": {"device.pumpDue": "Kolven nú"}})
    assert r.status_code == 200
    i18n.invalidate()
    out = i18n.device("device.pumpDue", "nl", tmp_path)
    assert out == "Kolven nu"
    assert out.isascii()


def test_save_then_read_back(client, tmp_path):
    r = client.put("/api/i18n/nl", json={"overrides": {"sum.asleep": "Pitten"}})
    assert r.status_code == 200 and r.json()["saved"] == 1
    rows = {row["key"]: row for row in client.get("/api/i18n/catalog?lang=nl").json()["rows"]}
    assert rows["sum.asleep"]["override"] == "Pitten"
    assert rows["sum.asleep"]["effective"] == "Pitten"


def test_revert_single_key_and_all(client):
    client.put("/api/i18n/nl", json={"overrides": {"sum.asleep": "Pitten",
                                                   "sum.awake": "Wakker!"}})
    client.delete("/api/i18n/nl?key=sum.asleep")
    rows = {r["key"]: r for r in client.get("/api/i18n/catalog?lang=nl").json()["rows"]}
    assert rows["sum.asleep"]["override"] is None
    assert rows["sum.awake"]["override"] == "Wakker!"
    client.delete("/api/i18n/nl")
    rows = {r["key"]: r for r in client.get("/api/i18n/catalog?lang=nl").json()["rows"]}
    assert rows["sum.awake"]["override"] is None


def test_export_round_trips_into_the_repo(client):
    """The contributor's whole path: edit, Export, drop the file into web/i18n/.
    The exported file must pass the completeness check in test 1."""
    client.put("/api/i18n/nl", json={"overrides": {"sum.asleep": "Pitten"}})
    exported = client.get("/api/i18n/nl/export").json()
    assert keys(exported) == keys(catalog("en"))
    assert exported["sum.asleep"] == "Pitten"
    assert exported["sum.awake"] == "Wakker"


# --- 11. registry integrity -------------------------------------------------

def test_registry_entries_are_complete():
    for e in REGISTRY:
        for field in ("code", "name", "english_name", "flag", "status"):
            assert e.get(field), f"{e.get('code')} missing {field}"
        assert e["status"] in ("source", "machine", "human")
        assert (I18N / f"{e['code']}.json").is_file()


def test_english_is_the_only_source():
    assert [e["code"] for e in REGISTRY if e["status"] == "source"] == ["en"]


# --- device rows + AI prompt ------------------------------------------------

def test_build_rows_defaults_to_english():
    rows = display.build_rows("2026-08-09T00:00:00+00:00", None, 120,
                              __import__("datetime").datetime(
                                  2026, 8, 9, 12, 59,
                                  tzinfo=__import__("datetime").timezone.utc))
    assert rows["l1"] == "Feed 12h59m ago"
    assert rows["l2"] == "Pump: --"


def test_device_lang_resolves_auto_to_english():
    cfg = Config()
    cfg.language = "auto"
    assert display.device_lang(cfg) == "en"
    cfg.language = "nl"
    assert display.device_lang(cfg) == "nl"


def _digest() -> dict:
    """Minimal digest shaped like summary.build_digest()'s output."""
    return {
        "feeds_by": {"bottle": 4}, "feeds_today": 4, "avg_feed_gap_min": 180,
        "diapers_today": 6, "sleep_today": "3h 12m", "is_sleeping": False,
        "pumps_today": 2, "baths_today": 1, "tummy_today": 3, "medicines_today": 0,
        "contractions_today": 0, "last_temp": None, "growth": {}, "days_3": [],
    }


def test_summary_prompt_is_unchanged_for_english():
    """A default install must send a byte-identical prompt to the pre-i18n
    releases: the language line is appended only for non-English."""
    cfg = Config()
    cfg.language = "auto"
    assert "Respond in" not in summary.build_prompt(cfg, _digest())


def test_summary_prompt_appends_language():
    cfg = Config()
    cfg.language = "fr"
    prompt = summary.build_prompt(cfg, _digest())
    assert "Respond in French." in prompt
    # Appended, never substituted: the configured body survives intact.
    assert "do not use em-dashes" in prompt
