// Tests für die Logik der Browser-Erweiterung: node tests/test_erweiterung.js
const assert = require("assert");
const e = require("../erweiterung/content.js");

assert.strictEqual(e.auftragAusAdresse("https://www.cardmarket.com/de/x#pka=12"), "12");
assert.strictEqual(e.auftragAusAdresse("https://www.cardmarket.com/de/x"), null);
const gefiltert = e.mitFilter("https://www.cardmarket.com/de/Pokemon/Products/Singles/Crown-Zenith/Radiant-Charizard?language=1", 7);
assert.ok(e.filterAktiv(gefiltert) && gefiltert.endsWith("#pka=7") && gefiltert.includes("language=1"));
assert.ok(!e.filterAktiv("https://www.cardmarket.com/de/Pokemon/Products/Singles/a/b"));

const link = (text, href) => ({textContent: text, href});
const treffer = [
  link("Glurak (GEN RC5)", "https://www.cardmarket.com/de/Pokemon/Products/Singles/Generations/Charizard-RC5"),
  link("Strahlendes Glurak (PGO 011)", "https://www.cardmarket.com/de/Pokemon/Products/Singles/Pokemon-GO/Radiant-Charizard"),
  link("Strahlendes Glurak (CRZ 020)", "https://www.cardmarket.com/de/Pokemon/Products/Singles/Crown-Zenith/Radiant-Charizard-CRZ020"),
  link("Ab 6,90 €", "https://www.cardmarket.com/de/Pokemon/Products/Singles/Crown-Zenith/Radiant-Charizard-CRZ020"),
];
assert.ok(e.passenderTreffer(treffer, "CRZ", "020").endsWith("CRZ020"));
assert.ok(e.passenderTreffer(treffer, "XYZ", "020").endsWith("CRZ020"));   // Kürzel abweichend, Nummer eindeutig
assert.strictEqual(e.passenderTreffer(treffer, "CRZ", "099"), null);
assert.ok(e.passenderTreffer(treffer, "GEN", "RC5").endsWith("RC5"));

const seite = "ab 6,90 €\nVerkäuferstatus\nProduktinfo\nAngebot\nR0NIN\nGD\n8,99 €";
assert.ok(e.angebotsText(seite).startsWith("Produktinfo") && !e.angebotsText(seite).includes("6,90"));
assert.strictEqual(e.angebotsText("keine Tabelle"), null);
console.log("Erweiterung: alle Tests bestanden");

// Kennung steht nicht im Link, sondern in der Kachel darum herum (mit "Ab"-Preis dahinter)
const kachel = {textContent: "Strahlendes Glurak (CRZ 020) Ab 6,90 €", parentElement: null};
const bildlink = {textContent: "", title: "", href: "https://www.cardmarket.com/de/Pokemon/Products/Singles/Crown-Zenith/Radiant-Charizard", parentElement: kachel};
assert.ok(e.passenderTreffer([bildlink], "CRZ", "020").endsWith("Radiant-Charizard"));
const mitPreis = {textContent: "Strahlendes Glurak (CRZ 020) Ab 6,90 €", href: "https://www.cardmarket.com/de/Pokemon/Products/Singles/Crown-Zenith/Radiant-Charizard"};
assert.ok(e.passenderTreffer([mitPreis], "CRZ", "020"));
console.log("Erweiterung: Kacheln ok");
