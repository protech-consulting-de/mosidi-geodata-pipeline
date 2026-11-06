"""Explicit initialization, one-shot imports and periodic execution."""
from pathlib import Path
from datetime import datetime, timezone
from hashlib import sha256
import argparse
import json
import os
import sys
import time
import uuid
import re
from urllib.parse import urlsplit, parse_qsl
from importlib.resources import files
import psycopg
from . import __version__
from .config import load_config
from .db import Db
from .http import Http, public_url
from .reporting import write_reports
from .sources import fetch


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_error(exc, dsn="", source=None):
    message = str(exc)
    if dsn:
        message = message.replace(dsn, "[database connection]")
        try:
            password = urlsplit(dsn).password
            if password:message = message.replace(password, "[redacted]")
        except ValueError:
            pass
    url = (source or {}).get("url", "")
    if url:
        message = message.replace(url, public_url(url))
        parsed = urlsplit(url)
        for secret in [parsed.password, *(v for k,v in parse_qsl(parsed.query))]:
            if secret and len(secret) > 2:message = message.replace(secret, "[redacted]")
    message = re.sub(r"https?://[^\s<>\"']+", lambda match: public_url(match.group(0)), message)
    return {"type": type(exc).__name__, "message": message[:1500]}


def initialize_demo(dsn):
    """An empty, explicitly selected database is the only allowed demo target."""
    schema_sql = files("geopipeline").joinpath("sql/demo_schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(dsn) as conn:
        tables = conn.execute("""SELECT n.nspname,c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.relkind IN ('r','p','m') AND n.nspname NOT IN ('pg_catalog','information_schema')
            AND n.nspname NOT LIKE 'pg_toast%'
            AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.objid=c.oid
                AND d.classid='pg_class'::regclass AND d.deptype='e')""").fetchall()
        if tables:
            raise ValueError("init-demo requires an empty database. Existing tables detected; nothing changed.")
        conn.execute(schema_sql)
    with Db(dsn) as db:
        db.ensure_control_schema()
        db.preflight()
    print("Demo tables initialized. This schema is not THB's authoritative production dump.")


def run_pipeline(config, dsn, *, source_ids=(), dry_run=False, cache_dir=".cache", report_dir="reports"):
    started = time.monotonic()
    report = {"run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8],
              "started_at": now(), "mode": "dry-run" if dry_run else "import", "version": __version__,
              "source_results": [], "status": "running",
              "config_sha256": sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()}
    selected = set(source_ids)
    available = {source["id"] for source in config["sources"]}
    if selected - available:
        raise ValueError(f"Unknown source(s): {sorted(selected - available)}")
    http = Http(cache_dir)
    try:
        with Db(dsn) as db:
            report["database"] = db.preflight()
            if not dry_run:
                db.ensure_control_schema()
            for source in config["sources"]:
                if not source.get("enabled", True) or (selected and source["id"] not in selected):
                    continue
                result = {"id": source["id"], "type": source["type"], "status": "running", "committed": False,
                          "mode": report["mode"], "started_at": now(), "warnings": [], "provenance": {"attribution": source.get("attribution"), "license": source.get("license")}}
                report["source_results"].append(result)
                source_start = time.monotonic()
                print(f"[{source['id']}] Fetching and validating public data...", flush=True)
                try:
                    if not dry_run:db.record_source_run(report["run_id"], source["id"], result)
                    batch = fetch(source, http)
                    result.update(fetched_count=batch.fetched_count, accepted_count=len(batch.records),
                                  skipped_count=batch.skipped_count, repaired_count=batch.repaired_count,
                                  filtered_count=batch.filtered_count, deletion_enabled=batch.deletion_enabled)
                    result["provenance"].update(batch.provenance)
                    if not batch.records and not source.get("allow_empty", False):
                        raise ValueError("No valid records; source remains unchanged")
                    stats = db.apply_source(source, batch.records, dry_run=dry_run,
                                            deletion_enabled=batch.deletion_enabled, protected_keys=batch.protected_keys,
                                            run_id=report["run_id"])
                    result.update(stats)
                    result["warnings"] = batch.warnings + stats.get("warnings", [])
                    result["status"] = "warning" if result["warnings"] else "success"
                    print(f"[{source['id']}] {result['status']}: {len(batch.records)} accepted, "
                          f"{stats['added_count']} new, {stats['changed_count']} changed, "
                          f"{stats['removed_count']} removed, {stats['unchanged_count']} unchanged", flush=True)
                except Exception as exc:
                    result.update(status="failed", error=safe_error(exc,dsn,source))
                    print(f"[{source['id']}] FAILED: {result['error']['type']}: {result['error']['message']}", file=sys.stderr, flush=True)
                finally:
                    result["finished_at"] = now()
                    result["duration_seconds"] = round(time.monotonic()-source_start, 3)
                    if not dry_run:
                        try:db.record_source_run(report["run_id"], source["id"], result)
                        except Exception as exc:
                            result["warnings"].append("Run detail persistence failed: " + safe_error(exc,dsn,source)["message"])
                            if result["status"] != "failed":result["status"] = "warning"
    except Exception as exc:
        report["fatal_error"] = safe_error(exc,dsn)
    finally:
        http.close()
    results = report["source_results"]
    if report.get("fatal_error") or any(x["status"] == "failed" for x in results):
        report["status"] = "failed"
    elif any(x["status"] == "warning" for x in results):
        report["status"] = "warning"
    else:
        report["status"] = "success"
    report.update(finished_at=now(), duration_seconds=round(time.monotonic()-started,3))
    paths = write_reports(report, report_dir)
    print(f"Report: {paths[0]}\nReadable report: {paths[1]}", flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="MOSIDI WFS/CSV import pipeline")
    parser.add_argument("--config", default="config/demo.yaml")
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--cache-dir", default=".cache")
    parser.add_argument("--report-dir", default="reports")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Validate configuration and target schema without writes")
    sub.add_parser("init-demo", help="Initialize explicitly selected EMPTY demo database only")
    restore = sub.add_parser("export-report", help="Regenerate reports from durable database evidence")
    restore.add_argument("run_id")
    for name in ("run", "schedule"):
        command = sub.add_parser(name)
        command.add_argument("--source", action="append", default=[])
        command.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("Set DATABASE_URL or --dsn")
    try:
        if args.command == "init-demo":
            initialize_demo(args.dsn)
            return 0
        if args.command == "export-report":
            with Db(args.dsn) as db:results = db.read_run(args.run_id)
            if not results:raise ValueError("No durable evidence for this run ID")
            report = {"run_id":args.run_id,"mode":"import","version":__version__,"recovered":True,
                      "source_results":results,"status":"warning",
                      "warnings":["Recovered from source-level evidence. Running or committed statuses may reflect an interrupted reporting phase."]}
            paths = write_reports(report,args.report_dir)
            print(f"Recovered JSON: {paths[0]}\nRecovered HTML: {paths[1]}")
            return 0
        config = load_config(args.config)
        if args.command == "check":
            with Db(args.dsn) as db:
                result = db.preflight()
            print(json.dumps({"configuration": "valid", "sources": [s["id"] for s in config["sources"]], "database": result}, indent=2))
            return 0
        if args.command == "run":
            report = run_pipeline(config, args.dsn, source_ids=args.source, dry_run=args.dry_run,
                                  cache_dir=args.cache_dir, report_dir=args.report_dir)
            return 1 if report["status"] == "failed" else 0
        # Scheduling is serial; the database lock also protects independent instances.
        selected = set(args.source)
        ids = {s["id"] for s in config["sources"]}
        if selected - ids:raise ValueError(f"Unknown sources: {selected-ids}")
        deadlines = {s["id"]: 0.0 for s in config["sources"] if s.get("enabled", True) and (not selected or s["id"] in selected)}
        if not deadlines:raise ValueError("No enabled sources selected")
        print("Scheduler started; Ctrl+C stops after interrupting the current run.", flush=True)
        while True:
            due = [sid for sid,due_at in deadlines.items() if due_at <= time.monotonic()]
            if due:
                run_pipeline(config,args.dsn,source_ids=due,dry_run=args.dry_run,cache_dir=args.cache_dir,report_dir=args.report_dir)
                for source in config["sources"]:
                    if source["id"] in due:
                        deadlines[source["id"]] = time.monotonic() + int(source.get("interval_seconds",86400))
            time.sleep(min(30,max(1,min(deadlines.values())-time.monotonic())))
    except KeyboardInterrupt:
        print("Stopped.",file=sys.stderr)
        return 130
    except Exception as exc:
        error = safe_error(exc,args.dsn)
        print(f"{error['type']}: {error['message']}", file=sys.stderr)
        return 2

