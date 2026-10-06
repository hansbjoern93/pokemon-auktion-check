// Verbindung zur lokalen App. Die Inhaltsseite von Cardmarket darf localhost nicht selbst erreichen,
// die Erweiterung schon (host_permissions).
const APP = "http://127.0.0.1:8000";

chrome.runtime.onMessage.addListener((nachricht, _absender, antworten) => {
  (async () => {
    try {
      if (nachricht.art === "auftrag") {
        const r = await fetch(`${APP}/api/auftrag/${nachricht.id}`);
        antworten(await r.json());
      } else if (nachricht.art === "ergebnis") {
        const r = await fetch(`${APP}/api/auftrag/${nachricht.id}/ergebnis`, {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({url: nachricht.url, text: nachricht.text, fehler: nachricht.fehler || null}),
        });
        antworten(await r.json());
      }
    } catch (e) {
      antworten({fehler: "Die App ist nicht erreichbar. Läuft start.bat?"});
    }
  })();
  return true; // Antwort kommt asynchron
});
