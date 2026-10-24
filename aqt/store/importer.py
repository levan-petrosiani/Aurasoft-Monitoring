"""
Loads existing run folders into the store: every folder with a summary.json (results) or a run.json (portal record).
Portal folders also have a run.json (status, request); CLI folders are named <BIC>-<yyyyMMdd-HHmmss>.
"""

import json
import os
from dataclasses import dataclass, field

from ..results import RunResult, parse_time
from .mapper import RunInfo


@dataclass
class ImportResult:
    imported: int = 0
    skipped: int = 0
    problems: list[str] = field(default_factory=list)


def _bic_from_portal_id(run_id: str) -> str | None:
    """Portal run ids look like yyyyMMdd-HHmmss-BIC-xxxx."""
    parts = run_id.split("-")
    return parts[2] if len(parts) == 4 else None


def read_folder(folder: str) -> tuple[RunInfo, RunResult | None]:
    summary_file = os.path.join(folder, "summary.json")
    result = None
    if os.path.isfile(summary_file):
        with open(summary_file, encoding="utf-8-sig") as f:
            result = RunResult.from_dict(json.load(f))

    name = os.path.basename(os.path.normpath(folder))
    run_file = os.path.join(folder, "run.json")
    if os.path.isfile(run_file):
        with open(run_file, encoding="utf-8-sig") as f:
            record = json.load(f)
        run_id = record.get("id") or name
        request = record.get("request") or {}
        return RunInfo(
            id=run_id, source="portal", status=record.get("status") or "Completed",
            bank_bic=(result.bank_bic if result else None) or _bic_from_portal_id(run_id) or request.get("bank") or "UNKNOWN",
            bank_name=record.get("bankName") or (result.bank_name if result else None),
            created_utc=parse_time(record.get("createdUtc")) or (result.started_at_utc if result else None),
            started_utc=parse_time(record.get("startedUtc")), finished_utc=parse_time(record.get("finishedUtc")),
            suites=list(request.get("suites") or []), error=record.get("error"),
            report_directory=os.path.abspath(folder), options_json=request or None), result

    if result is None:
        raise ValueError("neither summary.json nor a readable run.json")
    return RunInfo(id=name, source="cli", status="Completed", bank_bic=result.bank_bic, bank_name=result.bank_name,
                   created_utc=result.started_at_utc, report_directory=os.path.abspath(folder)), result


def import_folders(store, roots: list[str], replace_existing: bool, progress=None) -> ImportResult:
    outcome = ImportResult()
    folders = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for directory, _, files in os.walk(root):
            if "summary.json" in files or "run.json" in files:
                folders.add(directory)

    for folder in sorted(folders):
        try:
            info, result = read_folder(folder)
            if not replace_existing and store.run_exists(info.id):
                outcome.skipped += 1
                continue
            store.save_run(info, result)
            store.add_event("import", "run.imported", f"Imported {info.id} from {folder}", info.id)
            outcome.imported += 1
            if progress:
                detail = f"{len(result.scenarios)} scenario(s)" if result else f"{info.status}, no results"
                progress(f"{info.id}  {info.bank_bic}  {detail}")
        except (OSError, ValueError, KeyError) as ex:
            outcome.problems.append(f"{folder}: {ex}")
    return outcome
