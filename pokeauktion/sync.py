"""Abgleich der lokalen Datenbank mit TCGdex.

Aufrufe:
  python -m pokeauktion.sync                 # erster Lauf: alles laden, danach: nur Preise
  python -m pokeauktion.sync --voll          # Karten, Texte, Sets und Preise neu laden
  python -m pokeauktion.sync --preise        # nur Preise (für den täglichen Abgleich)
  python -m pokeauktion.sync --wenn-veraltet # Preise nur, wenn letzter Abgleich > 20 h her
  python -m pokeauktion.sync --voll --sets base1,swsh3 --sprachen en,de   # schneller Test
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

from . import config
from .db import PREISFELDER, als_json, meta_lesen, meta_schreiben, normalisiere, verbinde
from .tcgdex import Tcgdex, TcgdexFehler

log = logging.getLogger("sync")


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sprachen_sortiert(sprachen: list[str]) -> list[str]:
    # Englisch zuerst: liefert die sprachunabhängigen Felder (Seltenheit, Stufe, ...) in einheitlicher Form.
    return sorted(dict.fromkeys(sprachen), key=lambda s: (s != "en", s != "de", s))


class Fortschritt:
    def __init__(self, titel: str, gesamt: int):
        self.titel, self.gesamt, self.n, self.t0 = titel, gesamt, 0, time.monotonic()

    def schritt(self, n: int = 1) -> None:
        self.n += n
        if self.n == self.gesamt or self.n % 500 == 0:
            dauer = time.monotonic() - self.t0
            print(f"  {self.titel}: {self.n}/{self.gesamt} ({dauer:.0f} s)", flush=True)


# --------------------------------------------------------------------------- Speichern

def set_speichern(con: sqlite3.Connection, sprache: str, s: dict, detail: bool) -> None:
    if s.get("name"):
        con.execute(
            "INSERT INTO set_namen (set_id, sprache, name) VALUES (?, ?, ?) "
            "ON CONFLICT(set_id, sprache) DO UPDATE SET name = excluded.name",
            (s["id"], sprache, s["name"]),
        )
    anzahl = s.get("cardCount") or {}
    if not detail:
        con.execute(
            "INSERT OR IGNORE INTO sets (id, karten_offiziell, karten_gesamt) VALUES (?, ?, ?)",
            (s["id"], anzahl.get("official"), anzahl.get("total")),
        )
        return
    serie = s.get("serie") or {}
    con.execute(
        """INSERT INTO sets (id, serie_id, serie_name, release_date, karten_offiziell, karten_gesamt,
                             kuerzel, cardmarket_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             serie_id = excluded.serie_id, serie_name = excluded.serie_name,
             release_date = excluded.release_date, karten_offiziell = excluded.karten_offiziell,
             karten_gesamt = excluded.karten_gesamt, kuerzel = excluded.kuerzel,
             cardmarket_id = excluded.cardmarket_id""",
        (
            s["id"], serie.get("id"), serie.get("name"), s.get("releaseDate"),
            anzahl.get("official"), anzahl.get("total"),
            (s.get("abbreviation") or {}).get("official"),
            (s.get("thirdParty") or {}).get("cardmarket"),
        ),
    )


def _schaden(wert) -> str | None:
    return None if wert in (None, "") else str(wert)


def karte_speichern(con: sqlite3.Connection, sprache: str, k: dict, basis_ueberschreiben: bool) -> None:
    """Speichert eine Karte aus GraphQL oder REST (gleiche Feldnamen)."""
    karte_id = k["id"]
    s = k.get("set") or {}
    set_id = s.get("id") or karte_id.rsplit("-", 1)[0]
    if s.get("name"):
        set_speichern(con, sprache, {"id": set_id, "name": s["name"], "cardCount": s.get("cardCount")}, False)

    werte = (
        karte_id, set_id, str(k.get("localId") or ""), k.get("category"), k.get("hp"),
        als_json(k.get("types")), k.get("stage"), k.get("suffix"), k.get("rarity"),
        k.get("illustrator"), als_json(k.get("dexId")), k.get("regulationMark"),
        k.get("trainerType"), k.get("energyType"), k.get("retreat"), _jetzt(),
    )
    spalten = ("id, set_id, nummer, kategorie, kp, typen, stufe, suffix, seltenheit, illustrator, "
               "dex_ids, regulation_mark, trainer_typ, energie_typ, rueckzug, aktualisiert")
    if basis_ueberschreiben:
        aktualisieren = ", ".join(
            f"{sp.strip()} = excluded.{sp.strip()}" for sp in spalten.split(",")[1:]
        )
        con.execute(
            f"INSERT INTO karten ({spalten}) VALUES ({', '.join('?' * len(werte))}) "
            f"ON CONFLICT(id) DO UPDATE SET {aktualisieren}",
            werte,
        )
    else:
        con.execute(f"INSERT OR IGNORE INTO karten ({spalten}) VALUES ({', '.join('?' * len(werte))})", werte)

    attacken = [
        {"name": a.get("name"), "cost": a.get("cost"), "damage": _schaden(a.get("damage")), "effect": a.get("effect")}
        for a in (k.get("attacks") or []) if a and a.get("name")
    ]
    con.execute(
        """INSERT INTO karten_texte (karte_id, sprache, name, name_norm, bild_basis, attacken, faehigkeiten, effekt)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(karte_id, sprache) DO UPDATE SET
             name = excluded.name, name_norm = excluded.name_norm, bild_basis = excluded.bild_basis,
             attacken = excluded.attacken, faehigkeiten = excluded.faehigkeiten, effekt = excluded.effekt""",
        (
            karte_id, sprache, k.get("name") or "", normalisiere(k.get("name")), k.get("image"),
            als_json(attacken), als_json([f for f in (k.get("abilities") or []) if f]), k.get("effect"),
        ),
    )
    con.execute("DELETE FROM attacken WHERE karte_id = ? AND sprache = ?", (karte_id, sprache))
    con.executemany(
        "INSERT INTO attacken (karte_id, sprache, pos, name, name_norm, schaden) VALUES (?, ?, ?, ?, ?, ?)",
        [(karte_id, sprache, i, a["name"], normalisiere(a["name"]), a["damage"]) for i, a in enumerate(attacken)],
    )


def _preis_speichern(con: sqlite3.Connection, karte_id: str, variante: str, cm: dict, datum: str) -> None:
    spalten = list(PREISFELDER.values())
    werte = [cm.get(feld) for feld in PREISFELDER]
    con.execute(
        f"""INSERT INTO preise (karte_id, variante, id_produkt, {', '.join(spalten)}, stand, abgerufen)
            VALUES (?, ?, ?, {', '.join('?' * len(spalten))}, ?, ?)
            ON CONFLICT(karte_id, variante) DO UPDATE SET id_produkt = excluded.id_produkt,
              {', '.join(f'{s} = excluded.{s}' for s in spalten)},
              stand = excluded.stand, abgerufen = excluded.abgerufen""",
        [karte_id, variante, cm.get("idProduct"), *werte, cm.get("updated"), _jetzt()],
    )
    con.execute(
        "INSERT OR REPLACE INTO preis_verlauf (karte_id, variante, datum, trend, trend_holo) VALUES (?, ?, ?, ?, ?)",
        (karte_id, variante, datum, cm.get("trend"), cm.get("trend-holo")),
    )


def detail_speichern(con: sqlite3.Connection, sprache: str, k: dict) -> None:
    """REST-Detail: Texte, Varianten, Cardmarket-ID und Preise."""
    karte_speichern(con, sprache, k, basis_ueberschreiben=(sprache == "en"))
    detail = []
    for v in k.get("variants_detailed") or []:
        detail.append({feld: v.get(feld) for feld in
                       ("variantId", "type", "subtype", "size", "stamp", "foil", "thirdParty")})
    con.execute(
        "UPDATE karten SET varianten = ?, varianten_detail = ?, cardmarket_id = ?, detail_geladen = 1 WHERE id = ?",
        (als_json(k.get("variants")), als_json(detail or None),
         (k.get("thirdParty") or {}).get("cardmarket"), k["id"]),
    )
    datum = datetime.now(timezone.utc).date().isoformat()
    con.execute("DELETE FROM preise WHERE karte_id = ?", (k["id"],))
    cm = ((k.get("pricing") or {}).get("cardmarket")) or None
    if cm:
        _preis_speichern(con, k["id"], "", cm, datum)
    for v in k.get("variants_detailed") or []:
        vcm = ((v.get("pricing") or {}).get("cardmarket")) or None
        if vcm and v.get("variantId"):
            _preis_speichern(con, k["id"], v["variantId"], vcm, datum)


# --------------------------------------------------------------------------- Abläufe

async def sets_laden(api: Tcgdex, con: sqlite3.Connection, sprachen: list[str], nur: set[str] | None) -> dict[str, list[str]]:
    """Lädt Set-Listen aller Sprachen und Set-Details (einmal pro Set, bevorzugt Englisch).

    Gibt {sprache: [set_ids]} zurück.
    """
    pro_sprache: dict[str, list[str]] = {}
    detail_sprache: dict[str, str] = {}
    listen = await asyncio.gather(*(api.sets(s) for s in sprachen), return_exceptions=True)
    for sprache, liste in zip(sprachen, listen):
        if isinstance(liste, Exception):
            print(f"  ! Sets für '{sprache}' nicht abrufbar: {liste}")
            continue
        ids = []
        for s in liste:
            if nur and s["id"] not in nur:
                continue
            set_speichern(con, sprache, s, detail=False)
            ids.append(s["id"])
            detail_sprache.setdefault(s["id"], sprache)
        pro_sprache[sprache] = ids
        print(f"  Sets {sprache}: {len(ids)}")
    con.commit()

    fortschritt = Fortschritt("Set-Details", len(detail_sprache))

    async def eins(set_id: str, sprache: str):
        try:
            s = await api.set(sprache, set_id)
        except TcgdexFehler as fehler:
            print(f"  ! Set {set_id}: {fehler}")
            s = None
        if s:
            set_speichern(con, sprache, s, detail=True)
        fortschritt.schritt()

    await asyncio.gather(*(eins(i, s) for i, s in detail_sprache.items()))
    con.commit()
    return pro_sprache


async def texte_per_graphql(api: Tcgdex, con: sqlite3.Connection, sprache: str, seitengroesse: int) -> int:
    seite, anzahl = 1, 0
    while True:
        karten, roh = await api.karten_graphql(sprache, seite, seitengroesse)
        if roh > len(karten):
            print(f"  ! {roh - len(karten)} Karten ({sprache}, Seite {seite}) mit GraphQL-Fehler übersprungen")
        for k in karten:
            karte_speichern(con, sprache, k, basis_ueberschreiben=(sprache == "en"))
        con.commit()
        anzahl += len(karten)
        if seite % 10 == 0:
            print(f"  Karten {sprache}: {anzahl} …", flush=True)
        if roh < seitengroesse:
            return anzahl
        seite += 1


async def texte_per_rest(api: Tcgdex, con: sqlite3.Connection, sprache: str, set_ids: list[str]) -> int:
    """Langsamer Weg: Set für Set, Karte für Karte (Ersatz für GraphQL, oder bei --sets)."""
    karten_ids: list[str] = []
    sets = await asyncio.gather(*(api.set(sprache, i) for i in set_ids), return_exceptions=True)
    for s in sets:
        if isinstance(s, Exception):
            print(f"  ! Set ({sprache}): {s}")
            continue
        karten_ids += [k["id"] for k in (s or {}).get("cards") or []]
    fortschritt = Fortschritt(f"Karten {sprache}", len(karten_ids))

    async def eins(karte_id: str):
        try:
            k = await api.karte(sprache, karte_id)
        except TcgdexFehler as fehler:
            print(f"  ! {sprache}/{karte_id}: {fehler}")
            k = None
        if k:
            karte_speichern(con, sprache, k, basis_ueberschreiben=(sprache == "en"))
        fortschritt.schritt()

    await asyncio.gather(*(eins(i) for i in karten_ids))
    con.commit()
    return len(karten_ids)


async def preise_laden(api: Tcgdex, con: sqlite3.Connection, alle: bool, nur: set[str] | None) -> int:
    """REST-Detail je Karte: Preise und Varianten.

    Abgerufen wird in Englisch, falls vorhanden, sonst in der ersten vorhandenen Sprache.
    Ohne --alle-preise nur Karten, die noch nie im Detail geladen wurden oder eine
    Cardmarket-Zuordnung haben.
    """
    bedingung = "" if alle else "WHERE k.detail_geladen = 0 OR k.cardmarket_id IS NOT NULL OR EXISTS (SELECT 1 FROM preise p WHERE p.karte_id = k.id)"
    zeilen = con.execute(
        f"""SELECT k.id, k.set_id,
                   (SELECT t.sprache FROM karten_texte t WHERE t.karte_id = k.id
                    ORDER BY t.sprache != 'en', t.sprache != 'de', t.sprache LIMIT 1) AS sprache
            FROM karten k {bedingung}"""
    ).fetchall()
    auftraege = [(z["id"], z["sprache"] or "en") for z in zeilen if not nur or z["set_id"] in nur]
    fortschritt = Fortschritt("Preise", len(auftraege))
    fehler_anzahl = 0

    async def eins(karte_id: str, sprache: str):
        nonlocal fehler_anzahl
        try:
            k = await api.karte(sprache, karte_id)
        except TcgdexFehler as fehler:
            fehler_anzahl += 1
            log.warning("%s/%s: %s", sprache, karte_id, fehler)
            k = None
        if k:
            detail_speichern(con, sprache, k)
        fortschritt.schritt()
        if fortschritt.n % 200 == 0:
            con.commit()

    await asyncio.gather(*(eins(i, s) for i, s in auftraege))
    con.commit()
    if fehler_anzahl:
        print(f"  ! {fehler_anzahl} Karten konnten nicht abgerufen werden (siehe Log).")
    return len(auftraege)


async def voll(api: Tcgdex, con: sqlite3.Connection, ein: config.Einstellungen, nur_sets: set[str] | None) -> None:
    sprachen = _sprachen_sortiert(ein.sprachen)
    print(f"1/3 Sets ({', '.join(sprachen)})")
    pro_sprache = await sets_laden(api, con, sprachen, nur_sets)

    print("2/3 Kartentexte")
    for sprache in sprachen:
        if not pro_sprache.get(sprache):
            continue
        if nur_sets:
            n = await texte_per_rest(api, con, sprache, pro_sprache[sprache])
        else:
            try:
                n = await texte_per_graphql(api, con, sprache, ein.graphql_seitengroesse)
            except TcgdexFehler as fehler:
                print(f"  GraphQL für '{sprache}' fehlgeschlagen ({fehler}), nutze REST …")
                n = await texte_per_rest(api, con, sprache, pro_sprache[sprache])
        print(f"  Karten {sprache}: {n}")
    meta_schreiben(con, "letzter_voller_abgleich", _jetzt())
    con.commit()

    print("3/3 Preise und Varianten")
    await preise_laden(api, con, alle=True, nur=nur_sets)
    meta_schreiben(con, "letzter_preisabgleich", _jetzt())
    con.commit()


def zusammenfassung(con: sqlite3.Connection) -> str:
    z = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    sprachen = con.execute(
        "SELECT sprache, COUNT(*) n FROM karten_texte GROUP BY sprache ORDER BY n DESC"
    ).fetchall()
    return "\n".join([
        f"Sets: {z('SELECT COUNT(*) FROM sets')}",
        f"Karten: {z('SELECT COUNT(*) FROM karten')}",
        "Texte je Sprache: " + ", ".join(f"{r['sprache']}={r['n']}" for r in sprachen),
        f"Karten mit Cardmarket-Preis: {z('SELECT COUNT(DISTINCT karte_id) FROM preise')}",
        f"Letzter Preisabgleich: {meta_lesen(con, 'letzter_preisabgleich')}",
    ])


async def main_async(argv: list[str] | None = None, api: Tcgdex | None = None) -> int:
    p = argparse.ArgumentParser(description="Abgleich der Kartendatenbank mit TCGdex")
    p.add_argument("--voll", action="store_true", help="Alles neu laden (Sets, Karten, Texte, Preise)")
    p.add_argument("--preise", action="store_true", help="Nur Preise abgleichen")
    p.add_argument("--alle-preise", action="store_true", help="Preise für wirklich alle Karten abrufen")
    p.add_argument("--wenn-veraltet", action="store_true", help="Preise nur, wenn älter als 20 Stunden")
    p.add_argument("--sets", help="Nur diese Set-IDs (kommagetrennt), zum Testen")
    p.add_argument("--sprachen", help="Sprachen (kommagetrennt), überschreibt TCGDEX_SPRACHEN")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    ein = config.lade()
    if args.sprachen:
        ein.sprachen = [s.strip() for s in args.sprachen.split(",") if s.strip()]
    nur_sets = {s.strip() for s in args.sets.split(",")} if args.sets else None

    con = verbinde(ein.db_pfad)
    eigener_client = api is None
    api = api or Tcgdex(ein.tcgdex_basis, ein.parallele_abrufe)
    try:
        leer = con.execute("SELECT COUNT(*) FROM karten").fetchone()[0] == 0
        if args.voll or (leer and not args.preise):
            await voll(api, con, ein, nur_sets)
        else:
            if args.wenn_veraltet:
                letzter = meta_lesen(con, "letzter_preisabgleich")
                if letzter and datetime.fromisoformat(letzter) > datetime.now(timezone.utc) - timedelta(hours=20):
                    print(f"Preise sind aktuell (Stand {letzter}).")
                    return 0
            print("Preisabgleich …")
            await preise_laden(api, con, alle=args.alle_preise, nur=nur_sets)
            meta_schreiben(con, "letzter_preisabgleich", _jetzt())
            con.commit()
        print("\n" + zusammenfassung(con))
        return 0
    finally:
        if eigener_client:
            await api.schliessen()
        con.close()


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
