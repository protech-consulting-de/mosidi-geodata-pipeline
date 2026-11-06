# Drittkomponenten

Getestete Python-Laufzeitabhängigkeiten, Stand 05.11.2026. Die Lizenzangaben stammen aus den Metadaten der installierten Distributionen. Die eigene Apache-2.0-Lizenz ersetzt diese Bedingungen nicht. Unveränderte Bibliotheken werden bei Installation über ihre offiziellen Paketdistributionen bezogen.

| Paket | Version | Lizenz laut Paketmetadaten |
|---|---|---|
| requests | 2.34.2 | Apache-2.0 |
| psycopg | 3.3.8 | LGPL-3.0-only |
| psycopg-binary | 3.3.8 | LGPL-3.0-only |
| PyYAML | 6.0.3 | MIT |
| shapely | 2.2.0 | BSD-3-Clause |
| pyproj | 3.8.0 | MIT |
| defusedxml | 0.7.1 | PSFL |
| inflate64 | 1.0.4 | LGPL-2.1-or-later |
| openpyxl | 3.1.5 | MIT |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| urllib3 | 2.8.0 | MIT |
| certifi | 2026.10.23 | MPL-2.0 |
| charset-normalizer | 3.5.2 | MIT |
| idna | 3.20 | BSD-3-Clause |
| typing-extensions | 4.16.0 | PSF-2.0 |
| et-xmlfile | 2.0.0 | MIT |

Die binären Distributionen enthalten weitere native Bestandteile, insbesondere GEOS, PROJ und PostgreSQL-Clientbibliotheken. Ihre zugehörigen Lizenztexte sind Bestandteil der jeweiligen Wheels und bleiben bei der Installation erhalten. PostGIS wird als eigenständiger Dienst im offiziellen Container betrieben. Python- und PostGIS-Image sind im Docker-/Compose-Setup per Digest festgelegt.

Für eine Weitergabe selbst gebauter Binärpakete oder Container müssen die darin enthaltenen Lizenz- und Quellcodehinweise der Drittkomponenten ebenfalls erhalten bleiben. Der hier bereitgestellte Projektquellcode bündelt keine fremden Binärbibliotheken.

Entwicklungswerkzeuge (separat in requirements-dev.lock): pytest (MIT), iniconfig (MIT), packaging (Apache-2.0 oder BSD), pluggy (MIT), Pygments (BSD).
