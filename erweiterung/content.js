// Läuft auf www.cardmarket.com, tut aber nur etwas, wenn die Adresse einen Auftrag der App trägt
// (#pka=<Nummer>). Ohne Auftrag bleibt die Seite unberührt.
const FILTER = {sellerCountry: "7", minCondition: "4"};   // 7 = Deutschland, 4 = Good oder besser
const KOPF = ["Produktinfo", "Product Information"];       // Spaltenüberschrift der Angebotstabelle
const PAUSE_MS = 3000;                                     // nach dem Laden kurz warten, wie beim Lesen

function auftragAusAdresse(href) {
  const m = /[#&]pka=(\d+)/.exec(href);
  return m ? m[1] : null;
}

function mitFilter(href, auftrag) {
  const u = new URL(href);
  for (const [k, v] of Object.entries(FILTER)) u.searchParams.set(k, v);
  u.hash = `pka=${auftrag}`;
  return u.toString();
}

function filterAktiv(href) {
  const u = new URL(href);
  return Object.entries(FILTER).every(([k, v]) => u.searchParams.get(k) === v);
}

// Aus den Suchtreffern den Link zur richtigen Karte wählen: "Name (KÜRZEL 020)".
function passenderTreffer(links, kuerzel, nummer) {
  const zahl = parseInt(nummer, 10);
  const kandidaten = [];
  for (const a of links) {
    const text = (a.textContent || "").trim();
    const m = /\(([A-Z0-9-]+)\s+([A-Z]*\d+[a-z]?)\)\s*$/.exec(text);
    if (!m || !a.href.includes("/Products/Singles/")) continue;
    const gleicheNummer = m[2] === nummer || (!isNaN(zahl) && parseInt(m[2].replace(/\D/g, ""), 10) === zahl
                                               && m[2].replace(/\d/g, "") === String(nummer).replace(/\d/g, ""));
    if (!gleicheNummer) continue;
    if (kuerzel && m[1] === kuerzel) return a.href;
    kandidaten.push(a.href);
  }
  return [...new Set(kandidaten)].length === 1 ? kandidaten[0] : null;
}

function angebotsText(text) {
  let i = -1;
  for (const k of KOPF) { const j = text.indexOf(k); if (j >= 0 && (i < 0 || j < i)) i = j; }
  return i < 0 ? null : text.slice(i, i + 1500);
}

function senden(nachricht) {
  return new Promise(ok => chrome.runtime.sendMessage(nachricht, ok));
}

function ohneAnker(href) { return href.split("#")[0]; }

async function weiter(antwort) {
  const ziel = antwort && (antwort.naechste_url || antwort.app_url);
  if (!ziel) return;
  const nurAnkerNeu = ohneAnker(ziel) === ohneAnker(location.href);
  location.href = ziel;
  // Ändert sich nur der Teil nach "#", lädt der Browser die Seite nicht neu: dann selbst neu laden
  if (nurAnkerNeu) location.reload();
}

async function arbeiten() {
  const id = auftragAusAdresse(location.href);
  if (!id) return;
  const titel = document.title.toLowerCase();
  if (titel.includes("moment") || titel.includes("sicherheits")) return; // Prüfseite: du bestätigst selbst
  await new Promise(r => setTimeout(r, PAUSE_MS));
  const auftrag = await senden({art: "auftrag", id});
  if (!auftrag || auftrag.fehler) { alert("Pokémon-Karten-Check: " + (auftrag?.fehler || "keine Antwort")); return; }

  if (location.pathname.includes("/Products/Singles/")) {
    if (!filterAktiv(location.href)) { location.href = mitFilter(location.href, id); return; }
    const text = angebotsText(document.body.innerText);
    await weiter(await senden({art: "ergebnis", id, url: location.href, text: text || "",
                               fehler: text ? null : "Keine Angebotstabelle gefunden"}));
    return;
  }
  if (location.pathname.includes("/Products/Search")) {
    const ziel = passenderTreffer(document.querySelectorAll("a[href]"), auftrag.kuerzel, auftrag.nummer);
    if (ziel) { location.href = mitFilter(ziel, id); return; }
    await weiter(await senden({art: "ergebnis", id, url: location.href, text: "",
                               fehler: `Karte (${auftrag.kuerzel} ${auftrag.nummer}) in der Suche nicht gefunden`}));
  }
}

if (typeof module !== "undefined") {
  module.exports = {auftragAusAdresse, mitFilter, filterAktiv, passenderTreffer, angebotsText};
} else {
  arbeiten();
}
