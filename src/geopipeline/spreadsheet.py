"""Normalize public statistical XLSX tables to a provenance-tracked CSV."""
import csv
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from openpyxl import load_workbook
from openpyxl.utils.cell import column_index_from_string


def _sha256(path):
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _column(value):
    index = column_index_from_string(value) if isinstance(value, str) else int(value)
    if index < 1:
        raise ValueError("XLSX column numbers are one-based")
    return index - 1


def _geo_id(value, width):
    # Only pure integral identifiers can be area keys. Notes and footers are
    # rejected; zfill preserves leading zeroes lost by Excel's numeric storage.
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if not Decimal(str(value)).is_finite() or value != int(value):
            return None
        value = str(int(value))
    value = str(value).strip()
    if not value.isascii() or not value.isdigit() or len(value) > width:
        return None
    return value.zfill(width)


def prepare_csv(source: dict, http) -> tuple[Path, dict]:
    """Read ``source['xlsx']`` and return a deterministic semicolon CSV.

    Required settings: sheet, columns (geo_id/name/value), year, indicator.
    Optional: start_row, geo_id_width (12), region_prefix, expected_rows,
    max_rows. Only the configured sheet is read; macros are never executed.
    """
    settings = source["xlsx"]
    if source.get("path"):
        path = Path(source["path"])
        upstream = {"local_file": path.name, "bytes": path.stat().st_size,
                    "sha256": _sha256(path)}
    else:
        path, upstream = http.download(source["url"], int(source.get("max_download_bytes", 50_000_000)))
    checksum = _sha256(path)
    if upstream.get("sha256") and upstream["sha256"] != checksum:
        raise ValueError("XLSX cache checksum differs from download provenance")
    upstream = {**upstream, "sha256": checksum}
    columns = {name: _column(settings["columns"][name]) for name in ("geo_id", "name", "value")}
    start_row = int(settings.get("start_row", 1))
    width = int(settings.get("geo_id_width", 12))
    year = int(settings["year"])
    indicator = str(settings["indicator"]).strip()
    if start_row < 1 or width < 1 or not indicator or not 1800 <= year <= 2200:
        raise ValueError("Invalid XLSX transformation settings")
    prefix = str(settings.get("region_prefix", ""))
    transform = {"sheet": settings["sheet"], "columns": settings["columns"],
                 "start_row": start_row, "geo_id_width": width, "year": year,
                 "indicator": indicator, "region_prefix": prefix,
                 "expected_rows": settings.get("expected_rows")}
    cache_key = sha256(json.dumps({"sha256": checksum, "transform": transform},
                                  sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    output = Path(http.cache_dir) / f"xlsx-{cache_key}.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=output.parent, prefix="xlsx-")
    accepted, ignored, seen = 0, 0, set()
    workbook = None
    try:
        # Http cache names intentionally have no suffix. Passing an open binary
        # stream avoids openpyxl's filename extension check without weakening ZIP
        # or workbook validation.
        with path.open("rb") as binary:
            workbook = load_workbook(binary, read_only=True, data_only=True)
            if settings["sheet"] not in workbook.sheetnames:
                raise ValueError(f"XLSX sheet missing: {settings['sheet']}")
            sheet = workbook[settings["sheet"]]
            if sheet.max_row and sheet.max_row > int(settings.get("max_rows", 1_000_000)):
                raise ValueError("XLSX sheet exceeds row limit")
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                fd = None
                writer = csv.writer(stream, delimiter=";", lineterminator="\n")
                writer.writerow(["Kennziffer", "Name", "Zeitbezug", "Wert", "Indikator"])
                for index, row in enumerate(sheet.iter_rows(min_row=start_row,
                                          max_col=max(columns.values()) + 1,
                                          values_only=True), start=start_row):
                    if index > int(settings.get("max_rows", 1_000_000)):
                        raise ValueError("XLSX sheet exceeds row limit")
                    key = _geo_id(row[columns["geo_id"]], width)
                    if key is None or (prefix and not key.startswith(prefix)):
                        ignored += 1
                        continue
                    if key in seen:
                        raise ValueError(f"Duplicate XLSX area key at row {index}: {key}")
                    seen.add(key)
                    name = str(row[columns["name"]] or "").strip()
                    raw_value = row[columns["value"]]
                    try:
                        value = Decimal(str(raw_value))
                    except InvalidOperation as exc:
                        raise ValueError(f"Invalid XLSX statistical value at row {index}") from exc
                    if not name or not value.is_finite():
                        raise ValueError(f"Missing name or non-finite value at XLSX row {index}")
                    writer.writerow([key, name, year, format(value, "f"), indicator])
                    accepted += 1
            expected = settings.get("expected_rows")
            if accepted == 0 or (expected is not None and accepted != int(expected)):
                raise ValueError(f"XLSX completeness check failed: {accepted} rows, expected {expected}")
        os.replace(temporary, output)
    finally:
        if workbook is not None:
            workbook.close()
        if fd is not None:
            os.close(fd)
        if os.path.exists(temporary):
            os.unlink(temporary)
    return output, {**upstream, "input_format": "xlsx", "transformation": transform,
                    "transformed_sha256": _sha256(output), "transformed_bytes": output.stat().st_size,
                    "transformed_rows": accepted, "ignored_nonmatching_rows": ignored}
