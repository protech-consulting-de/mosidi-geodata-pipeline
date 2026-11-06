# Nachgewiesener Stand der Demonstration

Version 0.1.0, geprüft am 5. November 2026. Alle hier genannten Datenbankergebnisse stammen aus echten Abrufen der öffentlichen Originalquellen. Tests mit künstlichen Fehlerfällen werden getrennt ausgewiesen.

## Live-Ergebnis

| Prüfung | Ergebnis |
|---|---|
| Gemeindegeometrien aus dem LGB-WFS | 413 importiert, alle gültig, EPSG:4326 |
| INKAR Arbeitslose, Jahresdurchschnitt 2023 | 413 Werte importiert |
| AfS Einwohner, Zensus 15.05.2022 | 413 Werte importiert |
| Verknüpfung über amtlichen Gemeindeschlüssel | Alle 826 Statistikwerte einer Gemeinde zugeordnet |
| Wiederholung aller drei Quellen | Je 413 unverändert, keine neuen/geänderten/entfernten Datensätze |
| Probelauf aller drei Quellen | Erfolgreich, keine Datenbankänderungen |
| Wiederherstellung eines Berichts aus der Datenbank | Erfolgreicher Export der drei Quellenbelege |

Der INKAR-Import hat die gesamte CSV mit **63.426.864 Datenzeilen** durchlaufen und daraus den konfigurierten Ausschnitt ausgewählt. Die ZIP-Mitgliedsgröße von 6.762.930.064 Bytes und die CRC wurden am Streamende geprüft. Der Cache enthält das 433.790.695 Byte große Originalarchiv, nicht eine selbst zusammengestellte Eingabedatei. Die AfS-XLSX wurde reproduzierbar nach CSV normalisiert.

Beispiel Brandenburg an der Havel, AGS `12051000`: 3.281 Arbeitslose im Jahresdurchschnitt 2023 und 73.552 Einwohner zum Zensusstichtag 2022. Diese Werte beschreiben unterschiedliche Zeiträume und werden nicht zu einer Arbeitslosenquote kombiniert.

## Belege zum Öffnen

- [Wiederholung: HTML-Bericht](evidence/wiederholung.html) und [JSON](evidence/wiederholung.json).
- [Probelauf: HTML-Bericht](evidence/probelauf.html) und [JSON](evidence/probelauf.json).
- [Vorher-/Nachher-Prüfsummen des Probelaufs](evidence/dry-run-database-verification.json): acht Tabellen einschließlich der Kontrolltabellen sowie zwei Sequenzen unverändert. Die PostGIS-Systemtabelle und die materialisierte Sicht sind nicht Teil dieses Fingerabdrucks.
- [SQL-Prüfungen des Zielbestands](evidence/database-verification.json): Zählwerte, Geometriegültigkeit, Verknüpfung und konkretes Beispiel.
- [Wiederhergestellter Bericht](evidence/wiederhergestellt.html), sichtbar als Wiederherstellung gekennzeichnet.

Die ursprünglichen Laufzeitstempel und Lauf-IDs bleiben in den Belegen erhalten. Die Berichte enthalten keine Zugangsdaten. Die Quellenvermerke und Datenstände gehören zu den Nachweisen; bei Weitergabe beibehalten.

## Automatisierte Prüfung

**91 Tests bestanden**, vollständiger Lauf einschließlich PostgreSQL/PostGIS-Integration: `91 passed in 11.53s`.

Abgedeckt sind unter anderem vollständige WFS-Seitennavigation, Achsenreihenfolge, Koordinatentransformation, Fehlerberichte, stabile Schlüssel, CSV-/ZIP-Integrität, Deflate64, XLSX-Normalisierung, Cacheintegrität, keine Datenänderung bei Dry Run, Transaktionsrücknahme, Schutz fremder Attribute und gemeinsamer Geometrien, Änderungen des Quellenumfangs, gemeinsame Metadaten, persistente Laufbelege und sichere HTML-Ausgabe.

Die Datenbanktests verwenden eigene temporäre Datenbanken. Der Live-End-to-End-Test verwendet eine getrennte Demo-Datenbank mit den oben genannten Behördenquellen. Die GitHub-Action ist vorbereitet; ein tatsächlicher GitHub-CI-Lauf wurde noch nicht ausgelöst.

## Laufzeit und Installation

- Python 3.12.14 auf dem lokalen Rechner; PostgreSQL 17.11 mit PostGIS 3.7.0 im isolierten Docker-Dienst.
- Installation als Python-Paket sowie separat gebautes Wheel geprüft. Das zur Demo-Initialisierung benötigte SQL ist im Paket enthalten; Initialisierung einer leeren Datenbank erfolgreich, Wiederholung bei bestehenden Tabellen verweigert.
- Endgültiger Docker-Build mit festgeschriebenen Abhängigkeiten erfolgreich: Image `mosidi-geodata-pipeline:0.1.0`.
- **Containerstart der Pipeline auf diesem Rechner nicht erfolgreich verifiziert:** Neu angelegte Python-Container blieben im lokalen Docker Desktop im Zustand `Created`, noch bevor Python startete. Der Datenbankcontainer läuft. Der tatsächliche Import wurde deshalb mit lokalem Python gegen diese PostGIS-Datenbank ausgeführt. Der Docker-Dienst wurde nicht neu gestartet; fremde Container wurden nicht verändert.

## Noch projektbezogen offen

Das endgültige THB-Schema wird laut Vergabeunterlagen erst bei Beauftragung bereitgestellt. Diese Demonstration verwendet das offengelegte Profil in `sql/demo_schema.sql`; sie ist keine Abnahme in der Hochschulumgebung. Der aktuelle Geometrie-Datenbankadapter schreibt Gemeinde-MultiPolygone. Punkt-/Linien-Zieltabellen, weitere konkrete Layer und die abschließende INKAR-/AfS-Auswahl müssen mit dem verbindlichen Zielschema verbunden und jeweils getestet werden. Die Konfiguration belegt einen vollständigen INKAR-Indikator, nicht den Import aller Indikatoren und Raumebenen.

WFS 2.0 ist mit dem genannten Live-Dienst geprüft. WFS 1.1 erfordert nachgewiesene Seitennavigation des jeweiligen Dienstes; Parser- und Fehlerfalltests ersetzen dort keinen eigenen Live-Nachweis. Die öffentliche GitHub-Veröffentlichung und eine Beauftragung durch die Hochschule werden nicht behauptet.
