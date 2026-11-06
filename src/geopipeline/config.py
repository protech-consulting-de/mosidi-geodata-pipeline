from pathlib import Path
import re
import yaml


def load_config(path: str | Path) -> dict:
    path = Path(path).resolve()
    with path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("Configuration must be an object with version: 1")
    sources = config.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Configuration requires a nonempty sources list")
    ids = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Every source must be an object")
        sid = source.get("id", "")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", sid) or sid in ids:
            raise ValueError(f"Invalid or duplicate source id: {sid!r}")
        ids.add(sid)
        if source.get("type") not in {"wfs", "csv"}:
            raise ValueError(f"{sid}: supported source types are wfs and csv")
        if source.get("kind") not in {"geometry", "attributes"}:
            raise ValueError(f"{sid}: kind must be geometry or attributes")
        if source["type"] == "wfs" and source["kind"] != "geometry":
            raise ValueError(f"{sid}: WFS adapter currently imports geometry records")
        if source["type"] == "csv" and source["kind"] != "attributes":
            raise ValueError(f"{sid}: CSV adapter imports attribute records")
        if not source.get("url") and not source.get("path"):
            raise ValueError(f"{sid}: requires url or local path")
        if source.get("url") and not source["url"].startswith(("https://", "http://")):
            raise ValueError(f"{sid}: URL must be HTTP(S)")
        if source.get("path"):
            source["path"] = str((path.parent / source["path"]).resolve())
        if source["type"] == "csv":
            for column, rule in source.get("filters", {}).items():
                if not isinstance(rule, dict) or not rule or set(rule) - {"equals", "in", "prefix"}:
                    raise ValueError(f"{sid}: unsupported or empty filter for {column}")
                if "in" in rule and not isinstance(rule["in"], list):
                    raise ValueError(f"{sid}: filter 'in' must be a list")
            if not source.get("key_fields") or not isinstance(source.get("fields"), dict):
                raise ValueError(f"{sid}: requires key_fields and fields mapping")
            for field in ("indikator", "kennziffer", "wert", "zeitbezug"):
                if field not in source["fields"]:
                    raise ValueError(f"{sid}: missing field mapping {field}")
            for field, spec in source["fields"].items():
                if isinstance(spec, dict):
                    if ("column" in spec) == ("constant" in spec):
                        raise ValueError(f"{sid}: field {field} needs exactly one column or constant")
                    if set(spec) - {"column", "constant", "transform"} or spec.get("transform") not in {None,"ars_to_ags"}:
                        raise ValueError(f"{sid}: unsupported field mapping for {field}")
                elif not isinstance(spec,str):
                    raise ValueError(f"{sid}: field {field} must name a column or explicit constant")
        else:
            for field in ("feature_type", "id_field", "name_field", "axis_order"):
                if field not in source:
                    raise ValueError(f"{sid}: missing WFS setting {field}")
            if source["axis_order"] not in {"xy", "yx"}:
                raise ValueError(f"{sid}: axis_order must be xy or yx")
        if int(source.get("interval_seconds", 86400)) < 60:
            raise ValueError(f"{sid}: interval_seconds must be at least 60")
    return config

