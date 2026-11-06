"""Meaningful integration tests against disposable PostGIS databases.

TEST_DATABASE_URL must identify a PostgreSQL role allowed to CREATE DATABASE and
CREATE EXTENSION postgis. The referenced database is never truncated or modified.
Each test provisions and removes its own uniquely named database.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest

from geopipeline.db import Db, OwnershipError, SchemaError, ScopeChangeError, _numeric


@pytest.fixture
def database():
    admin_dsn = os.environ.get('TEST_DATABASE_URL')
    if not admin_dsn:
        pytest.skip('TEST_DATABASE_URL not set; PostGIS integration test requires a database server')
    name = 'geopipeline_test_' + uuid.uuid4().hex[:16]
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    params = conninfo_to_dict(admin_dsn)
    params['dbname'] = name
    dsn = make_conninfo(**params)
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute((Path(__file__).parents[1] / 'sql/demo_schema.sql').read_text())
        yield dsn
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


def cfg(name='population', kind='attributes', **extra):
    return {'id': name, 'kind': kind, 'publisher': 'Statistics', 'granularity': 'Gemeinden', **extra}


def attr(key='one', value='123.5', geo='12054000', indicator='Population'):
    return {'key': key, 'attributes': {'indikator': indicator, 'kennziffer': geo,
            'wert': value, 'zeitbezug': 2024, 'granularity': 'Gemeinden',
            'source': 'Statistics', 'data_type': 'numeric'}}


def geom(key='berlin', name='Berlin'):
    return {'key': key, 'geometry': {'geo_id': '11000000', 'name': name,
            'wkt': 'MULTIPOLYGON(((13 52,14 52,14 53,13 53,13 52)))'}}


def test_numeric_comparison_is_decimal_and_rejects_nonfinite():
    assert _numeric('0.100') == _numeric(Decimal('0.1'))
    for bad in ('NaN', 'Infinity', '-Infinity', 'banana'):
        with pytest.raises(ValueError):
            _numeric(bad)


def test_first_import_repeat_change_and_catalogue(database):
    with Db(database) as db:
        db.preflight()
        db.ensure_control_schema()
        first = db.apply_source(cfg(), [attr()])
        assert first['added_count'] == 1 and first['committed']
        first_id = db.conn.execute('SELECT id FROM dashboard_data_de').fetchone()['id']
        repeat = db.apply_source(cfg(), [attr(value='123.500')])
        assert repeat['unchanged_count'] == 1 and not repeat['changes']
        changed = db.apply_source(cfg(), [attr(value='124')])
        assert changed['changed_count'] == 1
        assert db.conn.execute('SELECT id FROM dashboard_data_de').fetchone()['id'] == first_id
        assert db.conn.execute('SELECT count(*) AS n FROM source_granularities').fetchone()['n'] == 1
        meta = db.conn.execute('SELECT dct_catalog_publisher,dct_temporal_startdata FROM table_metadata').fetchone()
        assert meta['dct_catalog_publisher'] == 'Statistics'
        assert meta['dct_temporal_startdata'].year == 2024


def test_dry_run_before_control_schema_has_no_writes(database):
    with Db(database) as db:
        assert not db.preflight()['control_schema_present']
        result = db.apply_source(cfg(), [attr()], dry_run=True)
        assert result['added_count'] == 1 and not result['committed']
        assert not db.preflight()['control_schema_present']
        assert db.conn.execute('SELECT count(*) AS n FROM dashboard_data_de').fetchone()['n'] == 0
        assert db.conn.execute('SELECT count(*) AS n FROM table_metadata').fetchone()['n'] == 0


def test_scoped_removal_never_deletes_foreign_rows(database):
    with Db(database) as db:
        db.ensure_control_schema()
        db.apply_source(cfg('one'), [attr()])
        db.apply_source(cfg('two'), [attr(key='other', indicator='Employment')])
        result = db.apply_source(cfg('one'), [])
        assert result['removed_count'] == 1
        remaining = db.conn.execute('SELECT indikator FROM dashboard_data_de').fetchall()
        assert remaining == [{'indikator': 'Employment'}]
        assert db.conn.execute('SELECT indikator FROM source_granularities').fetchall() == remaining


def test_missing_and_protected_records_are_retained(database):
    with Db(database) as db:
        db.ensure_control_schema()
        db.apply_source(cfg(), [attr()])
        for options in ({'deletion_enabled': False}, {'protected_keys': {'one'}}):
            result = db.apply_source(cfg(), [], **options)
            assert result['retained_count'] == 1 and result['removed_count'] == 0
            assert db.conn.execute('SELECT count(*) AS n FROM dashboard_data_de').fetchone()['n'] == 1


def test_geometry_identity_is_preserved_and_missing_geometry_never_deleted(database):
    with Db(database) as db:
        db.ensure_control_schema()
        assert db.apply_source(cfg('borders', 'geometry'), [geom()])['added_count'] == 1
        old = db.conn.execute('SELECT id FROM "Kommunale Gebiete Deutschland"').fetchone()['id']
        changed = db.apply_source(cfg('borders', 'geometry'), [geom(name='Berlin City')])
        assert changed['changed_count'] == 1
        assert db.conn.execute('SELECT id FROM "Kommunale Gebiete Deutschland"').fetchone()['id'] == old
        result = db.apply_source(cfg('borders', 'geometry'), [])
        assert result['retained_count'] == 1 and result['removed_count'] == 0
        assert db.conn.execute('SELECT count(*) AS n FROM "Kommunale Gebiete Deutschland"').fetchone()['n'] == 1


def test_existing_attribute_requires_explicit_adoption_and_cannot_steal(database):
    with Db(database) as db:
        db.ensure_control_schema()
        db.conn.execute("INSERT INTO dashboard_data_de(indikator,kennziffer,wert,zeitbezug,granularity,source,data_type) VALUES('Population','12054000',123.5,2024,'Gemeinden','Statistics','numeric')")
        with pytest.raises(OwnershipError):
            db.apply_source(cfg(), [attr()])
        assert db.apply_source(cfg(adopt_existing=True), [attr()])['unchanged_count'] == 1
        with pytest.raises(OwnershipError):
            db.apply_source(cfg('stealing', adopt_existing=True), [attr()])


def test_metadata_failure_rolls_back_attributes_and_ownership(database):
    with Db(database) as db:
        db.ensure_control_schema()
        db.conn.execute("INSERT INTO table_metadata(table_name,dcatde_politicalgeocodingleveluri,dct_catalog_publisher) VALUES('Population','Gemeinden','Foreign publisher')")
        with pytest.raises(OwnershipError):
            db.apply_source(cfg(), [attr()])
        assert db.conn.execute('SELECT count(*) AS n FROM dashboard_data_de').fetchone()['n'] == 0
        assert db.conn.execute('SELECT count(*) AS n FROM pipeline_meta.source_records').fetchone()['n'] == 0


def test_duplicate_identity_rejected_without_partial_write(database):
    with Db(database) as db:
        db.ensure_control_schema()
        with pytest.raises(ValueError, match='Duplicate target identity'):
            db.apply_source(cfg(), [attr(), attr(key='second')])
        assert db.conn.execute('SELECT count(*) AS n FROM dashboard_data_de').fetchone()['n'] == 0


def test_late_database_error_rolls_back_earlier_rows(database):
    with Db(database) as db:
        db.ensure_control_schema()
        second = attr(key='second', geo='12060000', indicator='Employment')
        second['attributes']['source'] = 'x' * 201  # target VARCHAR(200)
        with pytest.raises(psycopg.errors.StringDataRightTruncation):
            db.apply_source(cfg(), [attr(), second])
        assert db.conn.execute('SELECT count(*) AS n FROM dashboard_data_de').fetchone()['n'] == 0
        assert db.conn.execute('SELECT count(*) AS n FROM pipeline_meta.source_records').fetchone()['n'] == 0


def test_concurrent_same_source_is_serialized_and_idempotent(database):
    with Db(database) as db:
        db.ensure_control_schema()
    def run(_):
        with Db(database) as db:
            return db.apply_source(cfg(), [attr()])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert sum(r['added_count'] for r in results) == 1
    assert sum(r['unchanged_count'] for r in results) == 1


def test_schema_changes_are_refused_not_repaired(database):
    with Db(database) as db:
        db.conn.execute('ALTER TABLE table_metadata DROP COLUMN dcat_ap_title')
        with pytest.raises(SchemaError, match='dcat_ap_title'):
            db.preflight()
        missing = db.conn.execute("SELECT count(*) AS n FROM information_schema.columns WHERE table_name='table_metadata' AND column_name='dcat_ap_title'").fetchone()['n']
        assert missing == 0


def test_missing_materialized_view_is_visible_warning(database):
    with Db(database) as db:
        db.conn.execute('DROP MATERIALIZED VIEW source_granularities')
        assert any('absent' in warning for warning in db.preflight()['warnings'])


def test_accidental_source_scope_change_is_rejected_before_deletion(database):
    with Db(database) as db:
        db.ensure_control_schema()
        db.apply_source(cfg(url='https://example.org/full.csv'), [attr()])
        for dry_run in (True, False):
            with pytest.raises(ScopeChangeError):
                db.apply_source(cfg(url='https://example.org/subset.csv'), [], dry_run=dry_run)
        assert db.conn.execute('SELECT count(*) AS n FROM dashboard_data_de').fetchone()['n'] == 1
        result = db.apply_source(cfg(url='https://example.org/subset.csv', accept_scope_change=True), [])
        assert result['removed_count'] == 1


def test_display_metadata_and_retry_changes_do_not_change_source_scope(database):
    with Db(database) as db:
        db.ensure_control_schema()
        db.apply_source(cfg(), [attr()])
        result = db.apply_source(cfg(metadata_title='Updated description', retries=4), [attr()])
        assert result['unchanged_count'] == 1


def test_committed_source_evidence_is_durable_and_can_be_enriched(database):
    with Db(database) as db:
        db.ensure_control_schema()
        db.record_source_run('run-1', 'population', {'status': 'running', 'committed': False,
            'started_at': '2026-11-05T10:00:00Z', 'provenance': {'source': 'official'}})
        result = db.apply_source(cfg(), [attr()], run_id='run-1')
        stored = db.read_run('run-1')[0]
        assert stored['committed'] and stored['status'] == 'committed'
        assert stored['added_count'] == 1 and stored['changes'] == result['changes']
        assert stored['provenance']['source'] == 'official'
        db.record_source_run('run-1', 'population', {'status': 'success', 'duration_seconds': Decimal('1.25')})
        assert db.read_run('run-1')[0]['duration_seconds'] == '1.25'
        assert db.read_run('run-1')[0]['added_count'] == 1
        start = db.conn.execute('SELECT started_at FROM pipeline_meta.source_runs').fetchone()['started_at']
        assert start.isoformat() == '2026-11-05T10:00:00+00:00'
    with Db(database) as reopened:
        assert reopened.read_run('run-1')[0]['status'] == 'success'


def test_transaction_failure_keeps_started_run_but_no_committed_report(database):
    with Db(database) as db:
        db.ensure_control_schema()
        db.record_source_run('failed-run', 'population', {'status': 'running', 'committed': False})
        second = attr(key='second', geo='12060000', indicator='Employment')
        second['attributes']['source'] = 'x' * 201
        with pytest.raises(psycopg.errors.StringDataRightTruncation):
            db.apply_source(cfg(), [attr(), second], run_id='failed-run')
        assert db.read_run('failed-run') == [{'run_id': 'failed-run', 'source_id': 'population',
                                             'status': 'running', 'committed': False}]
        assert db.conn.execute('SELECT count(*) AS n FROM dashboard_data_de').fetchone()['n'] == 0
        db.record_source_run('failed-run', 'population', {'status': 'failed', 'error': 'database rejected row'})
        assert db.read_run('failed-run')[0]['status'] == 'failed'


def test_report_failure_rolls_back_successfully_prepared_target_changes(database):
    with Db(database) as db:
        db.ensure_control_schema()
        db.conn.execute("ALTER TABLE pipeline_meta.source_runs ADD CONSTRAINT force_report_failure CHECK (status <> 'committed')")
        with pytest.raises(psycopg.errors.CheckViolation):
            db.apply_source(cfg(), [attr()], run_id='atomic-report')
        assert db.read_run('atomic-report') == []
        assert db.conn.execute('SELECT count(*) AS n FROM dashboard_data_de').fetchone()['n'] == 0
        assert db.conn.execute('SELECT count(*) AS n FROM pipeline_meta.source_records').fetchone()['n'] == 0


def test_dry_run_with_run_id_never_persists_source_evidence(database):
    with Db(database) as db:
        assert db.read_run('preview') == []
        db.apply_source(cfg(), [attr()], dry_run=True, run_id='preview')
        assert not db.preflight()['control_schema_present']
        db.ensure_control_schema()
        result = db.apply_source(cfg(), [attr()], dry_run=True, run_id='preview')
        assert not result['committed'] and db.read_run('preview') == []
        with pytest.raises(ValueError, match='Dry-run'):
            db.record_source_run('preview', 'population', {'mode': 'dry-run', 'status': 'success'})
        assert db.read_run('preview') == []


def test_repeated_geometry_import_is_unchanged(database):
    with Db(database) as db:
        db.ensure_control_schema()
        db.apply_source(cfg('borders', 'geometry'), [geom()])
        repeat = db.apply_source(cfg('borders', 'geometry'), [geom()])
        assert repeat['unchanged_count'] == 1 and repeat['changes'] == []


def test_catalogue_urls_strip_credentials_queries_and_fragments(database):
    with Db(database) as db:
        db.ensure_control_schema()
        db.apply_source(cfg(url='https://username:password@example.org/data.csv?token=secret#private'), [attr()])
        stored = db.conn.execute('SELECT dct_accessurl FROM table_metadata').fetchone()['dct_accessurl']
        assert stored == 'https://example.org/data.csv'
        provenance = db.conn.execute('SELECT access_url FROM pipeline_meta.source_catalog').fetchone()['access_url']
        assert provenance == stored


def test_shared_catalogue_aggregates_all_years_and_retains_correct_provenance(database):
    with Db(database) as db:
        db.ensure_control_schema()
        first = attr(key='2022')
        first['attributes']['zeitbezug'] = 2022
        second = attr(key='2023')
        second['attributes']['zeitbezug'] = 2023
        config_first = cfg('first', url='https://example.org/2022.csv', license='License 2022')
        config_second = cfg('second', url='https://example.org/2023.csv', license='License 2023')
        db.apply_source(config_first, [first])
        db.apply_source(config_second, [second])
        meta = db.conn.execute('SELECT * FROM table_metadata').fetchone()
        assert meta['dct_temporal_startdata'].year == 2022
        assert meta['dct_temporal_enddate'].year == 2023
        assert meta['dct_catalog_publisher'] == 'Statistics'
        assert meta['dct_accessurl'] is None
        assert meta['dct_license'] == 'License 2022; License 2023'
        assert db.conn.execute('SELECT count(*) AS n FROM pipeline_meta.source_catalog').fetchone()['n'] == 2
        # Repeating either source must not shrink the common range.
        db.apply_source(config_first, [first])
        assert db.conn.execute('SELECT dct_temporal_enddate FROM table_metadata').fetchone()['dct_temporal_enddate'].year == 2023
        # Deleting the second source leaves first-source dates and provenance.
        db.apply_source(config_second, [])
        meta = db.conn.execute('SELECT * FROM table_metadata').fetchone()
        assert meta['dct_temporal_startdata'].year == meta['dct_temporal_enddate'].year == 2022
        assert meta['dct_accessurl'] == 'https://example.org/2022.csv'
        assert meta['dct_license'] == 'License 2022'
        # Last-data deletion also removes an entry originally created by us.
        db.apply_source(config_first, [])
        assert db.conn.execute('SELECT count(*) AS n FROM table_metadata').fetchone()['n'] == 0
        assert db.conn.execute('SELECT count(*) AS n FROM pipeline_meta.source_catalog').fetchone()['n'] == 0


def test_existing_unowned_years_are_included_without_claiming_their_provenance(database):
    with Db(database) as db:
        db.ensure_control_schema()
        db.conn.execute("INSERT INTO dashboard_data_de(indikator,kennziffer,wert,zeitbezug,granularity,source,data_type) VALUES('Population','12054000',100,2021,'Gemeinden','Statistics','numeric')")
        db.apply_source(cfg(url='https://example.org/2024.csv', license='License'), [attr()])
        meta = db.conn.execute('SELECT * FROM table_metadata').fetchone()
        assert meta['dct_temporal_startdata'].year == 2021
        assert meta['dct_temporal_enddate'].year == 2024
        assert meta['dct_accessurl'] is None and meta['dct_license'] is None
        db.apply_source(cfg(url='https://example.org/2024.csv', license='License'), [])
        meta = db.conn.execute('SELECT * FROM table_metadata').fetchone()
        assert meta['dct_temporal_startdata'].year == meta['dct_temporal_enddate'].year == 2021
        assert db.conn.execute('SELECT count(*) AS n FROM dashboard_data_de').fetchone()['n'] == 1


def test_preexisting_catalogue_entry_is_preserved_but_disabled_when_empty(database):
    with Db(database) as db:
        db.ensure_control_schema()
        db.conn.execute("INSERT INTO table_metadata(table_name,dcatde_politicalgeocodingleveluri,dct_catalog_publisher) VALUES('Population','Gemeinden','Statistics')")
        db.apply_source(cfg(), [attr()])
        db.apply_source(cfg(), [])
        meta = db.conn.execute('SELECT * FROM table_metadata').fetchone()
        assert meta is not None
        assert meta['geometry_type'] is None
        assert meta['dct_temporal_startdata'] is None and meta['dct_temporal_enddate'] is None
