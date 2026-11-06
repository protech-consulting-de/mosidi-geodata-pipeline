# Verifizierte öffentliche Quellen

Prüfstand: 5. November 2026. Der vollständige Import wird mit den unveränderten öffentlichen Originaldateien durchgeführt. Rohdateien werden nur im lokalen Cache gehalten.

## Gemeindegeometrien

- Anbieter: Landesvermessung und Geobasisinformation Brandenburg (LGB).
- WFS: https://isk.geobasis-bb.de/ows/vg_wfs
- Feature-Typ: `app:gem`; Gemeindeschlüssel `ags`, Name `gem_name`, Regionalschlüssel `ars`.
- Nachgewiesene Anzahl: 413, getrennt per `resultType=hits` ermittelt und gegen alle tatsächlichen Elemente geprüft.
- Ausgabe: GML 3.2. Der Dienst meldet im normalen Ergebnis teilweise `numberReturned="0"`, obwohl Elemente enthalten sind. Die Software zählt deshalb die wirklichen Elemente.
- `urn:ogc:def:crs:EPSG::4326` liefert hier Breitengrad vor Längengrad. Die Konfiguration verwendet `axis_order: yx` und validiert Brandenburgs erwarteten Bereich.
- Lizenz: Datenlizenz Deutschland Namensnennung 2.0, https://www.govdata.de/dl-de/by-2-0
- Quellenvermerk: `© GeoBasis-DE/LGB, dl-de/by-2-0`.

## INKAR

- Anbieter: Bundesinstitut für Bau-, Stadt- und Raumforschung (BBSR).
- Originalarchiv: https://www.bbr-server.de/imagemap/inkar/download/inkar_2025.zip
- Mitglied: `inkar_2025.csv`; Semikolon, UTF-8, Dezimalkomma.
- Größe des geprüften Archivs: 433.790.695 Bytes. SHA-256: `82f6923a4bdce8f3d084a65574f5b8781391fa8a1a608ab4f366ca903b485bcc`.
- Die CSV hat 6.762.930.064 Bytes und verwendet ZIP64 sowie Deflate64. Der eigene Archivleser verarbeitet dies als Stream und prüft Größe, CRC und Ende des Streams.
- Demofilter: Bereich `LRB`, ID `2176`, Kuerzel `alo`, Raumbezug `Gemeinden`, Zeitbezug `2023`, Kennziffer beginnt mit `12`.
- Ergebnis: Arbeitslose, Jahresdurchschnitt 2023, Personen, für 413 Gemeinden. Keine Arbeitslosenquote.
- Fachlicher Schlüssel: Kennziffer, Bereich, Kuerzel, Indikator, Zeitbezug.
- Lizenz laut originaler Metadaten-Datei: Datenlizenz Deutschland Namensnennung 2.0.
- Quellenvermerk: BBSR, INKAR, Ausgabe 2025, mit Link zur Originalquelle.
- Die Metadaten-ID darf nicht blind mit der CSV-ID gleichgesetzt werden. Der zugehörige Kurzname `alo` verbindet die dokumentierten Bezeichnungen. Diese Demo übernimmt den nachgewiesenen Indikatornamen und protokolliert die Auswahl.

## Einwohner aus dem Zensus 2022

- Anbieter: Amt für Statistik Berlin-Brandenburg.
- Originaldatei: https://download.statistik-berlin-brandenburg.de/07afbdeaedfc6def/b6ac8d6506f3/Zensus2022-Daten-Dashboard-Brandenburg-Gemeinden.xlsx
- Blatt: `GESCHLECHT_BB_GEM`, Gemeindezeilen ab Zeile 12. A = Regionalschlüssel, B = Name, C = Einwohner insgesamt.
- Stichtag: 15. Mai 2022. Datei/Quellenvermerk: Potsdam 2024.
- Der Adapter erzeugt eine CSV im Cache und dokumentiert Originalprüfsumme, Tabellenblatt, Spalten, Filter und Prüfsumme des abgeleiteten Ergebnisses.
- Numerisch gespeicherte Regionalschlüssel werden zunächst auf zwölf Stellen aufgefüllt. Der AGS wird als erste fünf plus letzte drei Stellen gebildet. Alle 413 Gemeinden sind mit dem WFS verbunden.
- Lizenzhinweis in der Originaldatei: Weitergabe und Vervielfältigung mit Quellenangabe.
- Quellenvermerk: `© Amt für Statistik Berlin-Brandenburg, Potsdam 2024; Zensus 2022`.

## Abgrenzung

Die öffentlich verfügbaren Beispieldaten sind echte Behördenquellen und passen fachlich zusammen. Sie ersetzen nicht die noch ausstehende Auswahl der konkreten WFS-Layer und AfS-Tabellen durch die Hochschule. Die ursprüngliche Ausschreibung fordert CSV-Anbindung; diese wird durch INKAR und den allgemeinen CSV-Adapter nachgewiesen. Die zusätzliche XLSX-Normalisierung erschließt eine passende AfS-Quelle, ohne sie als ursprünglich bereitgestellte CSV auszugeben.
