"""Ausschnitt mit TCGdex-Referenzbildern vergleichen.

Wird nur genutzt, wenn mehrere Drucke denselben Text haben (Nachdruck, Shiny, Full Art).
Zwei Merkmale, beide ohne trainiertes Modell:
- Farbverteilung (Farbton/Sättigung): trennt z. B. Shiny von normal, Full Art von Standard.
- ORB-Merkmalspunkte: trennt unterschiedliche Illustrationen.
Entschieden wird nur bei deutlichem Abstand; sonst bleiben alle Versionen stehen.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from pathlib import Path

import cv2
import httpx
import numpy as np

log = logging.getLogger(__name__)

GROESSE = (245, 342)  # Breite, Höhe (TCGdex "low" hat etwa diese Größe)
MINDESTABSTAND = 0.12


def _vorbereiten(bild: np.ndarray) -> np.ndarray:
    return cv2.resize(bild, GROESSE, interpolation=cv2.INTER_AREA)


def farbe(a: np.ndarray, b: np.ndarray) -> float:
    def hist(bild):
        hsv = cv2.cvtColor(bild, cv2.COLOR_BGR2HSV)
        h = cv2.calcHist([hsv], [0, 1], None, [30, 16], [0, 180, 0, 256])
        return cv2.normalize(h, h).flatten()
    return max(0.0, float(cv2.compareHist(hist(a), hist(b), cv2.HISTCMP_CORREL)))


_orb = cv2.ORB_create(nfeatures=800)
_matcher = cv2.BFMatcher(cv2.NORM_HAMMING)


def struktur(a: np.ndarray, b: np.ndarray) -> float:
    _, da = _orb.detectAndCompute(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), None)
    _, db = _orb.detectAndCompute(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), None)
    if da is None or db is None or len(da) < 2 or len(db) < 2:
        return 0.0
    gut = 0
    for paar in _matcher.knnMatch(da, db, k=2):
        if len(paar) == 2 and paar[0].distance < 0.75 * paar[1].distance:
            gut += 1
    return min(1.0, gut / 60)


def aehnlichkeit(ausschnitt: np.ndarray, referenz: np.ndarray) -> float:
    a, b = _vorbereiten(ausschnitt), _vorbereiten(referenz)
    return 0.5 * farbe(a, b) + 0.5 * struktur(a, b)


class Referenzbilder:
    """Lädt Referenzbilder bei Bedarf und speichert sie in data/bilder zwischen."""

    def __init__(self, con: sqlite3.Connection, ordner: Path, client: httpx.Client | None = None):
        self.con = con
        self.ordner = ordner
        self.client = client or httpx.Client(timeout=20, follow_redirects=True)
        self.nicht_erreichbar = False

    def url(self, karte_id: str, sprache: str) -> str | None:
        z = self.con.execute(
            """SELECT bild_basis FROM karten_texte WHERE karte_id = ? AND bild_basis IS NOT NULL
               ORDER BY sprache != ?, sprache != 'en' LIMIT 1""", (karte_id, sprache)).fetchone()
        return f"{z['bild_basis']}/low.webp" if z else None

    def lade(self, karte_id: str, sprache: str) -> np.ndarray | None:
        url = self.url(karte_id, sprache)
        if not url:
            return None
        datei = self.ordner / f"{hashlib.sha1(url.encode()).hexdigest()}.webp"
        if not datei.exists():
            if self.nicht_erreichbar:
                return None
            try:
                antwort = self.client.get(url)
                antwort.raise_for_status()
            except httpx.HTTPError as fehler:
                log.warning("Referenzbild %s nicht ladbar: %s", url, fehler)
                if isinstance(fehler, httpx.TransportError):
                    self.nicht_erreichbar = True
                return None
            datei.parent.mkdir(parents=True, exist_ok=True)
            datei.write_bytes(antwort.content)
        return cv2.imdecode(np.fromfile(datei, dtype=np.uint8), cv2.IMREAD_COLOR)

    def vergleiche(self, ausschnitt: np.ndarray, karten_ids: list[str], sprache: str = "en"):
        """Gibt (eingegrenzte Liste oder None, Notiz) zurück."""
        werte = []
        for karte_id in karten_ids:
            ref = self.lade(karte_id, sprache)
            if ref is not None:
                werte.append((aehnlichkeit(ausschnitt, ref), karte_id))
        if len(werte) < 2:
            return None, "Bildvergleich nicht möglich (Referenzbilder fehlen)"
        werte.sort(reverse=True)
        sortiert = [i for _, i in werte] + [i for i in karten_ids if i not in {w[1] for w in werte}]
        if werte[0][0] - werte[1][0] >= MINDESTABSTAND and len(werte) == len(karten_ids):
            return [werte[0][1]], f"Bildvergleich eindeutig ({werte[0][0]:.2f} vs. {werte[1][0]:.2f})"
        return sortiert, "Bildvergleich nicht eindeutig, alle Versionen möglich"
