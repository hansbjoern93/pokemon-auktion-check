"""Datenbank von Hand durchsuchen, zum Prüfen von Schritt 1.

  python -m pokeauktion.suche Glurak
  python -m pokeauktion.suche Charizard --kp 330
  python -m pokeauktion.suche --attacke "Feuersturm" --sprache de
"""
from __future__ import annotations

import argparse

from . import config
from .db import verbinde
from .katalog import cardmarket_link, englischer_name, suche


def _euro(wert: float | None) -> str:
    return "–" if wert is None else f"{wert:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Karten in der lokalen Datenbank suchen")
    p.add_argument("name", nargs="?", help="Kartenname in beliebiger Sprache")
    p.add_argument("--kp", type=int)
    p.add_argument("--attacke")
    p.add_argument("--sprache")
    p.add_argument("--limit", type=int, default=30)
    args = p.parse_args(argv)
    if not (args.name or args.attacke or args.kp):
        p.error("Bitte Name, --attacke oder --kp angeben.")

    con = verbinde(config.lade().db_pfad)
    treffer = suche(con, args.name, args.kp, args.attacke, args.sprache, args.limit)
    if not treffer:
        print("Keine Treffer.")
        return
    treffer.sort(key=lambda k: -(k.preis_max or 0))
    for k in treffer:
        attacken = ", ".join(f"{a['name']} {a.get('damage') or ''}".strip() for a in k.attacken)
        print(f"{k.name} [{k.sprache}] – {k.set_name} ({k.set_id}) #{k.nummer} – KP {k.kp or '–'} – {k.seltenheit or ''}")
        if attacken:
            print(f"    Attacken: {attacken}")
        for v in k.versionen:
            print(f"    {v.bezeichnung:<28} Trend {_euro(v.trend):>12}   30-Tage-Ø {_euro(v.avg30):>12}")
        if not k.versionen:
            print("    (kein Cardmarket-Preis bei TCGdex)")
        print(f"    Bild: {k.bild_url}")
        print(f"    Cardmarket: {cardmarket_link(englischer_name(con, k.id) or k.name)}")


if __name__ == "__main__":
    main()
