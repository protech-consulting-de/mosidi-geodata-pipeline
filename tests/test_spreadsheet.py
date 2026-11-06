import csv
from hashlib import sha256
from types import SimpleNamespace

from openpyxl import Workbook
import pytest

from geopipeline.spreadsheet import prepare_csv


def workbook_source(tmp_path):
    path = tmp_path / "upstream_without_extension"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "GESCHLECHT_BB_GEM"
    sheet.append(["ARS", "Gemeinde", "Einwohner"])
    sheet.append([120510000000, "Brandenburg an der Havel", 73552])
    sheet.append(["120520000000", "Cottbus / Chóśebuz", 93926])
    sheet.append([None, "Fußnote", None])
    sheet.append(["© Quelle 2024", None, None])
    sheet.append([2024, "Veröffentlichungsjahr", None])
    workbook.save(path)
    return {"path": str(path), "xlsx": {"sheet": sheet.title,
            "columns": {"geo_id": 1, "name": 2, "value": 3}, "start_row": 2,
            "year": 2022, "indicator": "Einwohner (Zensus 2022)",
            "region_prefix": "12", "expected_rows": 2}}


def test_official_workbook_shape_is_deterministic_and_tracks_provenance(tmp_path):
    source = workbook_source(tmp_path)
    http = SimpleNamespace(cache_dir=tmp_path / "cache")
    path, provenance = prepare_csv(source, http)
    with path.open() as stream:
        rows = list(csv.DictReader(stream, delimiter=";"))
    assert [r["Kennziffer"] for r in rows] == ["120510000000", "120520000000"]
    assert rows[0]["Wert"] == "73552"
    assert rows[0]["Zeitbezug"] == "2022"
    assert provenance["transformed_rows"] == 2
    assert provenance["ignored_nonmatching_rows"] == 3
    assert provenance["transformed_sha256"] == sha256(path.read_bytes()).hexdigest()
    original = path.read_bytes()
    again, metadata = prepare_csv(source, http)
    assert again == path and again.read_bytes() == original
    assert metadata == provenance


def test_expected_count_failure_does_not_publish_partial_csv(tmp_path):
    source = workbook_source(tmp_path)
    source["xlsx"]["expected_rows"] = 3
    cache = tmp_path / "cache"
    with pytest.raises(ValueError, match="completeness"):
        prepare_csv(source, SimpleNamespace(cache_dir=cache))
    assert list(cache.iterdir()) == []


def test_changed_sheet_and_invalid_statistic_fail(tmp_path):
    source = workbook_source(tmp_path)
    source["xlsx"]["sheet"] = "renamed"
    with pytest.raises(ValueError, match="sheet missing"):
        prepare_csv(source, SimpleNamespace(cache_dir=tmp_path / "cache"))
    source["xlsx"]["sheet"] = "GESCHLECHT_BB_GEM"
    source["xlsx"]["columns"]["value"] = "B"
    with pytest.raises(ValueError, match="statistical value"):
        prepare_csv(source, SimpleNamespace(cache_dir=tmp_path / "cache"))
