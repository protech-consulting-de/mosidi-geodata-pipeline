# MOSIDI Geodaten Import Pipeline

Ausführbare Python-Pipeline für öffentliche Gemeindegeometrien und statistische Daten. Sie importiert WFS- und CSV-Daten nach PostgreSQL/PostGIS, schützt vorhandene Bestände und erzeugt JSON- sowie HTML-Berichte. Ein zusätzlicher Adapter normalisiert die amtliche Zensus-XLSX-Datei in eine nachvollziehbare CSV.

Die mitgelieferte Konfiguration verbindet **413 Brandenburg-Gemeinden** mit **413 INKAR-Werten** und **413 Zensuswerten**. Die Originaldaten werden zur Laufzeit von den Behörden geladen und nicht mit dem Quellcode ausgeliefert.

**Geprüfter Stand:** Alle drei Quellen live importiert und vollständig wiederholt; alle 826 Statistikwerte mit Gemeindegeometrien verbunden; 91 Tests bestanden. [Nachweise und verbleibende Integrationsschritte](docs/VALIDIERUNG.md) · [Importbericht öffnen](docs/evidence/wiederholung.html) · [SQL-Beispielabfragen](docs/BEISPIELABFRAGEN.sql).

## Verbindungsumfang der Demo

| Quelle | Verbundene Daten | Zeitbezug |
|---|---|---|
| GeoBasis-DE/LGB, WFS `app:gem` | 413 Gemeindegeometrien, AGS, Name | aktueller Abrufstand des Dienstes |
| BBSR INKAR, Ausgabe 2025 | Arbeitslose, 413 Gemeinden, Kennung `alo` | Jahresdurchschnitt 2023, Personen |
| Amt für Statistik Berlin-Brandenburg | Einwohner, 413 Gemeinden | Zensus zum 15.05.2022 |

Die Daten werden über den achtstelligen amtlichen Gemeindeschlüssel verbunden. Beim Zensus wird der zwölfstellige Regionalschlüssel deterministisch in den Gemeindeschlüssel überführt. Unterschiedliche Zeitbezüge bleiben erhalten; die Software berechnet daraus keine scheinbar aktuelle Arbeitslosenquote.

Quellen, Lizenzangaben und Besonderheiten sind in [docs/QUELLEN.md](docs/QUELLEN.md) dokumentiert. Umfang und Filter stehen in [config/demo.yaml](config/demo.yaml).

## Schnellstart mit Docker

Benötigt werden Docker mit Compose und Internetzugang. Alle Befehle im Projektordner ausführen:

Prüfstand 05.11.2026: Compose-Konfiguration, Image-Builds für arm64/amd64 und der PostGIS-Dienst wurden erfolgreich geprüft. Auf dem verwendeten Docker-Desktop-Host blieb der Pipeline-Container vor dem Programmstart im Zustand `Created`; der Containerstart ist dort daher noch nicht nachgewiesen. Alle drei Quellen wurden mit lokalem Python gegen denselben PostGIS-Dienst importiert. Bei diesem Startproblem den folgenden Abschnitt zur lokalen Python-Ausführung verwenden.

```sh
docker compose up -d db
docker compose run --rm --build pipeline init-demo
docker compose run --rm pipeline check
docker compose run --rm pipeline run --dry-run
docker compose run --rm pipeline run
```

`init-demo` funktioniert ausschließlich in einer leeren Datenbank. Ein erneuter Aufruf bei bestehenden Tabellen bricht ab. Für weitere Imports nur `run` verwenden.

Der erste komplette Abruf lädt das **434 MB große INKAR-Archiv**. Die darin enthaltene CSV ist etwa **6,76 GB** groß und wird ohne vollständiges Entpacken auf die Festplatte durchlaufen. Die Pipeline prüft das gesamte Mitglied einschließlich CRC und Größe; im Speicher verbleiben nur die ausgewählten Datensätze. Genügend Platz für Archivcache und Datenbank vorsehen. Für einen schnellen ersten Lauf genügen Gemeindegeometrien und Zensus:

```sh
docker compose run --rm pipeline run --source brandenburg_gemeinden --source afs_zensus2022_einwohner_bb
```

Die lokalen Berichte liegen anschließend in `reports/`. Die HTML-Datei lässt sich direkt im Browser öffnen. Ein unveränderter zweiter Lauf meldet unveränderte Datensätze und legt keine Duplikate an.

```sh
docker compose run --rm pipeline run
docker compose run --rm pipeline schedule
```

Der Scheduler führt fällige Quellen nacheinander aus. Die Konfiguration sieht wöchentliche Geometrieimporte und 30-tägige Statistikimporte vor. Die historischen INKAR-/Zensus-Datensätze wechseln dadurch nicht automatisch auf ein anderes Jahr. Für einen neuen Datenstand eine eigene Quellen-ID oder eine ausdrücklich geprüfte Änderung des Quellenumfangs verwenden. Bei einem Neustart des Schedulers werden die ausgewählten Quellen erneut geprüft.

Die Demo-Datenbank ist nur unter `127.0.0.1:55436` erreichbar. Ihr voreingestelltes Passwort dient ausschließlich dieser lokalen Demo. `docker compose stop` beendet sie und erhält Daten sowie Berichte. Der Datenbankdienst nutzt das verifizierte offizielle PostGIS-Image; auf Apple Silicon läuft dessen amd64-Version unter Emulation.

## Ausführung mit lokalem Python

Die Anwendung wurde mit Python 3.12 geprüft. PostgreSQL/PostGIS kann dabei weiter im Docker-Dienst laufen:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.lock
pip install --no-deps .
export DATABASE_URL='postgresql://pipeline_demo:demo-only-change-me@127.0.0.1:55436/mosidi_demo'
```

Wenn die Demo-Datenbank noch nicht gestartet und initialisiert wurde, einmalig ausführen. Bei einer bereits initialisierten Demo `init-demo` auslassen:

```sh
docker compose up -d --wait db
python -m geopipeline --config config/demo.yaml init-demo
```

Anschließend prüfen und importieren:

```sh
python -m geopipeline --config config/demo.yaml check
python -m geopipeline --config config/demo.yaml run
```

Globale Optionen stehen vor dem Unterbefehl: `--config`, `--dsn`, `--cache-dir`, `--report-dir`. Quellen lassen sich durch wiederholtes `--source QUELLEN_ID` auswählen. `run --dry-run` liest und vergleicht, verändert aber weder fachliche Daten noch Kontrolltabellen. Cache und Berichtsdateien werden auch bei einem Prüflauf lokal geschrieben.

Rückgabewerte: `0` = erfolgreich, gegebenenfalls mit sichtbaren Warnungen; `1` = mindestens eine Quelle oder der Lauf fehlgeschlagen; `2` = Konfigurations-/Bedienfehler; `130` = unterbrochen. Der Bericht unterscheidet übernommen, Vorschau, Warnung und Fehler.

## Sicherheit der Datenübernahme

- Vollständiger WFS-Abruf mit separatem `hits`-Zählwert vor und nach der Seitennavigation. Doppelte Schlüssel, wiederholte Seiten, fehlende Seiten und geänderte Gesamtzahlen brechen die Quelle ab.
- Die Gemeindegeometrien werden in EPSG:4326 gespeichert. Achsenreihenfolge, Gültigkeit und der erwartete geografische Bereich werden geprüft.
- Attributänderungen, Quellenzuordnung, Katalogaktualisierung und erfolgreicher Laufnachweis werden in einer Transaktion gespeichert. Ein Fehler nimmt diese Transaktion zurück.
- Ein Datenbankschloss verhindert konkurrierende Pipeline-Schreibvorgänge. Unabhängige externe Schreiber müssen dieselben Betriebsregeln respektieren.
- Attribute dürfen nur gelöscht werden, wenn diese Pipeline sie derselben Quellen-ID eindeutig zuordnet. Vorhandene fremde Daten werden nicht übernommen, solange `adopt_existing` nicht ausdrücklich gesetzt ist.
- Gemeinsam genutzte Geometrien werden nie gelöscht; vorhandene IDs bleiben erhalten. Fehlende Geometrien werden als erhaltene Altbestände ausgewiesen.
- Ungültige Datensätze mit bekanntem Schlüssel schützen den bisherigen Datensatz vor Löschung. Bei fehlendem Schlüssel werden Löschungen für die gesamte Quelle gesperrt.
- Änderungen an Quelle, Filtern, Zuordnung oder Schlüsseln einer bereits verwendeten Quellen-ID werden blockiert. Eine geprüfte Umstellung benötigt `accept_scope_change: true`; der neue Umfang wird danach gespeichert.
- Eine ausgefallene Quelle verhindert weitere Quellen nicht. Der Gesamtlauf ist bei einem Quellenfehler trotzdem fehlgeschlagen.

Ein WFS bietet keinen datenbankweiten Snapshot über mehrere HTTP-Anfragen. Die beschriebenen Prüfungen erkennen viele, aber nicht jede theoretische Änderung während des Abrufs. Quellenausfälle oder unklare Vollständigkeit werden niemals als erfolgreicher Import ausgegeben.

## Berichte und Wiederherstellung

Ein Lauf erzeugt zwei zusammengehörige Dateien: maschinenlesbares JSON und eine eigenständige HTML-Zusammenfassung. Sie enthalten Herkunft, Datenprüfsummen, Filter, Anzahl gelesener/übernommener/ausgelassener Datensätze, Änderungen und Fehlermeldungen. Die HTML-Ausgabe maskiert Dateninhalte und benötigt keine externen Skripte.

Für echte Imports speichert `pipeline_meta.source_runs` außerdem den Quellenstatus und die erfolgreichen Änderungszahlen. Falls die Dateiausgabe unterbrochen wird, lassen sich diese Belege wieder exportieren:

```sh
docker compose run --rm pipeline export-report LAUF_ID
```

Wiederhergestellte Berichte sind ausdrücklich gekennzeichnet. Ein nach Abbruch verbliebener Status `running` ist kein Erfolgsnachweis. Die atomare Speicherung gilt je Datei; JSON und HTML bilden keine gemeinsame Dateisystemtransaktion.

## Integration in die Hochschulumgebung

**Die Demo ist noch keine freigegebene Installation in der THB-Produktivumgebung.** Das öffentlich sichtbare MOSIDI-Schema und dessen Metadaten-Skript widersprechen sich. Die Ausschreibung kündigt einen verbindlichen Schema-Dump erst bei Beauftragung an. Die mitgelieferte Datenbank ist daher ein offengelegtes Kompatibilitätsprofil, keine Behauptung über das endgültige Zielschema.

[docs/MOSIDI_INTEGRATION.md](docs/MOSIDI_INTEGRATION.md) beschreibt die bekannte Struktur und die Anpassung. `check` prüft Spalten, Schlüssel und PostGIS-Geometrietyp vor dem Import. Die Pipeline verändert keine vorhandene Tabellenstruktur. Sie legt lediglich ihre ausdrücklich vorgesehenen Kontrolltabellen in `pipeline_meta` an.

Die zusätzliche Datei `compose.mosidi.yaml` ergänzt einen Dienst im bestehenden Compose-Projekt. Sie ersetzt keine Datenbank und startet standardmäßig nur die Prüfung:

```sh
export GEOPIPELINE_PROJECT_DIR='/absoluter/pfad/mosidi-geodata-pipeline'
export DATABASE_URL='postgresql://BENUTZER:PASSWORT@brandenburg_db:5432/DATENBANK'
docker compose -f /pfad/MOSIDI/docker-compose.yml -f "$GEOPIPELINE_PROJECT_DIR/compose.mosidi.yaml" run --rm --build geopipeline check
```

`config/mosidi.yaml` enthält dieselben Beispielquellen und muss auf die endgültig vereinbarten Dienste, Tabellen und Kennzahlen abgestimmt werden. **`init-demo` niemals zur Vorbereitung einer bestehenden Hochschuldatenbank verwenden.**

## Neue Quellen

Neue CSV-Quellen benötigen `url` oder einen zur Konfigurationsdatei relativen `path`, Trennzeichen/Encoding, Filter, `key_fields` und eine Feldzuordnung. Pflichtzielfelder sind `indikator`, `kennziffer`, `wert`, `zeitbezug`. Konstanten werden als `{constant: Wert}` angegeben. Die Transformation `ars_to_ags` ist für zwölfstellige Regionalschlüssel vorgesehen. Führende Nullen bleiben erhalten.

WFS-Quellen benötigen zusätzlich Feature-Typ, ID-/Namensfeld und eine ausdrücklich geprüfte Achsenreihenfolge. Live verifiziert ist WFS 2.0.0/GML des LGB-Dienstes. WFS 1.1.0 setzt bestätigte STARTINDEX-Unterstützung voraus und bricht ohne `paging_supported: true` ab. Der vorliegende MOSIDI-Geometrieadapter schreibt Gemeinde-MultiPolygone. Punkt-/Linien-Zieltabellen benötigen ein separat abgestimmtes Datenbankprofil.

Große Datenquellen werden vollständig gelesen, aber nur der konfigurierte Ausschnitt wird übernommen. Für den vollständigen INKAR-Katalog müssten Auswahl, Zieltabelle, Speicherbedarf und Metadatenumfang gesondert abgestimmt werden; die Demo bietet bewusst einen vollständig überprüften Indikator.

## Tests

```sh
pip install -r requirements-dev.lock
pip install --no-deps .
pytest -q
export TEST_DATABASE_URL='postgresql://pipeline_demo:demo-only-change-me@127.0.0.1:55436/mosidi_demo'
pytest -q
```

Ohne `TEST_DATABASE_URL` werden Datenbanktests übersprungen. Mit dieser Variable erstellen und entfernen die Tests eigene temporäre Datenbanken; die angegebene Verbindungsdatenbank wird nicht geleert. Der Testbenutzer benötigt dafür `CREATEDB` und die Möglichkeit, PostGIS zu aktivieren. Live-Behördenabrufe sind von der automatischen Testsuite getrennt; die GitHub-Action benötigt dafür keine öffentlichen Datenquellen.

## Lizenz und Herkunft des Codes

Der neue Pipeline-Code wird unter Apache-2.0 bereitgestellt. Er wurde eigenständig implementiert. Aus dem MOSIDI-Repository wurden keine Importskripte kopiert, weil dort beim geprüften Stand keine eindeutige projektweite Lizenzdatei vorhanden war. Die Daten behalten die in [docs/QUELLEN.md](docs/QUELLEN.md) genannten Bedingungen. Abhängigkeiten behalten ihre jeweiligen Lizenzen; siehe [THIRD_PARTY.md](THIRD_PARTY.md).

GitHub-Veröffentlichung: Quellcode, Konfigurationen, Tests und Dokumentation können in ein eigenes Repository übernommen werden. `.env`, `.cache`, `reports`, lokale Datenbanken und heruntergeladene Quelldaten gehören nicht in das Repository. Das Projekt als eigene technische Demonstration kennzeichnen; die erfolgte Beauftragung durch die Hochschule wird nicht behauptet.
