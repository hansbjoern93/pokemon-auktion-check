"""Tests für den TCGdex-Abgleich gegen einen nachgebauten Server.

Die Antwortformate folgen meta/definitions/api.d.ts, meta/definitions/graphql.gql und
server/src/libs/providers/cardmarket.ts aus tcgdex/cards-database.
"""
import asyncio
import json

import httpx
import pytest

from pokeauktion import config, sync
from pokeauktion.db import normalisiere, verbinde
from pokeauktion.katalog import cardmarket_link, suche
from pokeauktion.tcgdex import Tcgdex

BASIS = "https://api.tcgdex.test/v2"


def _karte(sprache, name, attacken, preis=True):
    k = {
        "id": "swsh3-20", "localId": "20", "name": name, "category": "Pokemon",
        "image": f"https://assets.tcgdex.net/{sprache}/swsh/swsh3/20",
        "hp": 330, "types": ["Fire"], "stage": "VMAX", "suffix": None, "rarity": "Rare",
        "illustrator": "aky CG Works", "dexId": [6], "retreat": 3, "regulationMark": "D",
        "set": {"id": "swsh3", "name": "Darkness Ablaze" if sprache == "en" else "Flammende Finsternis",
                "cardCount": {"official": 189, "total": 201}},
        "attacks": attacken,
        "variants": {"normal": False, "reverse": False, "holo": True, "firstEdition": False},
        "variants_detailed": [{"type": "holo", "size": "standard", "variantId": "holo",
                               "thirdParty": {"cardmarket": 482423}}],
        "thirdParty": {"cardmarket": 482423},
        "legal": {"standard": False, "expanded": True}, "updated": "2025-01-01",
    }
    if preis:
        k["pricing"] = {"cardmarket": {
            "updated": "2026-10-04T00:00:00.000Z", "unit": "EUR", "idProduct": 482423,
            "avg": 12.5, "low": 8.0, "trend": 11.9, "avg1": 12.0, "avg7": 12.1, "avg30": 12.3,
            "avg-holo": None, "low-holo": None, "trend-holo": None,
        }, "tcgplayer": None}
    return k


KARTEN = {
    "en": _karte("en", "Charizard VMAX", [{"name": "G-Max Wildfire", "cost": ["Fire"], "damage": "300"}]),
    "de": _karte("de", "Glurak-VMAX", [{"name": "Giga-Flammenmeer", "cost": ["Feuer"], "damage": 300}]),
}
SETS = {
    sprache: {"id": "swsh3", "name": KARTEN[sprache]["set"]["name"], "cardCount": {"official": 189, "total": 201}}
    for sprache in KARTEN
}


def handler(anfrage: httpx.Request) -> httpx.Response:
    pfad = anfrage.url.path.removeprefix("/v2/")
    if pfad == "graphql":
        body = json.loads(anfrage.content)
        sprache = "de" if '"de"' in body["query"] else "en"
        seite = body["variables"]["seite"]
        karten = []
        if seite == 1:
            karten = [{k: v for k, v in KARTEN[sprache].items() if k not in ("pricing", "variants", "thirdParty")}, None]
        return httpx.Response(200, json={"data": {"cards": karten}})
    teile = pfad.split("/")
    sprache = teile[0]
    if sprache not in KARTEN:
        return httpx.Response(200, json=[])
    if teile[1:] == ["sets"]:
        return httpx.Response(200, json=[SETS[sprache]])
    if teile[1:] == ["sets", "swsh3"]:
        return httpx.Response(200, json={
            **SETS[sprache], "releaseDate": "2020-08-14", "serie": {"id": "swsh", "name": "Sword & Shield"},
            "abbreviation": {"official": "DAA"}, "thirdParty": {"cardmarket": 2870},
            "cards": [{"id": "swsh3-20", "localId": "20", "name": KARTEN[sprache]["name"]}],
        })
    if teile[1:] == ["cards", "swsh3-20"]:
        return httpx.Response(200, json=KARTEN[sprache])
    return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PFAD", str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("TCGDEX_SPRACHEN", "en,de,ja")
    api = Tcgdex(BASIS, 4, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(config.Einstellungen, "__init__", _einstellungen_mit_env(tmp_path), raising=False)
    return api, tmp_path / "test.sqlite"


def _einstellungen_mit_env(tmp_path):
    original = config.Einstellungen.__init__

    def init(self, *a, **kw):
        original(self, *a, **kw)
        self.db_pfad = tmp_path / "test.sqlite"
        self.tcgdex_basis = BASIS
        self.sprachen = ["ja", "de", "en"]
    return init


def test_normalisiere():
    assert normalisiere("Glurak-VMAX") == "glurak vmax"
    assert normalisiere("Pokémon Trainer’s") == "pokemon trainer s"
    assert normalisiere("Nidoran♀") == "nidoran w"
    assert normalisiere("リザードン") == "リザードン"


def test_voller_abgleich_und_suche(umgebung):
    api, pfad = umgebung
    assert asyncio.run(sync.main_async([], api=api)) == 0

    con = verbinde(pfad)
    karte = con.execute("SELECT * FROM karten WHERE id = 'swsh3-20'").fetchone()
    assert karte["kp"] == 330 and karte["stufe"] == "VMAX" and karte["cardmarket_id"] == 482423
    assert karte["detail_geladen"] == 1
    assert json.loads(karte["varianten"])["holo"] is True

    namen = dict(con.execute("SELECT sprache, name FROM karten_texte").fetchall())
    assert namen == {"en": "Charizard VMAX", "de": "Glurak-VMAX"}
    assert dict(con.execute("SELECT sprache, name FROM set_namen").fetchall())["de"] == "Flammende Finsternis"
    s = con.execute("SELECT * FROM sets WHERE id = 'swsh3'").fetchone()
    assert s["serie_name"] == "Sword & Shield" and s["kuerzel"] == "DAA" and s["release_date"] == "2020-08-14"

    preis = con.execute("SELECT * FROM preise WHERE karte_id = 'swsh3-20' AND variante = ''").fetchone()
    assert preis["trend"] == 11.9 and preis["avg30"] == 12.3 and preis["trend_holo"] is None
    assert con.execute("SELECT COUNT(*) FROM preis_verlauf").fetchone()[0] == 1

    # Attacken-Schaden als Zahl (de) und als Text (en) landen beide als Text
    schaden = dict(con.execute("SELECT sprache, schaden FROM attacken").fetchall())
    assert schaden == {"en": "300", "de": "300"}

    # Suche: deutscher Name, KP, Attackenname
    treffer = suche(con, "glurak", kp=330)
    assert [t.id for t in treffer] == ["swsh3-20"]
    assert treffer[0].name == "Glurak-VMAX" and treffer[0].set_name == "Flammende Finsternis"
    assert treffer[0].bild_url == "https://assets.tcgdex.net/de/swsh/swsh3/20/high.png"
    assert [(v.bezeichnung, v.trend) for v in treffer[0].versionen] == [("Holo", 11.9)]
    assert treffer[0].preis_min == treffer[0].preis_max == 11.9
    assert suche(con, "charizard", kp=120) == []
    assert [t.id for t in suche(con, attacke="wildfire")] == ["swsh3-20"]


def test_preisabgleich_ueberspringt_wenn_aktuell(umgebung, capsys):
    api, _ = umgebung
    asyncio.run(sync.main_async([], api=api))
    asyncio.run(sync.main_async(["--wenn-veraltet"], api=api))
    assert "Preise sind aktuell" in capsys.readouterr().out


def test_graphql_fehler_faellt_auf_rest_zurueck(umgebung):
    api, pfad = umgebung

    def kaputt(anfrage):
        if anfrage.url.path.endswith("graphql"):
            return httpx.Response(200, json={"errors": [{"message": "boom"}]})
        return handler(anfrage)

    api._client = httpx.AsyncClient(transport=httpx.MockTransport(kaputt))
    assert asyncio.run(sync.main_async(["--voll"], api=api)) == 0
    con = verbinde(pfad)
    assert con.execute("SELECT COUNT(*) FROM karten_texte").fetchone()[0] == 2


def test_cardmarket_link():
    assert cardmarket_link("Charizard VMAX") == (
        "https://www.cardmarket.com/de/Pokemon/Products/Search?searchString=Charizard+VMAX"
    )
