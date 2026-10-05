"""Test-Datenbank ohne Zugang zu api.tcgdex.net bauen (nur für Entwicklung und Tests).

Quelle: npm-Paket @tcgdata/tcgdex-offline. Es bündelt die Rohdaten aus tcgdex/cards-database
(Namen und Attacken in allen Sprachen), enthält aber KEINE Preise und keine Bild-URLs.
Für die echte App gilt weiterhin: python -m pokeauktion.sync

  python werkzeuge/offline_testdb.py [ziel.sqlite]
"""
from __future__ import annotations

import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pokeauktion import config  # noqa: E402
from pokeauktion.db import als_json, meta_schreiben, verbinde  # noqa: E402
from pokeauktion.sync import AUSGESCHLOSSENE_SERIEN, karte_speichern, set_speichern  # noqa: E402

PAKET = "https://registry.npmjs.org/@tcgdata/tcgdex-offline/-/tcgdex-offline-0.0.4.tgz"


def lade_paket() -> dict[str, bytes]:
    print("Lade", PAKET)
    daten = urllib.request.urlopen(PAKET, timeout=120).read()
    dateien = {}
    with tarfile.open(fileobj=io.BytesIO(daten), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.isfile() and m.name.endswith(".json") and "/dist/db/" in m.name:
                dateien[m.name.split("/dist/db/", 1)[1]] = tar.extractfile(m).read()
    return dateien


def lokalisiert(wert, sprache):
    return wert.get(sprache) if isinstance(wert, dict) else wert


def erster(wert):
    """Bei sprachabhängigen Werten (z. B. Erscheinungsdatum je Region) den englischen oder ersten nehmen."""
    if isinstance(wert, dict):
        return wert.get("en") or next(iter(wert.values()), None)
    return wert


def main() -> None:
    ziel = Path(sys.argv[1]) if len(sys.argv) > 1 else config.lade().db_pfad
    con = verbinde(ziel)
    dateien = lade_paket()

    for pfad, inhalt in dateien.items():
        if "/sets/" not in pfad:
            continue
        for s in json.loads(inhalt):
            serie = (s.get("series") or {}).get("id")
            if serie in AUSGESCHLOSSENE_SERIEN:
                continue
            for sprache, name in (s.get("name") or {}).items():
                set_speichern(con, sprache, {"id": s["id"], "name": name, "cardCount": s.get("cardCount")}, False)
            con.execute(
                "UPDATE sets SET serie_id = ?, release_date = ?, kuerzel = ?, cardmarket_id = ? WHERE id = ?",
                (serie, erster(s.get("releaseDate")), erster((s.get("abbreviations") or {}).get("official")),
                 (s.get("thirdParty") or {}).get("cardmarket"), s["id"]),
            )

    anzahl = 0
    for pfad, inhalt in dateien.items():
        if "/cards/" not in pfad:
            continue
        for k in json.loads(inhalt):
            if (k.get("series") or {}).get("id") in AUSGESCHLOSSENE_SERIEN:
                continue
            sprachen = sorted(k["name"], key=lambda s: (s != "en", s))
            for sprache in sprachen:
                api_form = {
                    **{f: k.get(f) for f in ("id", "category", "hp", "types", "stage", "suffix", "rarity",
                                             "illustrator", "dexId", "regulationMark", "trainerType",
                                             "energyType", "retreat")},
                    "localId": k.get("cardNumber"),
                    "name": k["name"][sprache],
                    "set": {"id": k["set"]["id"]},
                    "effect": lokalisiert(k.get("effect"), sprache),
                    "attacks": [{**a, "name": lokalisiert(a.get("name"), sprache) or "",
                                 "effect": lokalisiert(a.get("effect"), sprache)}
                                for a in k.get("attacks") or []],
                    "abilities": [{**f, "name": lokalisiert(f.get("name"), sprache) or "",
                                   "effect": lokalisiert(f.get("effect"), sprache)}
                                  for f in k.get("abilities") or []],
                }
                karte_speichern(con, sprache, api_form, basis_ueberschreiben=(sprache == sprachen[0]))
            varianten = {v["type"]: True for v in k.get("variants") or [] if v.get("type")}
            cm = next((v["thirdParty"]["cardmarket"] for v in k.get("variants") or []
                       if (v.get("thirdParty") or {}).get("cardmarket")), None)
            con.execute("UPDATE karten SET varianten = ?, varianten_detail = ?, cardmarket_id = ? WHERE id = ?",
                        (als_json(varianten or None), als_json(k.get("variants")), cm, k["id"]))
            anzahl += 1
            if anzahl % 5000 == 0:
                con.commit()
                print(f"  {anzahl} Karten …", flush=True)
    meta_schreiben(con, "quelle", "offline-testdaten (ohne Preise)")
    con.commit()
    print(f"Fertig: {anzahl} Karten in {ziel}")


if __name__ == "__main__":
    main()
