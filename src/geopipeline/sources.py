"""Streaming CSV/ZIP and complete WFS imports with explicit CRS handling."""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from hashlib import sha256
from fnmatch import fnmatch
from contextlib import ExitStack
import csv
import io
import json
import time
import zipfile
from defusedxml import ElementTree as ET
from pyproj import CRS, Transformer
from shapely import make_valid, normalize, to_wkt
from shapely.geometry import Polygon, MultiPolygon, LineString, MultiLineString, Point, MultiPoint, shape
from shapely.ops import transform
from .http import Http, public_url


@dataclass
class Batch:
    records: list[dict] = field(default_factory=list)
    fetched_count: int = 0
    skipped_count: int = 0
    repaired_count: int = 0
    filtered_count: int = 0
    warnings: list[str] = field(default_factory=list)
    protected_keys: set[str] = field(default_factory=set)
    deletion_enabled: bool = True
    provenance: dict = field(default_factory=dict)

    def warn(self, message):
        # Bound reports even when a source contains millions of invalid rows.
        if len(self.warnings) < 100:
            self.warnings.append(message)


def local(tag):
    return tag.rsplit("}", 1)[-1]


def xml_root(data):
    root = ET.fromstring(data)
    if local(root.tag) in {"ExceptionReport", "ServiceExceptionReport"}:
        raise ValueError("WFS exception: " + " ".join(root.itertext())[:500])
    return root


def _positions(element, axis):
    poslist = next((x for x in element.iter() if local(x.tag) == "posList"), None)
    if poslist is not None:
        values = [float(x) for x in (poslist.text or "").split()]
        dim = int(poslist.get("srsDimension", element.get("srsDimension", "2")))
        if dim not in {2, 3} or len(values) % dim:
            raise ValueError("Invalid GML coordinate dimensions")
        pairs = [(values[i], values[i+1]) for i in range(0, len(values), dim)]
    else:
        positions = [x for x in element.iter() if local(x.tag) == "pos"]
        if positions:
            pairs = [tuple(float(v) for v in x.text.split()[:2]) for x in positions]
        else:
            coordinates = next((x for x in element.iter() if local(x.tag) == "coordinates"), None)
            if coordinates is None:
                raise ValueError("Geometry has no GML coordinates")
            pairs = [tuple(float(v) for v in item.split(",")[:2]) for item in (coordinates.text or "").split()]
    if not pairs or any(len(x) != 2 for x in pairs):
        raise ValueError("Empty or invalid coordinates")
    return [(y, x) for x, y in pairs] if axis == "yx" else pairs


def parse_gml_geometry(element, axis="xy"):
    kind = local(element.tag)
    if kind in {"MultiSurface", "MultiPolygon"}:
        parts = [parse_gml_geometry(x, axis) for x in element.iter() if local(x.tag) == "Polygon"]
        if not parts:
            raise ValueError("Empty MultiSurface")
        return MultiPolygon(parts)
    if kind == "Polygon":
        exterior = next((x for x in element if local(x.tag) in {"exterior", "outerBoundaryIs"}), None)
        if exterior is None:
            raise ValueError("Polygon lacks exterior")
        interiors = [x for x in element if local(x.tag) in {"interior", "innerBoundaryIs"}]
        return Polygon(_positions(exterior, axis), [_positions(x, axis) for x in interiors])
    if kind == "Point":
        return Point(_positions(element, axis)[0])
    if kind == "Curve":
        segments = [node for node in element.iter() if local(node.tag) == "LineStringSegment"]
        if not segments:
            raise ValueError("Curve requires linear segments")
        coords = []
        for segment in segments:
            points = _positions(segment, axis)
            if coords and coords[-1] != points[0]:
                raise ValueError("Disconnected Curve segments")
            coords.extend(points[1:] if coords else points)
        return LineString(coords)
    if kind == "LineString":
        return LineString(_positions(element, axis))
    if kind == "MultiPoint":
        return MultiPoint([parse_gml_geometry(x, axis) for x in element.iter() if local(x.tag) == "Point"])
    if kind in {"MultiCurve", "MultiLineString"}:
        return MultiLineString([parse_gml_geometry(x, axis) for x in element.iter() if local(x.tag) in {"LineString", "Curve"}])
    raise ValueError(f"Unsupported GML geometry: {kind}")


def _features(data):
    if data.lstrip().startswith(b"{"):
        collection = json.loads(data)
        if collection.get("type") != "FeatureCollection":
            raise ValueError("Expected GeoJSON FeatureCollection")
        return [(f.get("properties", {}), f.get("geometry"), None) for f in collection["features"]]
    root = xml_root(data)
    features = []
    for member in root:
        if local(member.tag) in {"member", "featureMember"}:
            items = list(member)
        elif local(member.tag) == "featureMembers":
            items = list(member)
        else:
            continue
        for feature in items:
            properties, geom, srs = {}, None, None
            for prop in feature:
                properties[local(prop.tag)] = (prop.text or "").strip()
                for node in prop.iter():
                    if local(node.tag) in {"MultiSurface", "MultiPolygon", "Polygon", "Point", "LineString", "Curve", "MultiPoint", "MultiCurve", "MultiLineString"}:
                        geom, srs = node, node.get("srsName")
                        break
                if geom is not None and not srs:
                    srs = prop.get("srsName")
            features.append((properties, geom, srs))
    return features


def _wfs_count(http, source, params):
    root = xml_root(http.get(source["url"], {**params, "resultType": "hits"}, max_bytes=2_000_000))
    count = root.get("numberMatched", root.get("numberOfFeatures", "unknown"))
    if not count.isdigit():
        raise ValueError("WFS did not provide a verifiable total feature count; import cancelled")
    return int(count)


def fetch_wfs(source: dict, http: Http) -> Batch:
    batch = Batch()
    version = source.get("wfs_version", "2.0.0")
    if version not in {"2.0.0", "1.1.0"}:
        raise ValueError("Supported WFS versions: 2.0.0 and 1.1.0")
    if version == "1.1.0" and not source.get("paging_supported", False):
        raise ValueError("WFS 1.1 requires verified STARTINDEX support (paging_supported)")
    params = {"service": "WFS", "version": version, "request": "GetFeature",
              "typeNames" if version == "2.0.0" else "typeName": source["feature_type"],
              "srsName": source.get("srs_name", "urn:ogc:def:crs:EPSG::4326")}
    if source.get("output_format"):
        params["outputFormat"] = source["output_format"]
    if source.get("bbox"):
        params["bbox"] = source["bbox"]
    if source.get("sort_by"):
        params["sortBy"] = source["sort_by"]
    expected = _wfs_count(http, source, params)
    if expected == 0 and not source.get("allow_empty", False):
        raise ValueError("WFS unexpectedly empty; explicit allow_empty required")
    if expected > int(source.get("max_features", 100000)):
        raise ValueError("WFS count exceeds max_features")
    seen, start, digest = set(), 0, sha256()
    page_size = int(source.get("page_size", 100))
    if page_size < 1:
        raise ValueError("page_size must be positive")
    while start < expected:
        query = {**params, "startIndex": start, "count" if version == "2.0.0" else "maxFeatures": page_size}
        raw = http.get(source["url"], query, max_bytes=int(source.get("max_page_bytes", 80_000_000)))
        digest.update(raw)
        features = _features(raw)
        if not features:
            raise ValueError(f"Incomplete WFS pagination at {start}/{expected}")
        for props, geom_data, actual_srs in features:
            batch.fetched_count += 1
            key = str(props.get(source["id_field"], "")).strip()
            if not key:
                batch.skipped_count += 1
                batch.deletion_enabled = False
                batch.warn("Feature without key skipped; deletions disabled")
                continue
            if key in seen:
                raise ValueError(f"Duplicate WFS key or repeated page: {key}")
            seen.add(key)
            try:
                if geom_data is None:
                    raise ValueError("Missing geometry")
                geojson = isinstance(geom_data, dict)
                geom = shape(geom_data) if geojson else parse_gml_geometry(geom_data, source["axis_order"])
                input_crs = "EPSG:4326" if geojson else actual_srs or source.get("source_crs", "EPSG:4326")
                crs = CRS.from_user_input(input_crs)
                if crs != CRS.from_epsg(4326):
                    geom = transform(Transformer.from_crs(crs, 4326, always_xy=True).transform, geom)
                if not geom.is_valid:
                    geom = make_valid(geom)
                    batch.repaired_count += 1
                if isinstance(geom, Polygon):
                    geom = MultiPolygon([geom])
                if geom.is_empty or not geom.is_valid:
                    raise ValueError("Geometry remains invalid")
                if geom.geom_type != "MultiPolygon":
                    raise ValueError("Known MOSIDI municipality table requires MultiPolygon")
                x1, y1, x2, y2 = geom.bounds
                if not (-180 <= x1 <= x2 <= 180 and -90 <= y1 <= y2 <= 90):
                    raise ValueError("Coordinates outside EPSG:4326 bounds")
                expected_bounds = source.get("expected_bounds")
                if expected_bounds:
                    a,b,c,e = expected_bounds
                    if not (a <= x1 <= x2 <= c and b <= y1 <= y2 <= e):
                        raise ValueError("Geometry outside configured expected bounds; check axis order")
                name = str(props.get(source["name_field"], "")).strip()
                if not name:
                    raise ValueError("Missing geometry name")
                batch.records.append({"key": key, "geometry": {"geo_id": key, "name": name,
                                      "wkt": to_wkt(normalize(geom), rounding_precision=10)}})
            except (ValueError, TypeError) as exc:
                batch.skipped_count += 1
                batch.protected_keys.add(key)
                batch.warn(f"{key}: {exc}")
        start += len(features)
        if start > expected:
            raise ValueError("WFS returned more features than the verified total")
        if start < expected:
            time.sleep(float(source.get("request_pause", 0.2)))
    if _wfs_count(http, source, params) != expected:
        raise ValueError("WFS count changed during pagination; retry required")
    if batch.fetched_count != expected:
        raise ValueError("WFS completeness could not be established")
    batch.provenance = {"url": public_url(source["url"]), "feature_type": source["feature_type"],
                        "matched": expected, "sha256": digest.hexdigest(), "wfs_version": version,
                        "axis_order": source["axis_order"], "scope": source.get("bbox", "all")}
    return batch


def _mapped(spec, row):
    if isinstance(spec, dict):
        if "constant" in spec:
            return str(spec["constant"])
        value = row[spec["column"]]
        if spec.get("transform") == "ars_to_ags":
            value = value.strip()
            if len(value) != 12 or not value.isdigit():
                raise ValueError("ARS must contain exactly twelve digits")
            return value[:5] + value[9:12]
        if spec.get("transform"):
            raise ValueError("Unsupported field transform")
        return value
    return row[spec]


def _matches(row, filters):
    for column, rule in filters.items():
        value = row[column].strip()
        if "equals" in rule and value != str(rule["equals"]):
            return False
        if "in" in rule and value not in [str(x) for x in rule["in"]]:
            return False
        if "prefix" in rule and not value.startswith(str(rule["prefix"])):
            return False
    return True


def fetch_csv(source: dict, http: Http) -> Batch:
    batch = Batch()
    archive_metadata = {}
    if source.get("format") == "xlsx":
        from .spreadsheet import prepare_csv
        path, provenance = prepare_csv(source, http)
    elif source.get("path"):
        path = Path(source["path"])
        provenance = {"local_file": path.name, "bytes": path.stat().st_size}
    else:
        path, provenance = http.download(source["url"], int(source.get("max_download_bytes", 650_000_000)))
    with ExitStack() as stack:
        raw = stack.enter_context(path.open("rb"))
        if source.get("zip_member"):
            from .archive import open_zip_member
            raw, archive_metadata = stack.enter_context(open_zip_member(path, source["zip_member"], int(source.get("max_uncompressed_bytes", 8_000_000_000))))
            provenance = {**provenance, **archive_metadata}
        stream = stack.enter_context(io.TextIOWrapper(raw, encoding=source.get("encoding", "utf-8-sig"), errors="strict", newline=""))
        for _ in range(int(source.get("skip_lines", 0))):
            next(stream)
        reader = csv.DictReader(stream, delimiter=source.get("delimiter", ";"), strict=True)
        if len(reader.fieldnames or []) != len(set(reader.fieldnames or [])):
            raise ValueError("Duplicate CSV column names; ambiguous mapping refused")
        required = set(source["key_fields"]) | set(source.get("filters", {}))
        for spec in source["fields"].values():
            if isinstance(spec, str): required.add(spec)
            elif "column" in spec: required.add(spec["column"])
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV schema changed; missing columns: {sorted(missing)}; present: {reader.fieldnames}")
        seen = set()
        for line, row in enumerate(reader, start=int(source.get("skip_lines", 0))+2):
            batch.fetched_count += 1
            if batch.fetched_count > int(source.get("max_rows", 100_000_000)):
                raise ValueError("CSV exceeds max_rows; import cancelled")
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV row at line {line}; import cancelled")
            if not _matches(row, source.get("filters", {})):
                batch.filtered_count += 1
                continue
            key = None
            try:
                parts = [row[name].strip() for name in source["key_fields"]]
                if not all(parts):
                    raise ValueError("Missing key component")
                key = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
                if key in seen:
                    raise RuntimeError(f"Duplicate CSV key at line {line}: {key}")
                seen.add(key)
                data = {name: _mapped(spec, row).strip() for name, spec in source["fields"].items()}
                if any(not data[name] for name in ("indikator", "kennziffer", "wert", "zeitbezug")):
                    raise ValueError("Missing required attribute field")
                number = data["wert"]
                if number in source.get("null_values", [".", "-", "/", "...", "NA"]):
                    raise ValueError("Missing statistical value")
                thousands = source.get("thousands", "")
                if thousands: number = number.replace(thousands, "")
                number = number.replace(source.get("decimal", ","), ".")
                value = Decimal(number)
                if not value.is_finite():
                    raise ValueError("Non-finite statistical value")
                year = int(data["zeitbezug"])
                if not 1800 <= year <= 2200:
                    raise ValueError("Unsupported year")
                if source.get("geo_id_width"):
                    if not data["kennziffer"].isdigit():
                        raise ValueError("Geographic key must contain digits only")
                    data["kennziffer"] = data["kennziffer"].zfill(int(source["geo_id_width"]))
                data.update(wert=str(value), zeitbezug=year, source=source.get("publisher", source["id"]),
                            granularity=data.get("granularity", source.get("granularity", "Gemeinden")),
                            data_type=data.get("data_type", "number"))
                batch.records.append({"key": key, "attributes": data})
            except RuntimeError:
                raise
            except (ValueError, KeyError, InvalidOperation) as exc:
                batch.skipped_count += 1
                if key:batch.protected_keys.add(key)
                else:batch.deletion_enabled=False
                batch.warn(f"Line {line}: {exc}")
    if not batch.records and not source.get("allow_empty", False):
        raise ValueError("No valid records in configured CSV scope; deletion refused")
    batch.provenance = {**provenance, **archive_metadata, "filters": source.get("filters", {}), "key_fields": source["key_fields"]}
    return batch


def fetch(source, http):
    return fetch_wfs(source, http) if source["type"] == "wfs" else fetch_csv(source, http)
