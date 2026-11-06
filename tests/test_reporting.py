"""Behavioral checks for durable, standalone and safe run reports."""

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from geopipeline import reporting


def sample_report():
    return {
        "run_id": "20261105T183000Z",
        "started_at": "2026-11-05T18:30:00Z",
        "finished_at": "2026-11-05T18:31:00Z",
        "duration_seconds": 60.0,
        "mode": "import",
        "status": "success",
        "extra": {"unicode": "Gemeinden in Brandenburg: größer, gültig", "nested": [1, None, True]},
        "source_results": [{
            "id": "brandenburg_gemeinden", "type": "wfs", "status": "success",
            "fetched_count": 10, "accepted_count": 10, "added_count": 7,
            "changed_count": 2, "removed_count": 1, "unchanged_count": 1,
            "skipped_count": 0, "repaired_count": 1, "deletion_enabled": True,
            "committed": True, "warnings": [],
            "provenance": {"url": "https://example.org/wfs?a=1&b=2", "license": "Datenlizenz Deutschland"},
        }],
    }


def test_json_preserves_all_data_and_html_is_standalone(tmp_path):
    report = sample_report()
    original = deepcopy(report)
    json_path, html_path = reporting.write_reports(report, str(tmp_path))
    assert json.loads(json_path.read_text(encoding="utf-8")) == original
    assert report == original
    markup = html_path.read_text(encoding="utf-8")
    assert '<html lang="de">' in markup
    assert "brandenburg_gemeinden" in markup
    assert "Erfolgreich" in markup
    assert '<details>' in markup
    assert "Content-Security-Policy" in markup
    assert '<script' not in markup
    assert '<link' not in markup
    assert '\\u00f6' not in json_path.read_text(encoding="utf-8")


def test_escapes_all_untrusted_content(tmp_path):
    payload = '\"><img src=x onerror=alert(1)><script>alert(2)</script>&'
    report = sample_report()
    for key in ["run_id", "status", "mode", "started_at", "finished_at", "duration_seconds"]:
        report[key] = payload
    source = report["source_results"][0]
    for key in ["id", "type", "status", "added_count", "committed", "deletion_enabled"]:
        source[key] = payload
    source["warnings"] = [payload, {"message": payload}]
    source["error"] = payload
    source["provenance"] = {"url": f"javascript:{payload}", "license": payload}
    json_path, html_path = reporting.write_reports(report, tmp_path)
    markup = html_path.read_text(encoding="utf-8")
    assert payload not in markup
    assert "<img" not in markup
    assert "<script" not in markup
    assert "&lt;script&gt;" in markup
    assert 'href="javascript:' not in markup
    assert json.loads(json_path.read_text(encoding="utf-8")) == report


def test_missing_fields_are_reported_without_inventing_counts(tmp_path):
    _, html_path = reporting.write_reports({}, tmp_path)
    markup = html_path.read_text(encoding="utf-8")
    assert "Keine Quellenergebnisse vorhanden." in markup
    assert "Keine Quelldetails vorhanden." in markup
    assert "–" in markup
    _, html_path = reporting.write_reports({"source_results": [{}]}, tmp_path)
    markup = html_path.read_text(encoding="utf-8")
    assert "Quelle 1" in markup
    assert "Keine Warnungen gemeldet." in markup


@pytest.mark.parametrize("run_id", ["../../escape", "/tmp/escape", ".", "", "\\..\\secret", "💾" * 300, None])
def test_malformed_ids_cannot_escape_output_directory(tmp_path, run_id):
    output_dir = tmp_path / "reports"
    paths = reporting.write_reports({"run_id": run_id}, output_dir)
    for path in paths:
        assert path.resolve().parent == output_dir.resolve()
        assert path.name.startswith("run-")
        assert len(path.name) < 100
        assert path.is_file()
    assert len(list(tmp_path.rglob("*.*"))) == 2


def test_different_ids_with_same_slug_do_not_overwrite(tmp_path):
    first = reporting.write_reports({"run_id": "../same"}, tmp_path)
    second = reporting.write_reports({"run_id": "same"}, tmp_path)
    assert first != second
    assert all(path.is_file() for path in first + second)


def test_dry_run_is_clearly_labeled(tmp_path):
    report = sample_report()
    report["mode"] = "dry-run"
    report["source_results"][0]["committed"] = False
    _, html_path = reporting.write_reports(report, tmp_path)
    markup = html_path.read_text(encoding="utf-8")
    assert "Prüflauf ohne Änderung der Zieldaten" in markup
    assert "Änderungszahlen zeigen eine Vorschau" in markup
    assert "<td>Nein</td>" in markup


def test_failure_before_first_source_is_visible_without_expanding_json(tmp_path):
    report = {"status": "failed", "fatal_error": {"type": "ConnectionError", "message": "Database unavailable"}}
    _, html_path = reporting.write_reports(report, tmp_path)
    markup = html_path.read_text(encoding="utf-8")
    assert "<h2>Fehler des Gesamtlaufs</h2>" in markup
    assert markup.index("Database unavailable") < markup.index("<details>")


@pytest.mark.parametrize("recovered", [True, False])
def test_run_warnings_and_recovery_are_visible_without_expanding_details(tmp_path, recovered):
    report = sample_report()
    report.update(status="warning", recovered=recovered,
                  warnings=['Interrupted report phase <script>alert("x")</script>'])
    report["source_results"][0].update(status="running", committed=False)
    _, html_path = reporting.write_reports(report, tmp_path)
    markup = html_path.read_text(encoding="utf-8")
    visible_summary = markup.split("<details>", 1)[0]
    assert "Hinweise zum Gesamtlauf" in visible_summary
    assert "Interrupted report phase &lt;script&gt;" in visible_summary
    assert "<script>" not in markup
    assert ("Wiederhergestellter Bericht" in visible_summary) is recovered
    if recovered:
        assert "kein Erfolgsnachweis" in visible_summary


def test_replacement_only_receives_complete_prepared_files(tmp_path):
    report = sample_report()
    observed = []
    real_replace = reporting.os.replace

    def check_replace(source, destination):
        source, destination = Path(source), Path(destination)
        assert source.parent == destination.parent == tmp_path
        text = source.read_text(encoding="utf-8")
        if destination.suffix == ".json":
            assert json.loads(text) == report
        else:
            assert text.startswith("<!doctype html>")
            assert text.endswith("</html>\n")
        observed.append(destination.suffix)
        real_replace(source, destination)

    with patch.object(reporting.os, "replace", side_effect=check_replace):
        reporting.write_reports(report, tmp_path)
    assert observed == [".json", ".html"]
    assert len(list(tmp_path.iterdir())) == 2


def test_preparation_failure_preserves_existing_reports_and_removes_temporary_files(tmp_path):
    original = sample_report()
    paths = reporting.write_reports(original, tmp_path)
    before = {path: path.read_bytes() for path in paths}
    updated = deepcopy(original)
    updated["status"] = "failed"
    real_prepare = reporting._prepare_file

    def fail_html(destination, text):
        if destination.suffix == ".html":
            raise OSError("Disk full")
        return real_prepare(destination, text)

    with patch.object(reporting, "_prepare_file", side_effect=fail_html):
        with pytest.raises(OSError, match="Disk full"):
            reporting.write_reports(updated, tmp_path)
    assert {path: path.read_bytes() for path in paths} == before
    assert set(tmp_path.iterdir()) == set(paths)


def test_invalid_json_value_does_not_create_partial_output(tmp_path):
    output_dir = tmp_path / "not-created"
    with pytest.raises(ValueError):
        reporting.write_reports({"duration_seconds": float("nan")}, output_dir)
    assert not output_dir.exists()


def test_repeated_write_replaces_the_same_run(tmp_path):
    report = sample_report()
    first = reporting.write_reports(report, tmp_path)
    report["status"] = "failed"
    second = reporting.write_reports(report, tmp_path)
    assert first == second
    assert json.loads(first[0].read_text(encoding="utf-8"))["status"] == "failed"
    assert "Fehlgeschlagen" in first[1].read_text(encoding="utf-8")
