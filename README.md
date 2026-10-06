# Pokémon-Auktions-Check

Lokale Web-App, die eBay-Auktionen mit Pokémon-Karten auswertet: Sie erkennt die Karten auf den
Fotos und zeigt den Cardmarket-Trendpreis jeder Karte sowie die Summe als Spanne.

> **Stand:** Kartendatenbank, Erkennung und Weboberfläche mit festem Gesamtwert (Preise aus
> Deutschland über eine Browser-Erweiterung) sind fertig. Die eBay-Anbindung folgt.

## Schnellstart

- **Windows:** Doppelklick auf `start.bat`
- **macOS/Linux:** `./start.sh`

Beim ersten Start wird alles eingerichtet und die Kartendatenbank geladen (einmalig 15 bis 40
Minuten). Danach öffnet sich die App im Browser unter http://127.0.0.1:8000. Beenden mit Strg + C
im schwarzen Fenster.

### Einmalig: Browser-Erweiterung installieren (Chrome oder Edge)

1. `chrome://extensions` öffnen (Edge: `edge://extensions`).
2. Oben rechts den **Entwicklermodus** einschalten.
3. **„Entpackte Erweiterung laden“** und den Ordner `erweiterung` aus dem Projektordner wählen.

### So kommst du zum festen Gesamtwert

1. **Foto einlesen** und „Auswerten“ klicken. Die App erkennt die Karten und zeigt sofort einen
   **vorläufigen** Wert (billigster „ab“-Preis auf Cardmarket, alle Länder).
2. **„Preise aus Deutschland holen“** klicken. In einem neuen Tab öffnet die Erweiterung
   nacheinander für jede Karte (bei unsicherem Druck: für jeden möglichen Druck) die Cardmarket-Seite
   mit dem Filter **Verkäufer aus Deutschland, Zustand ab Good**, liest den **ersten Preis** und geht
   weiter. Am Ende springt der Tab zur App zurück.
3. Der Wert jeder Karte ist dann der Preis aus Deutschland; bei mehreren möglichen Drucken der
   billigste. Die Summe ist ein fester Betrag.
4. Du kannst jeden Preis auch von Hand eintragen und eine falsch erkannte Karte korrigieren.

Hinweise:
- Die Erweiterung arbeitet nur, wenn du den Knopf in der App drückst, mit Pausen zwischen den
  Seiten. Erscheint eine Cloudflare-Prüfung, bestätige sie; danach geht es weiter.
- Automatisches Auslesen ist laut Cardmarket-Nutzungsbedingungen nicht erlaubt. Die Nutzung erfolgt
  auf eigenes Risiko; bei wenigen Abfragen am Tag ist das Risiko gering.
- Ein geholter Preis gilt 3 Tage und wird auch für andere Fotos mit derselben Karte genutzt.
- Wird eine Karte auf Cardmarket nicht gefunden, bleibt der vorläufige Wert stehen. Wenn kein
  Preis gelesen werden konnte, steht der Seitentext in `data/letzter_cardmarket_text.txt`.

## Einrichtung

Voraussetzung: Python 3.10 oder neuer.

```bash
git clone https://github.com/hansbjoern93/pokemon-auktion-check.git
cd pokemon-auktion-check
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

Für Schritt 1 und 2 musst du in der `.env` nichts eintragen. Es entstehen keine Kosten.

## Schritt 1: Kartendatenbank

### Erster Abgleich (einmalig)

```bash
python -m pokeauktion.sync
```

Das Skript lädt aus [TCGdex](https://tcgdex.dev):

1. alle Sets in allen Sprachen, also Namen, Serie, Erscheinungsdatum und offizielles Kürzel,
2. alle Karten in allen Sprachen, also Name, KP, Attacken mit Schaden, Fähigkeiten, Stufe
   (Basic, VMAX, …), Suffix (EX, GX, V, …), Seltenheit und das Referenzbild der jeweiligen Sprache,
3. je Karte die Druckvarianten (normal, holo, reverse, 1. Edition) und den
   **Cardmarket-Preis**, den TCGdex mitliefert (Trend, Durchschnitt 1/7/30 Tage, Tiefstpreis,
   jeweils auch für die Foil-Version).

Der erste Lauf dauert je nach Verbindung etwa 15 bis 40 Minuten. Für Preise und Varianten ist ein
Abruf pro Karte nötig, rund 20.000 insgesamt. Die Datenbank liegt danach in
`data/karten.sqlite`.

**Schneller Probelauf** mit nur zwei Sets und zwei Sprachen (wenige Minuten):

```bash
python -m pokeauktion.sync --voll --sets base1,swsh3 --sprachen en,de
```

### Täglicher Preisabgleich

```bash
python -m pokeauktion.sync --preise          # immer abgleichen
python -m pokeauktion.sync --wenn-veraltet   # nur wenn älter als 20 Stunden
```

Ab Schritt 4 macht die App das beim Start automatisch mit `--wenn-veraltet`. Wenn du den Abgleich
unabhängig davon einplanen willst:

- **macOS/Linux** (`crontab -e`, täglich 6:10 Uhr):
  `10 6 * * * cd /PFAD/pokemon-auktion-check && .venv/bin/python -m pokeauktion.sync --wenn-veraltet`
- **Windows**: In der Aufgabenplanung eine einfache Aufgabe anlegen, täglich.
  Programm: `C:\PFAD\pokemon-auktion-check\.venv\Scripts\python.exe`,
  Argumente: `-m pokeauktion.sync --wenn-veraltet`, Starten in: `C:\PFAD\pokemon-auktion-check`.

Mit `--voll` lädst du alles neu, das ist sinnvoll nach einem neuen Set-Release. Mit `--alle-preise`
fragst du auch Karten ab, für die TCGdex bisher keinen Cardmarket-Preis hatte.

### Datenbank prüfen

```bash
python -m pokeauktion.suche Glurak
python -m pokeauktion.suche Charizard --kp 330
python -m pokeauktion.suche --attacke "Feuersturm" --sprache de
```

Die Ausgabe zeigt pro Treffer Name, Sprache, Set, Nummer, KP, Attacken, alle Versionen mit
Trendpreis, das Referenzbild und den Cardmarket-Link.

### Tests

```bash
python -m pytest
```

Die Tests laufen gegen einen nachgebauten TCGdex-Server, ohne Internet.

## Schritt 2: Karten auf Fotos erkennen

```bash
python -m pokeauktion.erkennen testfotos/foto1.webp
python -m pokeauktion.erkennen foto.jpg --hinweis "Sammlung deutsch Holo" --rahmen ausgabe/
```

Die Ausgabe nennt je Karte Name, Sprache, Set und Nummer (bzw. alle möglichen Drucke), Sicherheit,
Preis oder Preisspanne, den Cardmarket-Link und den Grund für die Zuordnung. Am Ende steht die Summe
als Spanne und wie viele Karten nicht erkannt wurden. Mit `--rahmen` wird das Foto mit nummerierten
Rahmen gespeichert.

### So arbeitet die Erkennung

Alles läuft **lokal und kostenlos**, ohne API-Schlüssel:

1. **Zuschnitt** (OpenCV): Helle, kartenförmige Flächen werden gesucht. Bei Ordnerseiten wird das
   Raster vervollständigt, sodass auch spiegelnde oder angeschnittene Karten einen Rahmen bekommen
   (orange statt grün).
2. **Text lesen** (RapidOCR, Modelle im Paket enthalten): Name, KP, Attacken, Schaden. Set-Symbol und
   Kartennummer werden bewusst nicht verwendet.
3. **Kandidaten** aus der Datenbank: unscharfe Suche über den Namen in allen Sprachen, bewertet mit KP
   und Attacken. Die Attacken entscheiden auch über die Sprache, wenn ein Name in mehreren Sprachen
   gleich ist. Ist der Name unlesbar, wird über die Attacken gesucht.
4. **Versionen**: Haben mehrere Drucke denselben Text (Nachdruck, Full Art, Shiny), vergleicht die
   App den Ausschnitt mit den TCGdex-Referenzbildern (Farbverteilung und Bildmerkmale). Sie legt
   sich nur bei deutlichem Unterschied fest. Sonst zeigt sie alle Drucke mit Preisspanne.
5. **Sicherheit**: *hoch* = Name, KP/Attacken passen, nur ein möglicher Druck; *mittel* = Karte
   klar, aber mehrere Drucke möglich oder wenig Belege; *niedrig* = mehrere ähnliche Karten, alle
   werden angezeigt.

### Optional: unsichere Karten von Claude lesen lassen (über dein Claude-Abo)

Die App kann unsichere Karten zusätzlich von Claude lesen lassen. Sie nutzt dafür das Programm
**Claude Code**, das in deinem Claude-Abo enthalten ist. Es gibt **keinen API-Schlüssel und keine
Zusatzkosten**. Jede gelesene Karte zählt aber zu deinem Abo-Kontingent.

1. Claude Code installieren (Anleitung: https://code.claude.com/docs/en/setup). Unter macOS/Linux:
   `curl -fsSL https://claude.ai/install.sh | bash`, unter Windows (PowerShell):
   `irm https://claude.ai/install.ps1 | iex`
2. Einmal `claude` im Terminal starten und mit deinem Claude-Konto anmelden.
3. In der `.env`: `ERKENNUNG_CLAUDE=unsicher`. Claude liest dann nur Karten mit Sicherheit
   „niedrig“ oder „nicht erkannt“ sowie Karten, deren mögliche Drucke sich im Preis stark
   unterscheiden (z. B. normal vs. Shiny). Mit `--claude unsicher` geht das auch für einen einzelnen
   Aufruf.

Das Modell stellst du mit `CLAUDE_MODELL` ein (`sonnet` schont das Kontingent, `opus` liest
genauer). Claude bekommt nur den Ausschnitt der einen Karte zu sehen und darf nichts anderes tun
als diese Bilddatei zu lesen.

### Trefferquote messen

```bash
python -m pokeauktion.auswerten               # nutzt testfotos/erwartet.txt
python -m pokeauktion.auswerten --claude unsicher
```

`testfotos/erwartet.txt` enthält pro Karte eine Zeile: `datei; reihe-spalte; name; sprache; hinweis`.
Neue Testfotos einfach dazulegen und die Liste ergänzen.

## Woher die Daten kommen

| Was | Quelle |
|---|---|
| Karten, Texte, Bilder | TCGdex REST `GET /v2/{sprache}/sets`, `/sets/{id}`, `/cards/{id}` und GraphQL `POST /v2/graphql` (Sprache über `@locale(lang: …)`) |
| Cardmarket-Preise | Feld `pricing.cardmarket` aus `GET /v2/{sprache}/cards/{id}`, in EUR. TCGdex übernimmt es aus dem offiziellen Cardmarket-Preisleitfaden. |
| Cardmarket-Link | Nur ein Link zur Cardmarket-Suche. Cardmarket wird **nicht** abgerufen oder gescrapt. |

Die Feldnamen stammen aus dem öffentlichen TCGdex-Quellcode (`meta/definitions/api.d.ts`,
`meta/definitions/graphql.gql`, `server/src/libs/providers/cardmarket.ts`) und den offiziellen SDKs.

### Wichtige Eigenheiten der Preise

- Cardmarket führt Preise **pro Produkt, nicht pro Sprache**. Der Trendpreis einer deutschen und
  einer englischen Karte desselben Drucks ist also derselbe. Japanische Karten sind auf Cardmarket
  eigene Produkte.
- Bei Cardmarket gelten die Felder ohne Zusatz für die Standardversion eines Produkts (bei
  Holo-Rares ist das die Holo-Karte). Die `*-holo`-Felder gelten für die Foil-Version, meist
  Reverse Holo. Die App zeigt beide als getrennte Versionen.
- Nicht jede Karte hat bei TCGdex eine Cardmarket-Zuordnung. Diese Karten erscheinen ohne Preis.
  Die Erkennung meldet das später ehrlich, statt einen Preis zu raten.

## Datenbankschema (Kurzfassung)

| Tabelle | Inhalt |
|---|---|
| `sets`, `set_namen` | Set-Stammdaten, Set-Namen je Sprache |
| `karten` | Sprachunabhängige Daten: Set, Nummer, KP, Typen, Stufe, Suffix, Seltenheit, Varianten, Cardmarket-Produkt-ID |
| `karten_texte` | Je Sprache: Name (auch normalisiert für die Suche), Attacken, Fähigkeiten, Referenzbild |
| `attacken` | Attacken einzeln, für die Suche nach Attackenname und Schaden |
| `preise` | Aktueller Cardmarket-Preis je Karte und Variante |
| `preis_verlauf` | Täglicher Trendpreis |
| `meta` | Zeitpunkte der letzten Abgleiche |

Referenzbilder werden nicht vorab heruntergeladen, das wären mehrere Gigabyte. Die Erkennung lädt
nur die Bilder der Kandidaten (kleine Auflösung) und speichert sie in `data/bilder/` zwischen.

`werkzeuge/offline_testdb.py` baut eine Test-Datenbank aus dem npm-Paket `@tcgdata/tcgdex-offline`,
ohne Preise und ohne Bilder. Das ist nur für Tests ohne Zugang zu TCGdex gedacht, die App nutzt
immer `python -m pokeauktion.sync`.

## Ausblick

- **Schritt 3:** eBay Browse API. Hier ergänze ich die Anleitung, wie du die eBay-Zugangsdaten
  bekommst.
- **Schritt 4:** Weboberfläche, Start mit einem einzigen Befehl.
