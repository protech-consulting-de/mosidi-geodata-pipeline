-- Schreibgeschützte Beispiele für die lokale Demo-Datenbank.
-- Quellen und unterschiedliche Datenstände: siehe QUELLEN.md.

-- Alle angeschlossenen Statistiken mit Gemeindename und Gemeindeschlüssel.
SELECT g.nationalco AS ags, g.name AS gemeinde,
       d.indikator, d.wert, d.zeitbezug, d.source AS quelle
FROM public.dashboard_data_de d
JOIN public."Kommunale Gebiete Deutschland" g
  ON g.nationalco = d.kennziffer
ORDER BY g.name, d.source;

-- Auf fehlende Gemeindezuordnungen prüfen: Demo-Ergebnis 0.
SELECT count(*) AS ohne_geometrie
FROM public.dashboard_data_de d
LEFT JOIN public."Kommunale Gebiete Deutschland" g
  ON g.nationalco = d.kennziffer
WHERE g.id IS NULL;

-- Gültigkeit und Koordinatensystem: Demo-Ergebnis 413 gültig, EPSG:4326.
SELECT count(*) AS gemeinden,
       count(*) FILTER (WHERE ST_IsValid(geom)) AS gueltig,
       min(ST_SRID(geom)) AS min_srid, max(ST_SRID(geom)) AS max_srid
FROM public."Kommunale Gebiete Deutschland";
