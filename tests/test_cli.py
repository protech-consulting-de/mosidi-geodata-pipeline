import json
import requests
from geopipeline.cli import safe_error, run_pipeline
from geopipeline.sources import Batch, _mapped


def test_error_redacts_url_and_database_credentials():
    url = 'https://user:secretpass@host.test/data?token=private-token'
    error = safe_error(requests.HTTPError(f'403 for url: {url}'),
                       'postgresql://me:db-secret@localhost/db', {'url':url})
    assert 'secretpass' not in error['message']
    assert 'private-token' not in error['message']
    assert 'https://host.test/data' in error['message']


def test_ars_to_ags_conversion():
    assert _mapped({'column':'ars','transform':'ars_to_ags'}, {'ars':'120640000340'}) == '12064340'


def test_failing_source_does_not_block_next_source(monkeypatch,tmp_path):
    class FakeDb:
        def __init__(self,dsn):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def preflight(self):return {'warnings':[]}
        def ensure_control_schema(self):pass
        def record_source_run(self,*args):pass
        def apply_source(self,source,records,**kwargs):
            return dict(added_count=1,changed_count=0,removed_count=0,unchanged_count=0,committed=True)
    def fetch(source,http):
        if source['id']=='broken':raise ValueError('Source unavailable')
        return Batch(records=[{'key':'1'}],fetched_count=1)
    monkeypatch.setattr('geopipeline.cli.Db',FakeDb)
    monkeypatch.setattr('geopipeline.cli.fetch',fetch)
    report=run_pipeline({'sources':[{'id':'broken','type':'csv'},{'id':'healthy','type':'csv'}]},'unused',
                        cache_dir=tmp_path/'cache',report_dir=tmp_path/'reports')
    assert report['status']=='failed'
    assert [r['status'] for r in report['source_results']]==['failed','success']
    assert report['source_results'][1]['committed']
    assert len(list((tmp_path/'reports').glob('*.json')))==1


def test_schema_failure_still_produces_failure_report(monkeypatch,tmp_path):
    class BrokenDb:
        def __init__(self,dsn):pass
        def __enter__(self):raise ValueError('Unsupported schema')
        def __exit__(self,*a):pass
    monkeypatch.setattr('geopipeline.cli.Db',BrokenDb)
    report=run_pipeline({'sources':[{'id':'source','type':'csv'}]},'unused',cache_dir=tmp_path/'cache',report_dir=tmp_path/'reports')
    assert report['status']=='failed'
    assert report['fatal_error']['message']=='Unsupported schema'
