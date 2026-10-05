"""Einzelne Karten auf einem Foto finden und ausschneiden.

Vorgehen ohne trainiertes Modell:
1. Helle, kartenförmige Flächen finden (Pokémon-Karten 63 x 88 mm, Seitenverhältnis ~0,72).
2. Liegen die sicheren Funde in einem Raster (Ordnerseite), das Raster vervollständigen:
   fehlende Felder (Spiegelungen, angeschnittene Reihen) werden aus Spalten, Reihen und
   Kartengröße ergänzt.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

KARTEN_VERHAELTNIS = 63 / 88  # Breite / Höhe


@dataclass
class Ausschnitt:
    nr: int                 # fortlaufende Nummer auf dem Foto (Leserichtung)
    box: tuple[int, int, int, int]  # x, y, w, h im Originalfoto
    reihe: int | None       # bei Raster: 1-basiert
    spalte: int | None
    quelle: str             # "kontur" oder "raster"
    sichtbar: float         # Anteil der Karte, der im Foto liegt (0..1)
    bild: np.ndarray | None = None


def _kartenfoermig(w: int, h: int, toleranz: float = 0.12) -> bool:
    return abs(w / h - KARTEN_VERHAELTNIS) <= toleranz


def _konturen(img: np.ndarray) -> list[tuple[int, int, int, int]]:
    H, W = img.shape[:2]
    grau = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, maske = cv2.threshold(cv2.GaussianBlur(grau, (5, 5), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kern = max(3, int(min(W, H) / 150)) | 1
    maske = cv2.morphologyEx(maske, cv2.MORPH_OPEN, np.ones((kern, kern), np.uint8))
    konturen, _ = cv2.findContours(maske, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxen = []
    for k in konturen:
        x, y, w, h = cv2.boundingRect(k)
        anteil = w * h / (W * H)
        # Fläche der Kontur muss den Rahmen weitgehend füllen (keine L-förmigen Klumpen)
        if 0.01 < anteil < 0.6 and cv2.contourArea(k) > 0.55 * w * h and _kartenfoermig(w, h):
            boxen.append((x, y, w, h))
    return boxen


def _gruppen(werte: list[float], abstand: float) -> list[float]:
    """Eindimensionales Clustern: Mittelwerte von Gruppen, deren Elemente < abstand auseinander liegen."""
    gruppen: list[list[float]] = []
    for v in sorted(werte):
        if gruppen and v - gruppen[-1][-1] < abstand:
            gruppen[-1].append(v)
        else:
            gruppen.append([v])
    return [float(np.mean(g)) for g in gruppen]


def _raster_ergaenzen(boxen, W, H):
    """Gibt (Liste von (box, reihe, spalte, quelle)) zurück oder None, wenn kein Raster erkennbar ist."""
    if len(boxen) < 3:
        return None
    bw = float(np.median([b[2] for b in boxen]))
    bh = float(np.median([b[3] for b in boxen]))
    # Nur Boxen in typischer Größe zählen für das Raster
    gute = [b for b in boxen if 0.75 < b[2] / bw < 1.3 and 0.75 < b[3] / bh < 1.3]
    xs = _gruppen([b[0] + b[2] / 2 for b in gute], bw * 0.5)
    ys = _gruppen([b[1] + b[3] / 2 for b in gute], bh * 0.5)
    if len(xs) < 2 and len(ys) < 2:
        return None
    # Abstand der Spalten/Reihen schätzen; ein Ordner hat gleichmäßige Abstände
    def schritt(mitten, groesse):
        # Abstände können mehrere Felder überspannen, wenn dazwischen nichts erkannt wurde
        if len(mitten) < 2:
            return groesse * 1.08
        d = np.diff(mitten)
        k = np.maximum(1, np.round(d / (groesse * 1.1)))
        return float(np.median(d / k))

    dx, dy = schritt(xs, bw), schritt(ys, bh)
    if not (0.9 * bw < dx < 1.5 * bw and 0.9 * bh < dy < 1.5 * bh):
        return None
    # Lücken zwischen gefundenen Spalten/Reihen auffüllen und nach außen erweitern,
    # solange mindestens 40 % einer Karte im Bild liegt.
    def erweitern(mitten, schritt, groesse, grenze):
        mitten = sorted(mitten)
        voll = [mitten[0]]
        for m in mitten[1:]:
            while m - voll[-1] > 1.5 * schritt:
                voll.append(voll[-1] + schritt)
            voll.append(m)
        while voll[0] - schritt + groesse * 0.1 > 0 and (voll[0] - schritt + groesse / 2) > 0.4 * groesse:
            voll.insert(0, voll[0] - schritt)
        while (grenze - (voll[-1] + schritt - groesse / 2)) > 0.4 * groesse:
            voll.append(voll[-1] + schritt)
        return voll

    spalten = erweitern(xs, dx, bw, W)
    reihen = erweitern(ys, dy, bh, H)
    if len(spalten) * len(reihen) > 30:
        return None
    ergebnis = []
    for r, cy in enumerate(reihen, 1):
        for s, cx in enumerate(spalten, 1):
            treffer = [b for b in gute if abs(b[0] + b[2] / 2 - cx) < dx / 2 and abs(b[1] + b[3] / 2 - cy) < dy / 2]
            if treffer:
                ergebnis.append((max(treffer, key=lambda b: b[2] * b[3]), r, s, "kontur"))
            else:
                box = (int(cx - bw / 2), int(cy - bh / 2), int(bw), int(bh))
                ergebnis.append((box, r, s, "raster"))
    return ergebnis


def _sichtbar(box, W, H) -> float:
    x, y, w, h = box
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    return max(0, x1 - x0) * max(0, y1 - y0) / (w * h)


def finde_karten(img: np.ndarray, rand: float = 0.03) -> list[Ausschnitt]:
    H, W = img.shape[:2]
    boxen = _konturen(img)
    raster = _raster_ergaenzen(boxen, W, H)
    if raster is None:
        eintraege = [(b, None, None, "kontur") for b in sorted(boxen, key=lambda b: (b[1] // max(1, b[3] // 2), b[0]))]
    else:
        eintraege = raster
    ausschnitte = []
    for nr, (box, reihe, spalte, quelle) in enumerate(eintraege, 1):
        x, y, w, h = box
        sichtbar = _sichtbar(box, W, H)
        if sichtbar < 0.4:
            continue
        mx, my = int(w * rand), int(h * rand)
        x0, y0 = max(0, x - mx), max(0, y - my)
        x1, y1 = min(W, x + w + mx), min(H, y + h + my)
        ausschnitte.append(Ausschnitt(len(ausschnitte) + 1, (x, y, w, h), reihe, spalte, quelle,
                                      round(sichtbar, 2), img[y0:y1, x0:x1].copy()))
    return ausschnitte


def zeichne(img: np.ndarray, ausschnitte: list[Ausschnitt]) -> np.ndarray:
    """Nummerierte Rahmen ins Foto zeichnen."""
    bild = img.copy()
    dicke = max(2, int(min(img.shape[:2]) / 250))
    for a in ausschnitte:
        x, y, w, h = a.box
        farbe = (40, 200, 40) if a.quelle == "kontur" else (0, 170, 255)
        cv2.rectangle(bild, (x, y), (x + w, y + h), farbe, dicke)
        groesse = max(0.6, min(img.shape[:2]) / 700)
        text = str(a.nr)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, groesse, dicke)
        tx, ty = max(0, x) + 4, max(0, y) + th + 8
        cv2.rectangle(bild, (tx - 4, ty - th - 6), (tx + tw + 4, ty + 6), (0, 0, 0), -1)
        cv2.putText(bild, text, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, groesse, (255, 255, 255), dicke)
    return bild
