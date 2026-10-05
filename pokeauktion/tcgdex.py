"""Schlanker Client für die TCGdex-API v2.

Belegte Endpunkte (Quelle: Quellcode tcgdex/cards-database, server/src/V2/...):
  GET  {basis}/{sprache}/sets          -> Liste von SetResume
  GET  {basis}/{sprache}/sets/{id}     -> Set (mit serie, releaseDate, abbreviation, thirdParty, cards)
  GET  {basis}/{sprache}/cards         -> Liste von CardResume (id, localId, name, image)
  GET  {basis}/{sprache}/cards/{id}    -> Card inkl. variants, variants_detailed, thirdParty, pricing
  POST {basis}/graphql                 -> GraphQL; Sprache über die Direktive @locale(lang: "..")
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

# Felder laut meta/definitions/graphql.gql. Bewusst weggelassen:
#  - level (Int im Schema, aber "X" bei LEVEL-UP-Karten)
#  - variants (Boolean!-Felder fehlen bei vielen Karten) -> kommen per REST
#  - Preise / thirdParty (nicht im GraphQL-Schema) -> kommen per REST
GRAPHQL_KARTEN = """
query Karten($seite: Int!, $anzahl: Int!) {
  cards(pagination: {page: $seite, itemsPerPage: $anzahl}) @locale(lang: "%s") {
    id localId name image category hp types stage suffix rarity illustrator
    dexId evolveFrom regulationMark trainerType energyType retreat effect
    set { id name }
    attacks { name cost damage effect }
    abilities { name type effect }
  }
}
"""


class TcgdexFehler(RuntimeError):
    pass


class Tcgdex:
    def __init__(self, basis: str, parallel: int = 8, client: httpx.AsyncClient | None = None):
        self.basis = basis.rstrip("/")
        self._sem = asyncio.Semaphore(parallel)
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers={"User-Agent": "pokeauktion/0.1 (lokales Auswertungstool)"},
            follow_redirects=True,
        )

    async def schliessen(self) -> None:
        await self._client.aclose()

    async def _anfrage(self, methode: str, url: str, **kw) -> httpx.Response:
        letzter_fehler: Exception | None = None
        for versuch in range(5):
            async with self._sem:
                try:
                    antwort = await self._client.request(methode, url, **kw)
                except httpx.TransportError as fehler:
                    letzter_fehler = fehler
                else:
                    if antwort.status_code == 404:
                        return antwort
                    if antwort.status_code < 400:
                        return antwort
                    letzter_fehler = TcgdexFehler(f"HTTP {antwort.status_code} für {url}")
                    if antwort.status_code < 500 and antwort.status_code != 429:
                        raise letzter_fehler
            await asyncio.sleep(2 ** versuch)
        raise TcgdexFehler(f"Abruf fehlgeschlagen: {url}: {letzter_fehler}")

    async def get(self, pfad: str):
        antwort = await self._anfrage("GET", f"{self.basis}/{pfad}")
        if antwort.status_code == 404:
            return None
        return antwort.json()

    async def sets(self, sprache: str) -> list[dict]:
        return await self.get(f"{sprache}/sets") or []

    async def set(self, sprache: str, set_id: str) -> dict | None:
        return await self.get(f"{sprache}/sets/{quote(set_id, safe='')}")

    async def karten_liste(self, sprache: str) -> list[dict]:
        return await self.get(f"{sprache}/cards") or []

    async def karte(self, sprache: str, karte_id: str) -> dict | None:
        return await self.get(f"{sprache}/cards/{quote(karte_id, safe='')}")

    async def karten_graphql(self, sprache: str, seite: int, anzahl: int) -> tuple[list[dict], int]:
        """Gibt (Karten, Anzahl Einträge auf der Seite inkl. fehlerhafter) zurück."""
        antwort = await self._anfrage(
            "POST",
            f"{self.basis}/graphql",
            json={"query": GRAPHQL_KARTEN % sprache, "variables": {"seite": seite, "anzahl": anzahl}},
        )
        daten = antwort.json()
        karten = (daten.get("data") or {}).get("cards")
        if karten is None:
            raise TcgdexFehler(f"GraphQL ohne Daten ({sprache}, Seite {seite}): {daten.get('errors')}")
        if daten.get("errors"):
            # Teilfehler (z. B. ein Feld einer einzelnen Karte) -> Karte ist dann None
            log.debug("GraphQL-Teilfehler %s Seite %s: %s", sprache, seite, daten["errors"][:3])
        return [k for k in karten if k], len(karten)
