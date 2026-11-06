# Integration und Grenzen des Datenbankprofils

Geprüfter öffentlicher Stand: `MOSIDI-ORG/MOSIDI`, Commit `26cadbba3d6223c84872987104fc737359bf8bfc`, abgerufen am 05.11.2026.

## Bekanntes Zielmodell

| Tabelle | Verwendete Felder | Verhalten |
|---|---|---|
| `Kommunale Gebiete Deutschland` | `id`, `geom`, `nationalco`, `name` | Geometrien aktualisieren/ergänzen, vorhandene IDs erhalten, nie löschen |
| `dashboard_data_de` | `id`, `indikator`, `kennziffer`, `wert`, `zeitbezug`, `granularity`, `source`, `data_type` | Nur eindeutig zugeordnete Attribute ersetzen/entfernen |
| `table_metadata` | DCAT-nahe Metadaten, Titel, Herausgeber, Zeitraum und Katalogschlüssel | Nach erfolgreichem Import aktualisieren |
| `source_granularities` | materialisierte Übersicht von Quelle, Indikator und Raumebene | In derselben Importtransaktion aktualisieren |

Das eingecheckte Schema hat für `table_metadata` nur `table_name` als Primärschlüssel. Das jüngere Metadaten-Skript erwartet dagegen `(table_name, dcatde_politicalgeocodingleveluri)` und zusätzliche Felder `dcat_ap_id` und `dcat_ap_title`. Deshalb wird keine ungeprüfte direkte Kompatibilität mit jeder MOSIDI-Installation behauptet.

Die mitgelieferte SQL-Datei beschreibt ein eigenständig formuliertes **Demo-Kompatibilitätsprofil** auf Basis dieser öffentlich sichtbaren Spalten. Sie ist kein Export einer Hochschuldatenbank. `init-demo` erzeugt dieses Profil ausdrücklich nur in einer leeren Datenbank. Eine bestehende Datenbankstruktur wird während `run` nicht geändert.

## Vor einer produktiven Verbindung

1. Den von der Hochschule angekündigten verbindlichen Schema-Dump erhalten und mit Tabellen, Typen, Primärschlüsseln, Geometrietypen und Katalogverknüpfungen abgleichen.
2. Die endgültigen WFS-Dienste/Layer, INKAR-Auswahl und AfS-Tabellen in `config/mosidi.yaml` eintragen. Neue Auswahlbereiche möglichst unter neuen Quellen-IDs führen.
3. Einen Datenbankbenutzer mit den benötigten Lese-/Schreibrechten auf den Zieltabellen und Rechten für das eigene Schema `pipeline_meta` bereitstellen. Die Auffrischung einer Materialized View benötigt passende Rechte.
4. Zunächst `check`, anschließend `run --dry-run` ausführen und Bericht, Zuordnung und Änderungen prüfen.
5. Erst nach diesem Abgleich den echten Import ausführen und alle vereinbarten Quellen auf der finalen Konfiguration erneut testen.

Wenn das Schema abweicht, beendet die Vorprüfung den Import mit einer konkreten Fehlermeldung. Änderungen am Adapter werden dann auf den gelieferten Dump abgestimmt. Ein fehlendes `source_granularities` wird als Integrationswarnung ausgewiesen; die Pipeline erzeugt diese Zielstruktur nicht automatisch.

## Kontrolltabellen

Die Pipeline nutzt ausschließlich ihr eigenes Schema für zusätzliche Steuerungsdaten:

- `source_records`: Zuordnung von Quellen-ID und fachlichem Schlüssel zur Zielzeile. Sie begrenzt Löschungen und verhindert Übernahme fremder Attribute.
- `source_config`: Fingerabdruck des bisherigen Quellenumfangs. Änderungen an Filtern, Mapping oder URL werden ohne explizite Freigabe verweigert.
- `source_runs`: dauerhafter Laufnachweis; erfolgreiche Änderungszahlen werden mit den Zieländerungen in derselben Transaktion gespeichert.
- `source_catalog` und `catalog_state`: Herkunft und Zuständigkeit für gemeinsame Katalogeinträge. Zeiträume werden aus allen verbleibenden Datensätzen der jeweiligen Gruppe neu ermittelt.

Das vorhandene Katalogmodell kann für denselben Indikator und dieselbe Raumebene nicht beliebig mehrere Herausgeber darstellen. Solche Konflikte werden sicher abgebrochen. Bei mehreren unterschiedlichen URLs derselben Kataloggruppe wird keine irreführende einzelne Herkunfts-URL behauptet.

## Nachvollziehbarkeit gegenüber dem Konzept

Implementiert sind Quellenkonfiguration, WFS/CSV-Import, eine zusätzliche XLSX-Normalisierung, transaktionale Datenübernahme, geschützte Löschung, Dry Run, JSON/HTML-Berichte, persistente Laufbelege, Zeitsteuerung, Tests und Docker-/CI-Konfiguration. Die Demo testet sämtliche drei mitgelieferten Quellen vollständig.

Noch projektbezogen festzulegen sind der verbindliche Hochschul-Schema-Dump, die endgültige Auswahl weiterer WFS-Layer und statistischer Tabellen sowie die Abnahme in der Auftraggeberumgebung. Live nachgewiesen ist der konfigurierte WFS-2.0-Dienst. WFS 1.1, zusätzliche Geometrietypen oder fremde Formate dürfen nicht ohne eigenen Integrationsnachweis als fertig angeschlossen dargestellt werden.

## Öffentliche Grundlagen

- https://github.com/MOSIDI-ORG/MOSIDI/blob/26cadbba3d6223c84872987104fc737359bf8bfc/services/backend/src/db_table_schema/schema.sqlalchemy
- https://github.com/MOSIDI-ORG/MOSIDI/blob/26cadbba3d6223c84872987104fc737359bf8bfc/services/backend/src/db_table_metadata/script.py
- https://github.com/MOSIDI-ORG/MOSIDI/blob/26cadbba3d6223c84872987104fc737359bf8bfc/services/backend/src/inkar_import/config.xml

Der neue Code ist eigenständig. Destruktive Tabellenneuanlage und numerische Umwandlung von Gemeindeschlüsseln aus den vorhandenen Importskripten werden nicht übernommen.
