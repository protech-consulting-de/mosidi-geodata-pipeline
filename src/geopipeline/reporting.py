"""Write a run's machine-readable record and a standalone German HTML report.

Both files are first fully written in the destination directory. Each final file
is then replaced atomically, so readers never observe a partially written file.
There is no cross-file transaction; the JSON record is the authoritative result.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any


_STATUS_LABELS = {
    "success": "Erfolgreich",
    "ok": "Erfolgreich",
    "warning": "Mit Warnungen",
    "warnings": "Mit Warnungen",
    "partial": "Teilweise erfolgreich",
    "failed": "Fehlgeschlagen",
    "error": "Fehlgeschlagen",
    "skipped": "Übersprungen",
    "running": "Läuft",
}
_MODE_LABELS = {"dry-run": "Prüflauf ohne Änderung der Zieldaten", "import": "Import"}
_COUNTS = (
    ("fetched_count", "Abgerufen"),
    ("accepted_count", "Gültig"),
    ("added_count", "Neu"),
    ("changed_count", "Geändert"),
    ("removed_count", "Entfernt"),
    ("unchanged_count", "Unverändert"),
    ("skipped_count", "Ausgelassen"),
    ("repaired_count", "Repariert"),
)


def _text(value: Any) -> str:
    """Escape values for HTML text/quoted attributes; never trust input markup."""
    if value is None:
        return "–"
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    return html.escape(str(value), quote=True)


def _status(value: Any) -> str:
    return _text(_STATUS_LABELS.get(value, value) if isinstance(value, str) else value)


def _boolean(value: Any) -> str:
    if value is True:
        return "Ja"
    if value is False:
        return "Nein"
    return _text(value)


def _filename_stem(run_id: Any) -> str:
    raw = "run" if run_id is None else str(run_id)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_-")[:70] or "run"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    # Fixed prefix, restricted alphabet and hash prevent traversal and collisions
    # between different IDs that normalize to the same readable slug.
    return f"run-{slug}-{digest}"


def _source_details(source: dict[str, Any], index: int) -> str:
    identifier = source.get("id", source.get("source_id", f"Quelle {index}"))
    counts = "".join(
        f"<tr><th scope=\"row\">{label}</th><td>{_text(source.get(key))}</td></tr>"
        for key, label in _COUNTS
    )
    warnings = source.get("warnings") or []
    if not isinstance(warnings, list):
        warnings = [warnings]
    warning_list = (
        "<ul>" + "".join(f"<li>{_text(item)}</li>" for item in warnings) + "</ul>"
        if warnings else "<p>Keine Warnungen gemeldet.</p>"
    )
    error = (
        f"<h3>Fehler</h3><pre>{_text(source['error'])}</pre>"
        if source.get("error") else ""
    )
    provenance = source.get("provenance")
    provenance_html = (
        f"<h3>Herkunft und Abruf</h3><pre>{_text(provenance)}</pre>"
        if provenance is not None else ""
    )
    return f"""<details>
<summary>{_text(identifier)} <span>{_status(source.get('status'))}</span></summary>
<div class="details-content">
<p>Quelltyp: {_text(source.get('type', source.get('source_type')))} ·
Änderungen übernommen: {_boolean(source.get('committed'))} ·
Löschungen freigegeben: {_boolean(source.get('deletion_enabled'))}</p>
<table class="metrics"><caption>Kennzahlen</caption><tbody>{counts}</tbody></table>
<h3>Warnungen</h3>{warning_list}{error}{provenance_html}
</div></details>"""


def _render_html(report: dict[str, Any], json_text: str) -> str:
    raw_sources = report.get("source_results") or []
    sources = raw_sources if isinstance(raw_sources, list) else []
    records = [value for value in sources if isinstance(value, dict)]
    rows = []
    for index, source in enumerate(records, 1):
        identifier = source.get("id", source.get("source_id", f"Quelle {index}"))
        rows.append(
            "<tr>"
            f"<th scope=\"row\">{_text(identifier)}</th>"
            f"<td>{_status(source.get('status'))}</td>"
            f"<td>{_text(source.get('added_count'))}</td>"
            f"<td>{_text(source.get('changed_count'))}</td>"
            f"<td>{_text(source.get('removed_count'))}</td>"
            f"<td>{_boolean(source.get('committed'))}</td>"
            "</tr>"
        )
    rows_html = "".join(rows) or '<tr><td colspan="6">Keine Quellenergebnisse vorhanden.</td></tr>'
    mode = report.get("mode")
    mode_label = _MODE_LABELS.get(mode, mode) if isinstance(mode, str) else mode
    dry_run_notice = (
        '<p class="notice">Prüflauf: Die Änderungszahlen zeigen eine Vorschau. '
        'Die fachlichen Zieltabellen wurden durch diesen Lauf nicht geändert.</p>'
        if mode == "dry-run" else
        '<p class="notice">Die Spalte „Übernommen“ zeigt, ob Änderungen einer Quelle '
        'in der Datenbank bestätigt wurden. Warnungen und fehlgeschlagene Quellen '
        'sind in den Details erläutert.</p>'
    )
    details = "".join(_source_details(source, index) for index, source in enumerate(records, 1))
    fatal_error = (
        f"<h2>Fehler des Gesamtlaufs</h2><pre>{_text(report['fatal_error'])}</pre>"
        if report.get("fatal_error") else ""
    )
    recovered_notice = (
        '<p class="notice warning"><strong>Wiederhergestellter Bericht</strong><br>'
        'Dieser Bericht wurde aus gespeicherten Quellenbelegen rekonstruiert. '
        'Ein verbliebener Status „running“ ist kein Erfolgsnachweis; '
        'Angaben zum unterbrochenen Gesamtlauf können fehlen.</p>'
        if report.get("recovered") is True else ""
    )
    run_warnings = report.get("warnings") or []
    if not isinstance(run_warnings, list):
        run_warnings = [run_warnings]
    warnings_html = (
        '<section class="notice warning"><h2>Hinweise zum Gesamtlauf</h2><ul>'
        + "".join(f"<li>{_text(item)}</li>" for item in run_warnings)
        + "</ul></section>"
        if run_warnings else ""
    )
    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Importbericht {_text(report.get('run_id'))}</title>
<style>
:root {{ color-scheme: light; font-family: system-ui, -apple-system, sans-serif; color: #172a38; background: #f4f7f8; }}
body {{ margin: 0; line-height: 1.55; }}
main {{ max-width: 1060px; margin: 0 auto; padding: 38px 24px 60px; }}
h1 {{ font-size: 2rem; margin: 0 0 8px; line-height: 1.2; }}
h2 {{ margin-top: 32px; font-size: 1.35rem; }}
h3 {{ font-size: 1rem; margin-bottom: 6px; }}
.label {{ color: #496375; font-size: .85rem; letter-spacing: .08em; text-transform: uppercase; margin: 0 0 12px; }}
.run-id {{ overflow-wrap: anywhere; color: #496375; }}
dl {{ display: grid; grid-template-columns: minmax(120px, 180px) 1fr; gap: 6px 18px; }}
dt {{ font-weight: 650; }} dd {{ margin: 0; overflow-wrap: anywhere; }}
.notice {{ border-left: 4px solid #157c82; padding: 10px 16px; background: #e5f2f2; }}
.warning {{ border-left-color: #aa6b00; background: #fff3d6; }}
.warning h2 {{ margin: 0; }}
.table-wrap {{ overflow-x: auto; background: white; }}
table {{ border-collapse: collapse; width: 100%; font-size: .95rem; }}
th, td {{ padding: 11px 14px; text-align: left; border-bottom: 1px solid #d8e1e6; vertical-align: top; overflow-wrap: anywhere; }}
thead {{ background: #e8eef1; }} tbody th {{ font-weight: 550; }}
caption {{ text-align: left; font-weight: 650; padding: 8px 0; }}
details {{ margin: 12px 0; background: white; border: 1px solid #d8e1e6; border-radius: 6px; }}
summary {{ padding: 14px 16px; cursor: pointer; font-weight: 650; overflow-wrap: anywhere; }}
summary span {{ font-weight: 400; color: #496375; margin-left: 12px; }}
.details-content {{ padding: 0 16px 18px; }}
.metrics {{ max-width: 520px; }}
pre {{ white-space: pre-wrap; overflow-wrap: anywhere; background: #f4f7f8; padding: 12px; font-size: .86rem; }}
li {{ overflow-wrap: anywhere; margin: 6px 0; }}
footer {{ margin-top: 30px; color: #496375; font-size: .85rem; }}
@media (max-width: 600px) {{ main {{ padding: 22px 14px; }} dl {{ grid-template-columns: 1fr; gap: 0; }} dd {{ margin-bottom: 10px; }} th, td {{ padding: 9px; }} }}
@media print {{ body {{ background: white; }} main {{ padding: 0; }} details {{ break-inside: avoid; }} }}
</style>
</head>
<body><main>
<p class="label">MOSIDI Geodaten Import Pipeline</p>
<h1>Importbericht</h1>
<p class="run-id">Lauf: {_text(report.get('run_id'))}</p>
{recovered_notice}
<dl>
<dt>Gesamtstatus</dt><dd>{_status(report.get('status'))}</dd>
<dt>Modus</dt><dd>{_text(mode_label)}</dd>
<dt>Gestartet</dt><dd>{_text(report.get('started_at'))}</dd>
<dt>Beendet</dt><dd>{_text(report.get('finished_at'))}</dd>
<dt>Laufzeit</dt><dd>{_text(report.get('duration_seconds'))} Sekunden</dd>
<dt>Quellen</dt><dd>{len(records)}</dd>
</dl>
{dry_run_notice}
{warnings_html}
{fatal_error}
<h2>Ergebnisse je Quelle</h2>
<div class="table-wrap"><table>
<thead><tr><th scope="col">Quelle</th><th scope="col">Status</th><th scope="col">Neu</th><th scope="col">Geändert</th><th scope="col">Entfernt</th><th scope="col">Übernommen</th></tr></thead>
<tbody>{rows_html}</tbody></table></div>
<h2>Details und Datenherkunft</h2>
{details or '<p>Keine Quelldetails vorhanden.</p>'}
<details><summary>Vollständiger maschinenlesbarer Bericht</summary><div class="details-content"><pre>{html.escape(json_text, quote=True)}</pre></div></details>
<footer>Dieser Bericht enthält keine externen Skripte, Schriftarten oder Dienste. Angaben zur Datenherkunft stehen bei der jeweiligen Quelle.</footer>
</main></body></html>
"""


def _prepare_file(destination: Path, text: str) -> Path:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary_path
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def write_reports(report: dict, output_dir: str | Path) -> tuple[Path, Path]:
    """Return paths to complete UTF-8 JSON and standalone HTML run reports.

    ``report`` must be a JSON-serializable dictionary. All keys, including extra
    application-specific fields, are retained in the JSON. Input data is never
    modified. Existing reports for the same run ID are replaced atomically per
    file. A serialization or rendering error leaves existing files untouched.
    """
    if not isinstance(report, dict):
        raise TypeError("report must be a dictionary")
    json_text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    html_text = _render_html(report, json_text)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stem = _filename_stem(report.get("run_id"))
    json_path, html_path = directory / f"{stem}.json", directory / f"{stem}.html"
    pending: list[tuple[Path, Path]] = []
    try:
        pending.append((_prepare_file(json_path, json_text), json_path))
        pending.append((_prepare_file(html_path, html_text), html_path))
        for temporary_path, destination in pending:
            os.replace(temporary_path, destination)
    finally:
        for temporary_path, _ in pending:
            temporary_path.unlink(missing_ok=True)
    return json_path, html_path
