# Pokémon-Auktions-Check

Lokale Web-App, die eBay-Auktionen mit Pokémon-Karten auswertet: Sie erkennt die Karten auf den
Fotos und zeigt den Cardmarket-Trendpreis jeder Karte sowie die Summe als Spanne.

> **Stand: Schritt 1 von 5. Die Kartendatenbank mit dem Abgleich aus TCGdex ist fertig.**
> Erkennung (2), eBay-Anbindung (3), Weboberfläche (4) und Motivsuche (5) folgen.

Hinweis: Die Preise sind Cardmarket-**Trendwerte** und gelten für gut erhaltene Karten. Der Zustand
beeinflusst den Preis stark. Zustandsbewertung, Echtheitsprüfung und automatisches Bieten gehören
nicht zur App.

---

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

Für Schritt 1 musst du in der `.env` nichts eintragen.

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

Referenzbilder werden nicht vorab heruntergeladen, das wären mehrere Gigabyte. Ab Schritt 2
lädt die Erkennung nur die Bilder der Kandidaten und speichert sie zwischen.

## Ausblick

- **Schritt 2:** Erkennung als Skript, Foto rein, Kartenliste mit Preisen raus. Dafür brauche ich
  den Ordner `testfotos/` mit Beispielfotos und einen `ANTHROPIC_API_KEY` in der `.env`.
- **Schritt 3:** eBay Browse API. Hier ergänze ich die Anleitung, wie du die eBay-Zugangsdaten
  bekommst.
- **Schritt 4:** Weboberfläche, Start mit einem einzigen Befehl.
