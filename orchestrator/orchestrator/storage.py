import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path

from orchestrator.parser import META_COLUMNS, Report
from orchestrator.scenarios import DEFAULT_SCENARIOS, scenario_columns
from orchestrator.qemu_runner import RunResult

from orchestrator.config import LEGACY_FACTORS

ID_COLUMNS = (
    "run_id",
    "experiment_id",
    "variant",
    "label",
)
BASE_COLUMNS = (
    "phase",
    "index",
    "order",
    "status",
    "boot_host_s",
    "total_s",
    "exit_code",
    "host_idle_busy_pct",
    "host_run_busy_pct",
    "host_load1",
    "note",
)
PHASE_PREFIX = {"warmup": "w", "measure": "r"}
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class StorageError(Exception):
    pass


def run_columns(scenarios, factors=LEGACY_FACTORS) -> tuple[str, ...]:
    columns = ID_COLUMNS + tuple(factors) + BASE_COLUMNS + META_COLUMNS
    for scenario in scenarios:
        columns += scenario_columns(scenario)
    duplicates = sorted({name for name in columns if columns.count(name) > 1})
    if duplicates:
        raise StorageError(f"powtórzone kolumny wyników: {', '.join(duplicates)}")
    return columns


RUN_COLUMNS = run_columns(DEFAULT_SCENARIOS)


def new_experiment_id(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y%m%d-%H%M%S")


def make_run_id(experiment_id: str, variant: str, phase: str, index: int) -> str:
    if phase not in PHASE_PREFIX:
        raise StorageError(f"nieznana faza przebiegu: {phase!r}")
    return f"{experiment_id}_{variant}_{PHASE_PREFIX[phase]}{index:02d}"


def build_row(
    *,
    run_id: str,
    experiment_id: str,
    variant: str,
    label: str,
    phase: str,
    factors: dict | None = None,
    index: int,
    order: int,
    result: RunResult,
    report: Report | None,
    host: dict | None = None,
    note: str = "",
) -> dict:
    row = {
        "run_id": run_id,
        "experiment_id": experiment_id,
        "variant": variant,
        "label": label,
        **(factors or {}),
        "phase": phase,
        "index": index,
        "order": order,
        "status": result.status,
        "boot_host_s": result.boot_host_s,
        "total_s": result.total_s,
        "exit_code": result.exit_code,
        "note": note,
    }
    if report is not None:
        row.update(report.as_dict())
    if host:
        row.update(host)
    return row


class ExperimentStore:
    def __init__(
        self,
        results_dir: Path,
        experiment_id: str,
        columns: tuple[str, ...] = RUN_COLUMNS,
    ):
        self.columns = columns
        if not SAFE_ID.match(experiment_id):
            raise StorageError(f"niepoprawny identyfikator eksperymentu: {experiment_id!r}")
        self.experiment_id = experiment_id
        self.directory = results_dir / experiment_id
        self.raw_dir = self.directory / "raw"
        self.runs_file = self.directory / "runs.csv"
        self.meta_file = self.directory / "meta.json"
        try:
            self.directory.mkdir(parents=True)
        except FileExistsError:
            raise StorageError(f"eksperyment już istnieje: {self.directory}") from None
        self.raw_dir.mkdir()

    def write_meta(self, meta: dict) -> None:
        with open(self.meta_file, "w", encoding="utf-8") as handle:
            json.dump(meta, handle, indent=2, ensure_ascii=False, default=str)
            handle.write("\n")

    def save_raw(self, run_id: str, report: str, console: str) -> tuple[Path, Path]:
        report_path = self.raw_dir / f"{run_id}.report.txt"
        console_path = self.raw_dir / f"{run_id}.console.txt"
        report_path.write_text(report, encoding="utf-8")
        console_path.write_text(console, encoding="utf-8")
        return report_path, console_path

    def append_run(self, row: dict) -> None:
        unknown = set(row) - set(self.columns)
        if unknown:
            raise StorageError(f"nieznane kolumny: {', '.join(sorted(unknown))}")
        is_new = not self.runs_file.exists() or self.runs_file.stat().st_size == 0
        with open(self.runs_file, "a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.columns)
            if is_new:
                writer.writeheader()
            writer.writerow(row)
            handle.flush()
            os.fsync(handle.fileno())
