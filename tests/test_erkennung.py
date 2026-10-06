"""Tests für Zuschnitt, Kandidatensuche, Bildvergleich und die Claude-Code-Anbindung (ohne Internet)."""
import json
import os
import stat
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest

from pokeauktion import claude_code
from pokeauktion.analyse import Einstellungen, analysiere_foto
from pokeauktion.bildvergleich import Referenzbilder, aehnlichkeit
from pokeauktion.db import verbinde
from pokeauktion.erkennung import Indizes, Merkmale, erkenne_aus_merkmalen, merkmale_aus, OcrZeile, _kp_aus
from pokeauktion.sync import karte_speichern
from pokeauktion.zuschnitt import finde_karten

TESTFOTOS = Path(__file__).resolve().parent.parent / "testfotos"


def _karte(con, id_, sprache, name, kp, attacken, seltenheit="Common", bild=None):
    karte_speichern(con, sprache, {
        "id": id_, "localId": id_.split("-")[1], "name": name, "hp": kp, "rarity": seltenheit,
        "category": "Pokemon", "set": {"id": id_.split("-")[0], "name": id_.split("-")[0]}, "image": bild,
        "attacks": [{"name": a, "damage": d} for a, d in attacken],
    }, basis_ueberschreiben=True)


@pytest.fixture
def con(tmp_path):
    con = verbinde(tmp_path / "t.sqlite")
    # Murkrow: drei Drucke mit gleichem Text (normal, Shiny, Promo) + ein anderer Murkrow
    for id_, s in (("sv02-131", "Common"), ("sv04.5-181", "Shiny rare"), ("svp-021", "Promo")):
        _karte(con, id_, "en", "Murkrow", 60, [("Spin Turn", "10"), ("United Wings", "20×")], s,
               bild=f"https://bilder.test/{id_}")
    _karte(con, "swsh11-114", "en", "Murkrow", 60, [("Peck", "10"), ("Wing Attack", "30")])
    # Gleicher Name in Englisch und Indonesisch
    _karte(con, "sv03-027", "en", "Charmeleon", 90, [("Heat Tackle", "70")])
    _karte(con, "SV3s-014", "id", "Charmeleon", 90, [("Heat Tackle", "70")])
    _karte(con, "sv03-125", "de", "Glurak-ex", 330, [("Brennende Finsternis", "180+")])
    _karte(con, "sv03-125", "en", "Charizard ex", 330, [("Burning Darkness", "180+")])
    con.commit()
    return con


def m(namen=(), kp=(), attacken=(), zahlen=()):
    return Merkmale([], list(namen), set(kp), list(attacken), set(zahlen))


def test_kp_aus_ocr_fehlern():
    assert 140 in _kp_aus("1400")      # Energiesymbol als 0 gelesen
    assert 220 in _kp_aus("2206")
    assert 70 in _kp_aus("70@")
    assert _kp_aus("KP") == set()


def test_merkmale_aus_zeilen():
    zeilen = [OcrZeile("BASIC", 0.9, 0.05, 0.2, 0.02), OcrZeile("Charmeleon", 0.95, 0.06, 0.5, 0.03),
              OcrZeile("90", 0.9, 0.06, 0.8, 0.03), OcrZeile("Heat Tackle", 0.9, 0.62, 0.4, 0.03)]
    merk = merkmale_aus(zeilen)
    assert "charmeleon" in merk.namen and merk.kp == {90} and merk.attacken == ["heattackle"]


def test_eindeutige_karte_hoch_und_seltene_sprache_nachrangig(con):
    e = erkenne_aus_merkmalen(con, Indizes.laden(con), m(["charmeleon"], [90], ["heattackle"]), None, set())
    assert e.sicherheit == "hoch" and e.sprache == "en" and [k.id for k in e.karten] == ["sv03-027"]


def test_gleicher_text_ergibt_alle_drucke_mit_spanne(con):
    e = erkenne_aus_merkmalen(con, Indizes.laden(con), m(["murkrow"], [60], ["spinturn", "unitedwings"]), None, set())
    assert e.sicherheit == "mittel"
    assert sorted(k.id for k in e.karten) == ["sv02-131", "sv04.5-181", "svp-021"]


def test_ocr_tippfehler_im_namen(con):
    e = erkenne_aus_merkmalen(con, Indizes.laden(con), m(["murkrovv"], [60], ["peck"]), None, set())
    assert e.karten[0].id == "swsh11-114"


def test_name_unlesbar_attacke_und_kp(con):
    e = erkenne_aus_merkmalen(con, Indizes.laden(con), m([], [330], ["brennendefinst"]), None, set())
    assert e.karten and e.karten[0].id == "sv03-125" and e.sprache == "de"
    assert "über Attacken" in e.begruendung


def test_nichts_lesbar(con):
    e = erkenne_aus_merkmalen(con, Indizes.laden(con), m(), None, set())
    assert e.sicherheit == "nicht erkannt" and not e.karten


def test_besonderheit_shiny_grenzt_ein_und_merkt_guenstigere(con):
    merk = m(["murkrow"], [60], ["spinturn", "unitedwings"])
    e = erkenne_aus_merkmalen(con, Indizes.laden(con), merk, None, set(), besonderheit="shiny")
    assert [k.id for k in e.karten] == ["sv04.5-181"]
    assert {k.id for k in e.ausgeschlossen} == {"sv02-131", "svp-021"}
    e = erkenne_aus_merkmalen(con, Indizes.laden(con), merk, None, set(), besonderheit="normal")
    assert "sv04.5-181" not in [k.id for k in e.karten]


@pytest.mark.skipif(not (TESTFOTOS / "foto1.webp").exists(), reason="keine Testfotos")
def test_zuschnitt_ordnerseiten():
    for n in (1, 2, 3):
        ausschnitte = finde_karten(cv2.imread(str(TESTFOTOS / f"foto{n}.webp")))
        assert sorted((a.reihe, a.spalte) for a in ausschnitte) == [(r, s) for r in (1, 2, 3) for s in (1, 2, 3)]


def _testkarte(farbe, muster):
    rng = np.random.default_rng(muster)
    bild = np.full((340, 245, 3), farbe, np.uint8)
    for _ in range(60):
        x, y = rng.integers(0, 230), rng.integers(0, 320)
        cv2.circle(bild, (int(x), int(y)), int(rng.integers(3, 15)), tuple(int(c) for c in rng.integers(0, 255, 3)), -1)
    return bild


def test_bildvergleich_findet_gleiches_bild_und_unterscheidet_farbe():
    normal, shiny = _testkarte((40, 40, 160), 1), _testkarte((160, 120, 40), 1)
    foto = cv2.GaussianBlur(cv2.resize(normal, (300, 420)), (5, 5), 0)
    assert aehnlichkeit(foto, normal) > aehnlichkeit(foto, shiny) + 0.1


def test_referenzbilder_mit_cache(con, tmp_path):
    bilder = {"sv02-131": _testkarte((40, 40, 160), 1), "sv04.5-181": _testkarte((160, 120, 40), 2),
              "svp-021": _testkarte((40, 160, 40), 3)}
    abrufe = []

    def handler(anfrage):
        abrufe.append(anfrage.url.path)
        karte = anfrage.url.path.split("/")[1]
        return httpx.Response(200, content=cv2.imencode(".webp", bilder[karte])[1].tobytes())

    ref = Referenzbilder(con, tmp_path / "bilder", httpx.Client(transport=httpx.MockTransport(handler)))
    foto = cv2.resize(bilder["sv04.5-181"], (260, 360))
    auswahl, notiz = ref.vergleiche(foto, list(bilder), "en")
    assert auswahl == ["sv04.5-181"] and "eindeutig" in notiz
    ref.vergleiche(foto, list(bilder), "en")
    assert len(abrufe) == 3  # zweiter Durchlauf aus dem Cache


def test_claude_antwort_auswerten():
    huelle = {"type": "result", "is_error": False, "result": "ok",
              "structured_output": {"name": "Murkrow", "sprache": "en", "kp": 60, "lesbar": True,
                                    "attacken": [{"name": "Spin Turn", "schaden": "10"}], "besonderheit": "shiny"}}
    antwort = claude_code.auswerten(json.dumps(huelle))
    merk = claude_code.als_merkmale(antwort)
    assert merk.namen == ["murkrow"] and merk.kp == {60} and merk.attacken == ["spinturn"]
    text = {"result": 'Hier: {"name": "Glurak-ex", "sprache": "de", "lesbar": true}'}
    assert claude_code.auswerten(json.dumps(text))["name"] == "Glurak-ex"
    assert claude_code.auswerten(json.dumps({"is_error": True, "result": "limit"})) is None


@pytest.mark.skipif(not (TESTFOTOS / "foto3.webp").exists(), reason="keine Testfotos")
def test_analyse_mit_falschem_claude_befehl(con, tmp_path):
    """Claude Code wird durch ein Skript ersetzt, das immer 'Murkrow, Shiny' antwortet."""
    skript = tmp_path / "claude"
    antwort = {"structured_output": {"name": "Murkrow", "sprache": "en", "kp": 60, "lesbar": True,
                                     "attacken": [{"name": "Spin Turn"}, {"name": "United Wings"}],
                                     "besonderheit": "shiny"}}
    skript.write_text("#!/bin/sh\ncat <<'ENDE'\n" + json.dumps(antwort) + "\nENDE\n")
    skript.chmod(skript.stat().st_mode | stat.S_IEXEC)
    foto = analysiere_foto(con, Indizes.laden(con), cv2.imread(str(TESTFOTOS / "foto3.webp")), None,
                           Einstellungen(claude_modus="immer", claude_befehl=str(skript)))
    murkrow = [k.ergebnis for k in foto.karten if (k.ausschnitt.reihe, k.ausschnitt.spalte) == (2, 3)][0]
    assert murkrow.quelle == "claude" and [k.id for k in murkrow.karten] == ["sv04.5-181"]


def test_gleicher_name_und_kp_mit_teilweise_gelesenen_attacken_zeigt_beide(con):
    """Nur 'Hinterhalt' gelesen: alte und neue Kramurx-Karte sind beide möglich (bis der Bildvergleich entscheidet)."""
    _karte(con, "xy4-51", "de", "Kramurx", 60, [("Hinterhalt", "10+"), ("Flügelschlag", "30")])
    _karte(con, "me02-057", "de", "Kramurx", 60, [("Hinterhalt", "10+")])
    con.commit()
    merk = m(["kramurx"], [60], ["hinterhalt"])
    e = erkenne_aus_merkmalen(con, Indizes.laden(con), merk, None, set())
    assert sorted(k.id for k in e.karten) == ["me02-057", "xy4-51"] and e.sicherheit == "mittel"

    def bildvergleich(bild, ids, sprache):
        return ["me02-057"], "Bildvergleich eindeutig"
    e = erkenne_aus_merkmalen(con, Indizes.laden(con), merk, None, set(), bildvergleich)
    assert [k.id for k in e.karten] == ["me02-057"]


def test_versionen_ohne_nullpreise_und_doppelte_produkte(con):
    from pokeauktion.katalog import versionen
    con.execute("UPDATE karten SET varianten = ?, varianten_detail = ? WHERE id = 'sv04.5-181'",
                (json.dumps({"holo": True}), json.dumps([{"variantId": "holo", "type": "holo"}])))
    for variante, produkt, trend, trend_holo in (("", 1, 3.39, 0), ("holo", 1, 3.39, 0)):
        con.execute("INSERT INTO preise (karte_id, variante, id_produkt, trend, trend_holo, abgerufen) "
                    "VALUES ('sv04.5-181', ?, ?, ?, ?, 'x')", (variante, produkt, trend, trend_holo))
    assert [(v.bezeichnung, v.trend) for v in versionen(con, "sv04.5-181")] == [("Holo", 3.39)]


def test_karte_mit_falschen_kp_ist_keine_alternative(con):
    _karte(con, "swsh6-104", "de", "Kleoparda V", 190, [("Schattenreißer", "110")])
    _karte(con, "xy4-57", "de", "Kleoparda", 90, [("Kratzer", "20")])
    con.commit()
    e = erkenne_aus_merkmalen(con, Indizes.laden(con), m(["kleoparday"], [190], ["schattenreiser"]), None, set())
    assert [k.id for k in e.karten] == ["swsh6-104"]
