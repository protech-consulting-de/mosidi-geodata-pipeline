"""Parser and completeness tests using controlled local CSV/GML responses."""

from copy import deepcopy
import json

import pytest

from geopipeline.config import load_config
from geopipeline.sources import fetch_csv, fetch_wfs, parse_gml_geometry, xml_root


GML = "http://www.opengis.net/gml/3.2"
WFS = "http://www.opengis.net/wfs/2.0"


def polygon_feature(key="12001000", name="Brandenburg", axis="xy", valid=True):
    coordinates = "13 52 14 52 14 53 13 53 13 52"
    if axis == "yx":
        coordinates = "52 13 52 14 53 14 53 13 52 13"
    geometry = (
        f'<gml:Polygon srsName="urn:ogc:def:crs:EPSG::4326">'
        f'<gml:exterior><gml:LinearRing><gml:posList>{coordinates}</gml:posList>'
        '</gml:LinearRing></gml:exterior></gml:Polygon>'
        if valid else ''
    )
    return f'<wfs:member><local:Gemeinde><local:ags>{key}</local:ags><local:name>{name}</local:name><local:geom>{geometry}</local:geom></local:Gemeinde></wfs:member>'


def page(*features):
    return (
        f'<wfs:FeatureCollection xmlns:wfs="{WFS}" xmlns:gml="{GML}" xmlns:local="urn:test">'
        + ''.join(features) + '</wfs:FeatureCollection>'
    ).encode()


def hits(count):
    return f'<wfs:FeatureCollection xmlns:wfs="{WFS}" numberMatched="{count}"/>'.encode()


class WfsHttp:
    def __init__(self, count, pages, final_count=None):
        self.count = count
        self.final_count = count if final_count is None else final_count
        self.pages = list(pages)
        self.hit_calls = 0
        self.start_indices = []

    def get(self, url, params, max_bytes):
        if params.get("resultType") == "hits":
            self.hit_calls += 1
            return hits(self.count if self.hit_calls == 1 else self.final_count)
        self.start_indices.append(params["startIndex"])
        return self.pages.pop(0)


def wfs_source(**overrides):
    result = {
        "id": "gemeinden", "type": "wfs", "kind": "geometry",
        "url": "https://example.org/wfs", "feature_type": "local:Gemeinde",
        "id_field": "ags", "name_field": "name", "axis_order": "xy",
        "page_size": 1, "request_pause": 0,
        "expected_bounds": [10, 50, 16, 55],
    }
    result.update(overrides)
    return result


def csv_source(tmp_path, rows, **overrides):
    path = tmp_path / "source.csv"
    path.write_text(rows, encoding="utf-8")
    result = {
        "id": "statistics", "type": "csv", "kind": "attributes", "path": str(path),
        "key_fields": ["geo", "year", "indicator"],
        "fields": {"kennziffer": "geo", "zeitbezug": "year", "indikator": "indicator", "wert": "value"},
        "delimiter": ";", "decimal": ",", "geo_id_width": 8,
    }
    result.update(overrides)
    return result


def test_wfs_paginates_and_verifies_count_after_complete_read():
    http = WfsHttp(2, [page(polygon_feature("12001000")), page(polygon_feature("12002000"))])
    batch = fetch_wfs(wfs_source(), http)
    assert http.start_indices == [0, 1]
    assert http.hit_calls == 2
    assert batch.fetched_count == len(batch.records) == 2
    assert batch.deletion_enabled is True
    assert batch.provenance["matched"] == 2


def test_wfs_repeated_page_is_not_a_complete_import():
    http = WfsHttp(2, [page(polygon_feature()), page(polygon_feature())])
    with pytest.raises(ValueError, match="Duplicate WFS key|repeated page"):
        fetch_wfs(wfs_source(), http)


@pytest.mark.parametrize("final_count", [1, 3])
def test_wfs_changed_total_prevents_committing_snapshot(final_count):
    http = WfsHttp(2, [page(polygon_feature("12001000")), page(polygon_feature("12002000"))], final_count)
    with pytest.raises(ValueError, match="count changed"):
        fetch_wfs(wfs_source(), http)


def test_wfs_missing_page_prevents_deleting_missing_objects():
    http = WfsHttp(2, [page(polygon_feature()), page()])
    with pytest.raises(ValueError, match="Incomplete WFS pagination"):
        fetch_wfs(wfs_source(), http)


def test_wfs_overfull_response_rejected():
    http = WfsHttp(1, [page(polygon_feature("12001000"), polygon_feature("12002000"))])
    with pytest.raises(ValueError, match="more features"):
        fetch_wfs(wfs_source(), http)


def test_wfs_unknown_total_rejected():
    with pytest.raises(ValueError, match="verifiable total"):
        fetch_wfs(wfs_source(), WfsHttp("unknown", []))


def test_wfs_empty_source_requires_explicit_permission():
    with pytest.raises(ValueError, match="unexpectedly empty"):
        fetch_wfs(wfs_source(), WfsHttp(0, []))
    batch = fetch_wfs(wfs_source(allow_empty=True), WfsHttp(0, []))
    assert batch.records == []


def test_wfs_lat_lon_axis_is_converted_to_lon_lat():
    from shapely import from_wkt
    batch = fetch_wfs(wfs_source(axis_order="yx"), WfsHttp(1, [page(polygon_feature(axis="yx"))]))
    assert len(batch.records) == 1
    geometry = from_wkt(batch.records[0]["geometry"]["wkt"])
    assert geometry.bounds == (13.0, 52.0, 14.0, 53.0)


def test_wfs_wrong_axis_protects_existing_record():
    batch = fetch_wfs(wfs_source(axis_order="xy"), WfsHttp(1, [page(polygon_feature(axis="yx"))]))
    assert batch.records == []
    assert batch.protected_keys == {"12001000"}
    assert batch.skipped_count == 1
    assert any("axis order" in warning for warning in batch.warnings)


def test_wfs_missing_geometry_protects_key_and_missing_key_disables_deletion():
    missing_geometry = polygon_feature("12001000", valid=False)
    missing_key = polygon_feature("", valid=True)
    batch = fetch_wfs(wfs_source(page_size=2), WfsHttp(2, [page(missing_geometry, missing_key)]))
    assert batch.records == []
    assert batch.protected_keys == {"12001000"}
    assert batch.deletion_enabled is False
    assert batch.skipped_count == 2


def test_csv_filter_decimal_comma_and_leading_zero_preservation(tmp_path):
    source = csv_source(
        tmp_path,
        "geo;year;indicator;value\n12001000;2024;population;1.234,50\n11000000;2024;population;900,0\n",
        filters={"geo": {"prefix": "12"}}, thousands=".",
    )
    batch = fetch_csv(source, None)
    assert batch.fetched_count == 2
    assert batch.filtered_count == 1
    assert batch.records[0]["attributes"]["wert"] == "1234.50"
    assert batch.records[0]["attributes"]["kennziffer"] == "12001000"
    assert batch.records[0]["attributes"]["zeitbezug"] == 2024


def test_csv_pads_geographic_codes_when_configured(tmp_path):
    source = csv_source(tmp_path, "geo;year;indicator;value\n1001000;2024;population;10\n")
    batch = fetch_csv(source, None)
    assert batch.records[0]["attributes"]["kennziffer"] == "01001000"


def test_csv_duplicate_keys_abort_entire_source(tmp_path):
    source = csv_source(tmp_path, "geo;year;indicator;value\n12001000;2024;population;10\n12001000;2024;population;11\n")
    with pytest.raises(RuntimeError, match="Duplicate CSV key"):
        fetch_csv(source, None)


@pytest.mark.parametrize("row", ["12001000;2024;population;10;extra", "12001000;2024;population"])
def test_csv_malformed_row_aborts_entire_source(tmp_path, row):
    source = csv_source(tmp_path, "geo;year;indicator;value\n" + row + "\n")
    with pytest.raises(ValueError, match="Malformed CSV row"):
        fetch_csv(source, None)


def test_csv_invalid_value_protects_known_key(tmp_path):
    source = csv_source(tmp_path, "geo;year;indicator;value\n12001000;2024;population;10\n12002000;2024;population;NA\n")
    batch = fetch_csv(source, None)
    assert len(batch.records) == 1
    assert batch.protected_keys == {json.dumps(["12002000", "2024", "population"], separators=(",", ":"))}
    assert batch.skipped_count == 1
    assert batch.deletion_enabled is True


def test_csv_missing_key_disables_deletion(tmp_path):
    source = csv_source(tmp_path, "geo;year;indicator;value\n12001000;2024;population;10\n;2024;population;11\n")
    batch = fetch_csv(source, None)
    assert len(batch.records) == 1
    assert batch.deletion_enabled is False


@pytest.mark.parametrize("rows", ["geo;year;indicator;value\n", "geo;year;indicator;value\n12001000;2024;population;NaN\n"])
def test_csv_empty_or_all_invalid_scope_cannot_delete(tmp_path, rows):
    with pytest.raises(ValueError, match="No valid records"):
        fetch_csv(csv_source(tmp_path, rows), None)


def test_csv_changed_columns_fail_before_import(tmp_path):
    source = csv_source(tmp_path, "geo;year;indicator;renamed\n12001000;2024;population;10\n")
    with pytest.raises(ValueError, match="missing columns"):
        fetch_csv(source, None)


def test_csv_duplicate_header_is_rejected_instead_of_silently_overwriting(tmp_path):
    source = csv_source(tmp_path, "geo;year;indicator;value;value\n12001000;2024;population;10;99\n")
    with pytest.raises(ValueError, match="[Dd]uplicate.*[Cc]olumn|[Dd]uplicate.*header"):
        fetch_csv(source, None)


def test_gml_curve_preserves_all_contiguous_segments():
    curve = xml_root(f'''<gml:Curve xmlns:gml="{GML}"><gml:segments>
    <gml:LineStringSegment><gml:posList>13 52 14 52</gml:posList></gml:LineStringSegment>
    <gml:LineStringSegment><gml:posList>14 52 14 53</gml:posList></gml:LineStringSegment>
    </gml:segments></gml:Curve>'''.encode())
    geometry = parse_gml_geometry(curve)
    assert list(geometry.coords) == [(13.0, 52.0), (14.0, 52.0), (14.0, 53.0)]


def test_gml_multicurve_preserves_curve_members():
    geometry_xml = xml_root(f'''<gml:MultiCurve xmlns:gml="{GML}"><gml:curveMember>
    <gml:Curve><gml:segments><gml:LineStringSegment><gml:posList>13 52 14 52</gml:posList></gml:LineStringSegment></gml:segments></gml:Curve>
    </gml:curveMember></gml:MultiCurve>'''.encode())
    geometry = parse_gml_geometry(geometry_xml)
    assert not geometry.is_empty
    assert len(geometry.geoms) == 1


def test_config_resolves_local_files_against_config_directory(tmp_path):
    import yaml
    source = csv_source(tmp_path, "geo;year;indicator;value\n12001000;2024;population;10\n")
    source["path"] = "source.csv"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"version": 1, "sources": [source]}))
    config = load_config(config_path)
    assert config["sources"][0]["path"] == str(tmp_path / "source.csv")


def test_config_rejects_duplicate_source_id(tmp_path):
    import yaml
    source = wfs_source()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"version": 1, "sources": [source, deepcopy(source)]}))
    with pytest.raises(ValueError, match="duplicate source id"):
        load_config(config_path)
