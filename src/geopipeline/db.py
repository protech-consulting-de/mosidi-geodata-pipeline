"""Transactional MOSIDI adapter; target DDL is deliberately never issued here.

Production requires the final THB schema dump. This adapter accepts only the
documented demo profile and fails closed when the database differs materially.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .http import public_url

GEOMETRY_TABLE = 'public."Kommunale Gebiete Deutschland"'
ATTRIBUTE_COLUMNS = ('indikator', 'kennziffer', 'wert', 'zeitbezug', 'granularity', 'source', 'data_type')
META_COLUMNS = {
    'dct_title', 'dct_description', 'dct_catalog_publisher', 'dct_accessurl',
    'dct_license', 'geometry_type', 'dcatde_politicalgeocodingleveluri',
    'table_name', 'dct_modified', 'dct_issued', 'dct_temporal_startdata',
    'dct_temporal_enddate', 'imported', 'dct_type', 'dcat_ap_id', 'dcat_ap_title',
}


class SchemaError(RuntimeError):
    """The target is not the explicitly supported MOSIDI schema profile."""


class OwnershipError(RuntimeError):
    """A proposed write would take control of existing unowned attribute data."""


class ScopeChangeError(RuntimeError):
    """A changed source definition would silently alter replacement boundaries."""


def _scope_signature(config: dict) -> str:
    # Fail closed for newly introduced config options; only known operational and
    # display settings are excluded from source identity.
    excluded = {'id', 'metadata_title', 'description', 'license', 'attribution',
                'interval_minutes', 'interval_seconds', 'schedule', 'timeout', 'connect_timeout',
                'read_timeout', 'retries', 'retry_backoff', 'page_size', 'max_pages',
                'max_download_bytes', 'request_pause', 'enabled', 'adopt_existing', 'accept_scope_change',
                'report_dir'}
    scope = {key: value for key, value in config.items() if key not in excluded}
    encoded = json.dumps(scope, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _numeric(value: Any) -> str:
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f'Invalid numeric value: {value!r}') from exc
    if not number.is_finite():
        raise ValueError('Numeric values must be finite')
    return str(number.normalize())


def _attributes(value: dict) -> dict:
    missing = set(ATTRIBUTE_COLUMNS) - value.keys()
    if missing:
        raise ValueError(f'Missing attribute columns: {sorted(missing)}')
    result = {key: value[key] for key in ATTRIBUTE_COLUMNS}
    for key in ('indikator', 'kennziffer', 'granularity', 'source'):
        if not isinstance(result[key], str) or not result[key].strip():
            raise ValueError(f'{key} must be a nonempty string')
    result['wert'] = _numeric(result['wert'])
    year = result['zeitbezug']
    if isinstance(year, bool) or str(year) != str(int(year)):
        raise ValueError('zeitbezug must be a whole year')
    result['zeitbezug'] = int(year)
    if not 1 <= result['zeitbezug'] <= 9999:
        raise ValueError('zeitbezug outside supported year range')
    return result


class Db:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.conn = None

    def __enter__(self):
        self.conn = psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)
        return self

    def __exit__(self, *_):
        if self.conn:
            self.conn.close()

    def _execute(self, query, params=None):
        if self.conn is None:
            raise RuntimeError('Use Db as a context manager')
        return self.conn.execute(query, params)

    def _control_exists(self) -> bool:
        return self._execute("SELECT to_regclass('pipeline_meta.source_records') IS NOT NULL AND to_regclass('pipeline_meta.source_config') IS NOT NULL AND to_regclass('pipeline_meta.source_runs') IS NOT NULL AND to_regclass('pipeline_meta.source_catalog') IS NOT NULL AND to_regclass('pipeline_meta.catalog_state') IS NOT NULL AS present").fetchone()['present']

    def preflight(self) -> dict:
        """Read-only schema inspection. Missing catalogue view is a visible warning."""
        if not self._execute("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='postgis') AS ok").fetchone()['ok']:
            raise SchemaError('PostGIS extension is missing; production schema must be provisioned separately')
        expected = {
            'dashboard_data_de': {'id', *ATTRIBUTE_COLUMNS},
            'Kommunale Gebiete Deutschland': {'id', 'geom', 'nationalco', 'name'},
            'table_metadata': META_COLUMNS,
        }
        for table, required in expected.items():
            rows = self._execute("SELECT column_name,data_type,udt_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (table,)).fetchall()
            columns = {row['column_name']: row for row in rows}
            missing = required - columns.keys()
            if missing:
                raise SchemaError(f'{table}: missing columns {sorted(missing)}. Obtain the authoritative schema; imports never alter target tables.')
            pk = self._execute("""SELECT array_agg(a.attname ORDER BY k.ord) AS columns
                FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
                JOIN pg_namespace n ON n.oid=t.relnamespace
                CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY k(attnum,ord)
                JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum
                WHERE c.contype='p' AND n.nspname='public' AND t.relname=%s""", (table,)).fetchone()['columns']
            wanted = ['table_name', 'dcatde_politicalgeocodingleveluri'] if table == 'table_metadata' else ['id']
            if pk != wanted:
                raise SchemaError(f'{table}: primary key {pk!r}, expected {wanted!r}')
            if table == 'dashboard_data_de':
                if columns['wert']['data_type'] != 'numeric' or columns['kennziffer']['data_type'] not in ('text', 'character varying') or columns['zeitbezug']['data_type'] not in ('integer', 'smallint', 'bigint'):
                    raise SchemaError('dashboard_data_de: expected numeric wert, textual kennziffer and integer zeitbezug')
        geometry = self._execute("SELECT type,srid FROM geometry_columns WHERE f_table_schema='public' AND f_table_name='Kommunale Gebiete Deutschland' AND f_geometry_column='geom'").fetchone()
        if not geometry or geometry['srid'] != 4326 or geometry['type'].upper() != 'MULTIPOLYGON':
            raise SchemaError('Geometry target must be MULTIPOLYGON in EPSG:4326')
        view = self._execute("SELECT c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname='source_granularities'").fetchone()
        if view and view['relkind'] != 'm':
            raise SchemaError('source_granularities exists but is not a materialized view')
        warnings = [] if view else ['source_granularities is absent; no catalogue-view refresh will be performed. Production integration is incomplete.']
        return {'profile': 'mosidi-public-demo-v1', 'warnings': warnings, 'control_schema_present': self._control_exists()}

    def ensure_control_schema(self) -> None:
        """Explicit initialization of permitted sidecar state, never target tables."""
        with self.conn.transaction():
            self._execute('CREATE SCHEMA IF NOT EXISTS pipeline_meta')
            self._execute("""CREATE TABLE IF NOT EXISTS pipeline_meta.source_records (
                source_id TEXT NOT NULL, record_key TEXT NOT NULL,
                target_kind TEXT NOT NULL CHECK(target_kind IN ('geometry','attributes')),
                target_id BIGINT NOT NULL,
                PRIMARY KEY(source_id,record_key))""")
            self._execute("CREATE UNIQUE INDEX IF NOT EXISTS attribute_owner_unique ON pipeline_meta.source_records(target_id) WHERE target_kind='attributes'")
            self._execute("""CREATE TABLE IF NOT EXISTS pipeline_meta.source_config (
                source_id TEXT PRIMARY KEY, scope_signature TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            self._execute("""CREATE TABLE IF NOT EXISTS pipeline_meta.source_runs (
                run_id TEXT NOT NULL, source_id TEXT NOT NULL,
                started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL, report JSONB NOT NULL,
                PRIMARY KEY(run_id,source_id))""")
            self._execute("""CREATE TABLE IF NOT EXISTS pipeline_meta.source_catalog (
                source_id TEXT NOT NULL, table_name TEXT NOT NULL, granularity TEXT NOT NULL,
                publisher TEXT NOT NULL, kind TEXT NOT NULL, access_url TEXT,
                license TEXT, description TEXT,
                PRIMARY KEY(source_id,table_name,granularity))""")
            self._execute("""CREATE TABLE IF NOT EXISTS pipeline_meta.catalog_state (
                table_name TEXT NOT NULL, granularity TEXT NOT NULL,
                publisher TEXT NOT NULL, kind TEXT NOT NULL, created_by_pipeline BOOLEAN NOT NULL,
                PRIMARY KEY(table_name,granularity))""")

    def _store_source_run(self, run_id: str, source_id: str, report: dict) -> None:
        if not isinstance(run_id, str) or not run_id or not isinstance(source_id, str) or not source_id:
            raise ValueError('run_id and source_id must be nonempty strings')
        if report.get('mode') == 'dry-run':
            raise ValueError('Dry-run reports must not be persisted to the database')
        payload = dict(report, run_id=run_id, source_id=source_id)
        status = payload.get('status', 'running')
        if not isinstance(status, str) or not status:
            raise ValueError('Source run status must be nonempty text')
        payload['status'] = status
        self._execute("""INSERT INTO pipeline_meta.source_runs(run_id,source_id,started_at,status,report)
            VALUES(%s,%s,COALESCE(%s::timestamptz,CURRENT_TIMESTAMP),%s,%s)
            ON CONFLICT(run_id,source_id) DO UPDATE SET
                status=EXCLUDED.status,
                report=pipeline_meta.source_runs.report || EXCLUDED.report""",
            (run_id, source_id, payload.get('started_at'), status,
             Jsonb(payload, dumps=lambda obj: json.dumps(obj, default=str, allow_nan=False))))

    def record_source_run(self, run_id: str, source_id: str, report: dict) -> None:
        """Persist fetch/progress/error evidence without touching any target rows.

        Caller must explicitly initialize control schema and must never call this
        for a dry run. Updates preserve the original started_at and merge detail.
        """
        if not self._control_exists():
            raise SchemaError('Initialize the control schema before recording source runs')
        with self.conn.transaction():
            self._store_source_run(run_id, source_id, report)

    def read_run(self, run_id: str) -> list[dict]:
        """Read durable source evidence, including runs interrupted before reporting."""
        if not self._execute("SELECT to_regclass('pipeline_meta.source_runs') IS NOT NULL AS present").fetchone()['present']:
            return []
        return [row['report'] for row in self._execute(
            'SELECT report FROM pipeline_meta.source_runs WHERE run_id=%s ORDER BY started_at,source_id',
            (run_id,)).fetchall()]

    def apply_source(self, config: dict, records: Iterable[dict], *, dry_run: bool = False,
                     deletion_enabled: bool = True, protected_keys=(), run_id: str | None = None) -> dict:
        source_id, kind = config['id'], config['kind']
        if not isinstance(source_id, str) or not source_id or kind not in ('geometry', 'attributes'):
            raise ValueError('Config requires a nonempty id and kind geometry or attributes')
        prepared, natural_keys = {}, set()
        for record in records:
            key = record['key']
            if not isinstance(key, str) or not key or key in prepared:
                raise ValueError(f'Empty or duplicate record key: {key!r}')
            if kind == 'attributes':
                value = _attributes(record['attributes'])
                natural = tuple(value[c] for c in ATTRIBUTE_COLUMNS if c not in ('wert', 'data_type'))
            else:
                value = dict(record['geometry'])
                if not all(isinstance(value.get(k), str) and value[k] for k in ('geo_id', 'name', 'wkt')):
                    raise ValueError('Geometry requires textual geo_id, name and wkt')
                natural = value['geo_id']
            if natural in natural_keys:
                raise ValueError(f'Duplicate target identity in input: {natural!r}')
            natural_keys.add(natural)
            prepared[key] = value
        result = {name: 0 for name in ('added_count', 'changed_count', 'removed_count', 'unchanged_count', 'retained_count')}
        result.update(committed=False, changes=[], deletion_enabled=deletion_enabled, warnings=[])
        with self.conn.transaction():
            # Serializing writers also protects shared geometry and the shared catalogue.
            self._execute("SELECT pg_advisory_xact_lock(hashtextextended('mosidi-geopipeline-target',0))")
            result['warnings'].extend(self.preflight()['warnings'])
            control = self._control_exists()
            if not dry_run and not control:
                raise SchemaError('pipeline_meta is absent; initialize control schema explicitly before writing')
            signature = _scope_signature(config)
            old_scope = self._execute('SELECT scope_signature FROM pipeline_meta.source_config WHERE source_id=%s', (source_id,)).fetchone() if control else None
            if old_scope and old_scope['scope_signature'] != signature and not config.get('accept_scope_change', False):
                raise ScopeChangeError('Source scope changed for the same id. Use a new id, or explicitly set accept_scope_change after reviewing the replacement boundaries.')
            self._validate_catalogue(config, prepared.values())
            owners = self._execute('SELECT record_key,target_kind,target_id FROM pipeline_meta.source_records WHERE source_id=%s', (source_id,)).fetchall() if control else []
            ownership = {row['record_key']: row for row in owners}
            if any(row['target_kind'] != kind for row in owners):
                raise OwnershipError('Source ID was previously used for another target kind')
            previous_groups = self._owned_attribute_groups(source_id) if control and kind == 'attributes' else []
            for key, value in prepared.items():
                if kind == 'geometry':
                    action, target_id = self._geometry(value, dry_run)
                else:
                    action, target_id = self._attribute(value, ownership.get(key), config, dry_run, control)
                result[f'{action}_count'] += 1
                if action != 'unchanged':
                    result['changes'].append({'key': key, 'action': action})
                if not dry_run:
                    self._execute("""INSERT INTO pipeline_meta.source_records(source_id,record_key,target_kind,target_id)
                        VALUES(%s,%s,%s,%s) ON CONFLICT(source_id,record_key)
                        DO UPDATE SET target_id=EXCLUDED.target_id""", (source_id, key, kind, target_id))
            absent = set(ownership) - prepared.keys()
            protected = set(protected_keys)
            for key in sorted(absent):
                if kind == 'geometry' or not deletion_enabled or key in protected:
                    result['retained_count'] += 1
                    continue
                row = self._execute('SELECT id FROM public.dashboard_data_de WHERE id=%s', (ownership[key]['target_id'],)).fetchone()
                if row:
                    result['removed_count'] += 1
                    result['changes'].append({'key': key, 'action': 'removed'})
                if not dry_run:
                    self._execute('DELETE FROM public.dashboard_data_de WHERE id=%s', (ownership[key]['target_id'],))
                    self._execute('DELETE FROM pipeline_meta.source_records WHERE source_id=%s AND record_key=%s', (source_id, key))
            if not dry_run:
                self._metadata(config, previous_groups)
                if self._execute("SELECT to_regclass('public.source_granularities') IS NOT NULL AS present").fetchone()['present']:
                    self._execute('REFRESH MATERIALIZED VIEW public.source_granularities')
                self._execute("""INSERT INTO pipeline_meta.source_config(source_id,scope_signature)
                    VALUES(%s,%s) ON CONFLICT(source_id) DO UPDATE SET
                    scope_signature=EXCLUDED.scope_signature,updated_at=CURRENT_TIMESTAMP""", (source_id, signature))
                result['committed'] = True
                if run_id is not None:
                    self._store_source_run(run_id, source_id,
                        dict(result, status='committed', mode='import'))
        return result

    def _geometry(self, value, dry_run):
        candidate = self._execute("""SELECT ST_IsValid(g) AS valid, GeometryType(g) AS kind,
                encode(ST_AsEWKB(g),'hex') AS canonical FROM
                (SELECT ST_Multi(ST_GeomFromText(%s,4326)) AS g) s""", (value['wkt'],)).fetchone()
        if not candidate['valid'] or candidate['kind'] != 'MULTIPOLYGON':
            raise ValueError('Geometry must be a valid polygon/multipolygon after adapter validation')
        rows = self._execute(f"SELECT id,name,encode(ST_AsEWKB(geom),'hex') AS canonical FROM {GEOMETRY_TABLE} WHERE nationalco=%s", (value['geo_id'],)).fetchall()
        if len(rows) > 1:
            raise OwnershipError(f'Ambiguous geometry key {value["geo_id"]}: multiple existing rows')
        old = rows[0] if rows else None
        action = 'added' if old is None else ('unchanged' if old['name'] == value['name'] and old['canonical'] == candidate['canonical'] else 'changed')
        target_id = old['id'] if old else None
        if not dry_run and action == 'added':
            target_id = self._execute(f'INSERT INTO {GEOMETRY_TABLE}(nationalco,name,geom) VALUES(%s,%s,ST_Multi(ST_GeomFromText(%s,4326))) RETURNING id', (value['geo_id'], value['name'], value['wkt'])).fetchone()['id']
        elif not dry_run and action == 'changed':
            self._execute(f'UPDATE {GEOMETRY_TABLE} SET name=%s,geom=ST_Multi(ST_GeomFromText(%s,4326)) WHERE id=%s', (value['name'], value['wkt'], target_id))
        return action, target_id

    def _attribute(self, value, ownership, config, dry_run, control):
        old = None
        if ownership:
            old = self._execute('SELECT * FROM public.dashboard_data_de WHERE id=%s', (ownership['target_id'],)).fetchone()
        if old is None:
            rows = self._execute("""SELECT * FROM public.dashboard_data_de WHERE
                indikator=%s AND kennziffer=%s AND zeitbezug=%s AND granularity=%s AND source=%s""",
                tuple(value[k] for k in ('indikator', 'kennziffer', 'zeitbezug', 'granularity', 'source'))).fetchall()
            if len(rows) > 1:
                raise OwnershipError('Multiple existing attribute rows have the same identity')
            if rows:
                if not config.get('adopt_existing', False):
                    raise OwnershipError('Existing attribute data is unowned. Explicit adopt_existing is required.')
                old = rows[0]
                other = self._execute("SELECT source_id FROM pipeline_meta.source_records WHERE target_kind='attributes' AND target_id=%s", (old['id'],)).fetchone() if control else None
                if other and other['source_id'] != config['id']:
                    raise OwnershipError(f'Attribute row already belongs to source {other["source_id"]}')
        action = 'added' if old is None else ('unchanged' if _attributes(old) == value else 'changed')
        target_id = old['id'] if old else None
        values = tuple(value[k] for k in ATTRIBUTE_COLUMNS)
        if not dry_run and action == 'added':
            target_id = self._execute('INSERT INTO public.dashboard_data_de(indikator,kennziffer,wert,zeitbezug,granularity,source,data_type) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id', values).fetchone()['id']
        elif not dry_run and action == 'changed':
            self._execute('UPDATE public.dashboard_data_de SET indikator=%s,kennziffer=%s,wert=%s,zeitbezug=%s,granularity=%s,source=%s,data_type=%s WHERE id=%s', values + (target_id,))
        return action, target_id

    def _validate_catalogue(self, config, values):
        """Run the same ownership checks during preview, before any mutation."""
        if config['kind'] == 'attributes':
            candidates = {(v['indikator'], v['granularity'], v['source']) for v in values}
        else:
            candidates = {('Kommunale Gebiete Deutschland', config.get('granularity', 'Gemeinden'), config.get('publisher', config['id']))}
        grouped = {}
        for title, granularity, publisher in candidates:
            identity = (title, granularity)
            if identity in grouped and grouped[identity] != publisher:
                raise OwnershipError('Multiple publishers share one catalogue key in the incoming data')
            grouped[identity] = publisher
            old = self._execute('SELECT dct_catalog_publisher FROM public.table_metadata WHERE table_name=%s AND dcatde_politicalgeocodingleveluri=%s', identity).fetchone()
            if old and old['dct_catalog_publisher'] != publisher:
                raise OwnershipError('Catalogue key belongs to another publisher; refusing to overwrite metadata')

    def _owned_attribute_groups(self, source_id):
        return self._execute("""SELECT DISTINCT d.indikator AS title,d.granularity,d.source AS publisher
            FROM public.dashboard_data_de d JOIN pipeline_meta.source_records o
            ON o.target_kind='attributes' AND o.target_id=d.id
            WHERE o.source_id=%s""", (source_id,)).fetchall()

    def _metadata(self, config, previous_groups):
        """Rebuild affected catalogue groups from *all* committed data and provenance.

        Multiple configurations can own disjoint years of the same indicator. A
        source update must never shrink metadata to only that source's years, or
        attach its download URL to other configurations' data.
        """
        source_id, kind = config['id'], config['kind']
        old_provenance = self._execute(
            'SELECT table_name AS title,granularity,publisher FROM pipeline_meta.source_catalog WHERE source_id=%s',
            (source_id,)).fetchall()
        if kind == 'attributes':
            active = self._owned_attribute_groups(source_id)
        else:
            any_owned = self._execute("SELECT EXISTS(SELECT 1 FROM pipeline_meta.source_records WHERE source_id=%s AND target_kind='geometry') AS present", (source_id,)).fetchone()['present']
            active = [{'title': 'Kommunale Gebiete Deutschland',
                       'granularity': config.get('granularity', 'Gemeinden'),
                       'publisher': config.get('publisher', source_id)}] if any_owned else []
        affected = {(g['title'], g['granularity']): g['publisher']
                    for g in [*previous_groups, *old_provenance, *active]}
        self._execute('DELETE FROM pipeline_meta.source_catalog WHERE source_id=%s', (source_id,))
        raw_url = config.get('source_url', config.get('url'))
        safe_url = public_url(raw_url) if raw_url else None
        for group in active:
            self._execute("""INSERT INTO pipeline_meta.source_catalog
                (source_id,table_name,granularity,publisher,kind,access_url,license,description)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                (source_id, group['title'], group['granularity'], group['publisher'], kind,
                 safe_url, config.get('license'), config.get('metadata_title', group['title'])))
        for (title, granularity), previous_publisher in sorted(affected.items()):
            identity = (title, granularity)
            state = self._execute('SELECT * FROM pipeline_meta.catalog_state WHERE table_name=%s AND granularity=%s', identity).fetchone()
            old = self._execute('SELECT dct_catalog_publisher FROM public.table_metadata WHERE table_name=%s AND dcatde_politicalgeocodingleveluri=%s', identity).fetchone()
            if old and old['dct_catalog_publisher'] != previous_publisher:
                raise OwnershipError('Catalogue key belongs to another publisher; refusing to overwrite metadata')
            if state and (state['publisher'] != previous_publisher or state['kind'] != kind):
                raise OwnershipError('Catalogue ownership differs from the current source')
            if kind == 'attributes':
                aggregate = self._execute("""SELECT count(*) AS row_count,min(d.zeitbezug) AS first_year,
                    max(d.zeitbezug) AS last_year,array_agg(DISTINCT d.source) AS publishers,
                    count(o.target_id) AS known_rows
                    FROM public.dashboard_data_de d LEFT JOIN pipeline_meta.source_records o
                    ON o.target_kind='attributes' AND o.target_id=d.id
                    WHERE d.indikator=%s AND d.granularity=%s""", identity).fetchone()
                if aggregate['row_count'] == 0:
                    # Remove only metadata created by this pipeline. A pre-existing
                    # record is retained but disabled so the MOSIDI API cannot show
                    # a nonexistent indicator with an obsolete time range.
                    if old:
                        if state and state['created_by_pipeline']:
                            self._execute('DELETE FROM public.table_metadata WHERE table_name=%s AND dcatde_politicalgeocodingleveluri=%s', identity)
                            self._execute('DELETE FROM pipeline_meta.catalog_state WHERE table_name=%s AND granularity=%s', identity)
                        else:
                            self._execute("""UPDATE public.table_metadata SET geometry_type=NULL,
                                dct_temporal_startdata=NULL,dct_temporal_enddate=NULL,dct_accessurl=NULL,
                                dct_license=NULL,dct_modified=CURRENT_TIMESTAMP
                                WHERE table_name=%s AND dcatde_politicalgeocodingleveluri=%s""", identity)
                    continue
                publishers = aggregate['publishers']
                if publishers != [previous_publisher]:
                    raise OwnershipError('Multiple publishers share a MOSIDI catalogue key; this schema cannot represent that safely')
                first_year, last_year = aggregate['first_year'], aggregate['last_year']
                unowned_data = aggregate['known_rows'] != aggregate['row_count']
                provenance = self._execute("""SELECT c.* FROM pipeline_meta.source_catalog c
                    WHERE c.table_name=%s AND c.granularity=%s AND c.kind='attributes'
                    AND EXISTS(SELECT 1 FROM pipeline_meta.source_records o
                        JOIN public.dashboard_data_de d ON d.id=o.target_id
                        WHERE o.source_id=c.source_id AND o.target_kind='attributes'
                        AND d.indikator=c.table_name AND d.granularity=c.granularity
                        AND d.source=c.publisher) ORDER BY c.source_id""", identity).fetchall()
                # Existing ownership without a provenance entry is also unknown.
                distinct_owners = self._execute("""SELECT count(DISTINCT o.source_id) AS n
                    FROM pipeline_meta.source_records o JOIN public.dashboard_data_de d ON d.id=o.target_id
                    WHERE o.target_kind='attributes' AND d.indikator=%s AND d.granularity=%s""", identity).fetchone()['n']
                unknown_provenance = unowned_data or distinct_owners != len(provenance)
            else:
                first_year = last_year = None
                provenance = self._execute("""SELECT * FROM pipeline_meta.source_catalog
                    WHERE table_name=%s AND granularity=%s AND kind='geometry'
                    ORDER BY source_id""", identity).fetchall()
                unknown_provenance = False
            urls = {row['access_url'] for row in provenance}
            licenses = {row['license'] for row in provenance}
            access_url = next(iter(urls)) if len(urls) == 1 and None not in urls and not unknown_provenance else None
            license_text = '; '.join(sorted(licenses)) if licenses and None not in licenses and not unknown_provenance else None
            descriptions = list(dict.fromkeys(row['description'] for row in provenance if row['description']))
            description = '; '.join(descriptions) or title
            if len(urls) > 1 or unknown_provenance:
                description += ' (mehrere Datenbestände; Herkunft je Importkonfiguration im Importprotokoll)'
            if not state:
                self._execute("""INSERT INTO pipeline_meta.catalog_state
                    (table_name,granularity,publisher,kind,created_by_pipeline) VALUES(%s,%s,%s,%s,%s)""",
                    (title, granularity, previous_publisher, kind, old is None))
            self._execute("""INSERT INTO public.table_metadata(
                dct_title,dct_description,dct_catalog_publisher,dct_accessurl,dct_license,
                geometry_type,dcatde_politicalgeocodingleveluri,table_name,dct_modified,
                dct_issued,dct_temporal_startdata,dct_temporal_enddate,imported,dct_type,dcat_ap_id)
                VALUES(%s,%s,%s,%s,%s,'Polygon',%s,%s,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,
                    CASE WHEN %s::int IS NULL THEN NULL ELSE make_date(%s,1,1) END,
                    CASE WHEN %s::int IS NULL THEN NULL ELSE make_date(%s,12,31) END,
                    CURRENT_TIMESTAMP,%s,%s)
                ON CONFLICT(table_name,dcatde_politicalgeocodingleveluri) DO UPDATE SET
                    dct_description=EXCLUDED.dct_description,dct_accessurl=EXCLUDED.dct_accessurl,
                    dct_license=EXCLUDED.dct_license,dct_modified=CURRENT_TIMESTAMP,
                    geometry_type=EXCLUDED.geometry_type,dct_type=EXCLUDED.dct_type,
                    dct_temporal_startdata=EXCLUDED.dct_temporal_startdata,
                    dct_temporal_enddate=EXCLUDED.dct_temporal_enddate,imported=CURRENT_TIMESTAMP,
                    dcat_ap_id=EXCLUDED.dcat_ap_id""",
                (title, description, previous_publisher, access_url, license_text, granularity, title,
                 first_year, first_year, last_year, last_year,
                 'indikator' if kind == 'attributes' else 'vector',
                 f'{title}_{granularity}_{previous_publisher}'))
