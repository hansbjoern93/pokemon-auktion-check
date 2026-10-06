"""Tests für Preis-Übernahme (Lesezeichen) und Weboberfläche."""
import importlib
from pathlib import Path

import pytest

from pokeauktion import preise_de

TESTFOTOS = Path(__file__).resolve().parent.parent / "testfotos"

# Nachgebaut nach dem Aufbau der Cardmarket-Seite aus deinem Screenshot (Seitentext, wie ihn der Browser liefert)
SEITE = """Mega-Glurak X ex
Reprints Zeige Reprints (16) Angebote zeigen
Verfügbare Artikel 2404
ab 1,49 €
Preis-Trend 2,85 €
Filter
Verkäuferstatus
Verkäufer
Produktinfo
Angebot
2K PoPaTCG
NM
1,99 €
1
32 Paddelboot122
NM
2,00 €
1"""


def test_erster_preis_unter_der_tabelle_nicht_ab_preis():
    assert preise_de.erster_preis(SEITE) == 1.99
    assert preise_de.erster_preis("Produktinfo\nNM\n1.013,10 €") == 1013.10
    assert preise_de.erster_preis("Product Information\nNM\n1,013.10 €") == 1013.10
    assert preise_de.erster_preis("ab 1,49 € ohne Tabelle") is None


def test_filter():
    assert preise_de.filter_aktiv("https://www.cardmarket.com/de/Pokemon/Products/Singles/X/Y?sellerCountry=7&minCondition=4")
    assert not preise_de.filter_aktiv("https://www.cardmarket.com/de/Pokemon/Products/Singles/X/Y")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tests.test_erkennung import _karte
    from pokeauktion.db import verbinde

    db = tmp_path / "karten.sqlite"
    con = verbinde(db)
    for id_, s in (("sv02-131", "Common"), ("sv04.5-181", "Shiny rare"), ("svp-021", "Promo")):
        _karte(con, id_, "en", "Murkrow", 60, [("Spin Turn", "10"), ("United Wings", "20×")], s)
    _karte(con, "me02-058", "de", "Kramshef", 130, [("Wind der Finsternis", "30"), ("Präzisionsfedern", None)])
    for karte, low in (("sv02-131", 0.05), ("sv04.5-181", 1.10), ("svp-021", 0.40), ("me02-058", 0.02)):
        con.execute("INSERT INTO preise (karte_id, variante, low, trend, abgerufen) VALUES (?, '', ?, ?, 'x')",
                    (karte, low, low * 2))
    con.commit()
    monkeypatch.setenv("DB_PFAD", str(db))
    monkeypatch.setenv("ERKENNUNG_CLAUDE", "aus")
    from pokeauktion import config, web
    importlib.reload(config)
    importlib.reload(web)
    return TestClient(web.app)


@pytest.mark.skipif(not (TESTFOTOS / "foto3.webp").exists(), reason="keine Testfotos")
def test_foto_bis_fester_gesamtwert(client):
    with open(TESTFOTOS / "foto3.webp", "rb") as f:
        d = client.post("/api/auswerten", files=[("fotos", ("foto3.webp", f.read(), "image/webp"))]).json()
    assert d["anzahl"] == 9 and len(d["fotos"]) == 1
    murkrow = next(z for z in d["zeilen"] if z["karten"] and z["karten"][0]["name"] == "Murkrow")
    # Druck unsicher (kein Bildvergleich im Test): gerechnet wird mit dem billigsten "ab"-Preis
    assert len(murkrow["karten"]) == 3 and murkrow["wert"] == 0.05 and murkrow["wert_quelle"] == "ab"

    # Gleiche Fotos noch einmal: keine neue Auswertung
    with open(TESTFOTOS / "foto3.webp", "rb") as f:
        assert client.post("/api/auswerten", files=[("fotos", ("x.webp", f.read(), "image/webp"))]).json()["id"] == d["id"]

    # Druck von Hand wählen: Shiny
    d = client.post(f"/api/auswertung/{d['id']}/korrektur",
                    json={"zeile": murkrow["zeile"], "karte_id": "sv04.5-181", "karte_aendern": True}).json()
    murkrow = next(z for z in d["zeilen"] if z["zeile"] == murkrow["zeile"])
    assert murkrow["wert"] == 1.10 and murkrow["von_hand"]

    # Erweiterung: Aufträge anlegen (je möglichem Druck einer, ohne von Hand gewählte Karten doppelt)
    start = client.post(f"/api/auswertung/{d['id']}/holen").json()
    assert start["anzahl"] >= 2 and "#pka=" in start["erste_url"] and "sellerCountry=7" in start["erste_url"]
    auftrag_id = int(start["erste_url"].split("#pka=")[1])
    a = client.get(f"/api/auftrag/{auftrag_id}").json()
    assert a["kuerzel"] == "" or a["kuerzel"]  # Testdaten haben keine Set-Kürzel
    # ungefilterte Seite wird abgelehnt, gefilterte Seite liefert den ersten Preis unter "Produktinfo"
    url = "https://www.cardmarket.com/de/Pokemon/Products/Singles/X/Y?sellerCountry=7&minCondition=4"
    r = client.post(f"/api/auftrag/{auftrag_id}/ergebnis", json={"url": url.split("?")[0], "text": SEITE}).json()
    assert r["fehler"] and r["naechste_url"]
    # alle offenen Aufträge abarbeiten wie die Erweiterung
    while (naechste := r["naechste_url"]):
        auftrag_id = int(naechste.split("#pka=")[1])
        r = client.post(f"/api/auftrag/{auftrag_id}/ergebnis", json={"url": url, "text": SEITE}).json()
        assert r["preis"] == 1.99
    assert r["app_url"].endswith(f"/#{d['id']}")
    # die abgelehnte Karte wird beim nächsten Klick auf "holen" erneut versucht
    nochmal = client.post(f"/api/auswertung/{d['id']}/holen").json()
    assert nochmal["anzahl"] == 1
    r = client.post(f"/api/auftrag/{int(nochmal['erste_url'].split('#pka=')[1])}/ergebnis",
                    json={"url": url, "text": SEITE}).json()
    assert r["preis"] == 1.99 and r["naechste_url"] is None

    d = client.get(f"/api/auswertung/{d['id']}").json()
    murkrow = next(z for z in d["zeilen"] if z["zeile"] == murkrow["zeile"])
    assert murkrow["wert"] == 1.99 and murkrow["wert_quelle"] == "de"
    assert d["auftraege"]["fertig"] >= 1 and d["mit_de_preis"] >= 1
    assert d["summe"] == round(sum(z["wert"] or 0 for z in d["zeilen"] if z["erkannt"]), 2)
    # nochmal holen: alles aktuell, keine neuen Aufträge
    assert client.post(f"/api/auswertung/{d['id']}/holen").json()["anzahl"] == 0

    # Preis von Hand überschreiben
    d = client.post(f"/api/auswertung/{d['id']}/korrektur",
                    json={"zeile": murkrow["zeile"], "preis_de": 2.5, "preis_aendern": True}).json()
    murkrow = next(z for z in d["zeilen"] if z["zeile"] == murkrow["zeile"])
    assert murkrow["wert"] == 2.5 and murkrow["wert_quelle"] == "hand"

    # Fotos gelöscht (z. B. Ordner data/auswertungen entfernt): gleiches Foto wird neu ausgewertet
    import shutil
    from pokeauktion import web
    shutil.rmtree(web.UPLOADS / d["id"])
    with open(TESTFOTOS / "foto3.webp", "rb") as f:
        neu = client.post("/api/auswerten", files=[("fotos", ("foto3.webp", f.read(), "image/webp"))]).json()
    assert neu["id"] == d["id"] and client.get(f"/fotos/{d['id']}/foto01.jpg").status_code == 200

    # Seite und Fotos werden ausgeliefert
    assert "Pokémon-Karten-Check" in client.get("/").text
    assert client.get(f"/fotos/{d['id']}/foto01.jpg").status_code == 200
    assert client.get(f"/fotos/{d['id']}/../../karten.sqlite").status_code == 404
