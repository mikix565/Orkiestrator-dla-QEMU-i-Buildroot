import csv
import json
from datetime import datetime
from pathlib import Path

import pytest

from orchestrator.parser import parse_report
from orchestrator.qemu_runner import RunResult
from orchestrator.storage import (
    RUN_COLUMNS,
    ExperimentStore,
    StorageError,
    build_row,
    make_run_id,
    new_experiment_id,
)

SAMPLE = Path(__file__).parent / "data" / "report_base_sample.txt"


def fake_result(status="ok"):
    return RunResult(
        status=status,
        boot_host_s=1.4,
        total_s=24.0,
        report="r",
        console="c",
        exit_code=0,
        command=("qemu",),
    )


def make_row(run_id="e_base_r01", status="ok", report=None):
    return build_row(
        run_id=run_id,
        experiment_id="e",
        variant="base",
        label="glibc",
        factors={"libc": "glibc", "opt": "O2", "kernel": "default"},
        phase="measure",
        index=1,
        order=1,
        result=fake_result(status),
        report=report,
    )


def read_rows(store):
    with open(store.runs_file, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_experiment_id_format():
    assert new_experiment_id(datetime(2026, 10, 23, 15, 30, 12)) == "20261023-153012"


def test_run_id_format():
    assert make_run_id("20261023-153012", "musl", "measure", 3) == "20261023-153012_musl_r03"
    assert make_run_id("20261023-153012", "base", "warmup", 1) == "20261023-153012_base_w01"


def test_run_id_rejects_unknown_phase():
    with pytest.raises(StorageError):
        make_run_id("e", "base", "other", 1)


def test_store_creates_layout(tmp_path):
    store = ExperimentStore(tmp_path, "e")
    assert (tmp_path / "e").is_dir()
    assert (tmp_path / "e" / "raw").is_dir()
    assert store.runs_file == tmp_path / "e" / "runs.csv"


def test_store_refuses_existing_experiment(tmp_path):
    ExperimentStore(tmp_path, "e")
    with pytest.raises(StorageError):
        ExperimentStore(tmp_path, "e")


def test_store_rejects_unsafe_id(tmp_path):
    with pytest.raises(StorageError):
        ExperimentStore(tmp_path, "../x")


def test_append_run_writes_header_once(tmp_path):
    store = ExperimentStore(tmp_path, "e")
    store.append_run(make_row(run_id="e_base_r01"))
    store.append_run(make_row(run_id="e_base_r02"))
    text = store.runs_file.read_text(encoding="utf-8")
    assert text.splitlines()[0] == ",".join(RUN_COLUMNS)
    assert text.count("run_id,experiment_id") == 1
    assert [row["run_id"] for row in read_rows(store)] == ["e_base_r01", "e_base_r02"]


def test_failed_run_has_empty_report_fields(tmp_path):
    store = ExperimentStore(tmp_path, "e")
    store.append_run(make_row(status="timeout"))
    row = read_rows(store)[0]
    assert row["status"] == "timeout"
    assert row["cpu_events_per_s"] == ""


def test_unknown_column_rejected(tmp_path):
    store = ExperimentStore(tmp_path, "e")
    row = make_row()
    row["nieznana"] = 1
    with pytest.raises(StorageError):
        store.append_run(row)


def test_build_row_from_report():
    report = parse_report(SAMPLE.read_text())
    row = make_row(report=report)
    assert row["cpu_events_per_s"] == pytest.approx(1049.59)
    assert row["status"] == "ok"
    assert set(row) <= set(RUN_COLUMNS)


def test_save_raw(tmp_path):
    store = ExperimentStore(tmp_path, "e")
    report_path, console_path = store.save_raw("e_base_r01", "raport", "konsola")
    assert report_path.read_text(encoding="utf-8") == "raport"
    assert console_path.read_text(encoding="utf-8") == "konsola"


def test_write_meta(tmp_path):
    store = ExperimentStore(tmp_path, "e")
    store.write_meta({"a": 1, "path": Path("/x")})
    assert json.loads(store.meta_file.read_text(encoding="utf-8")) == {"a": 1, "path": "/x"}


def test_factor_columns_exist():
    assert RUN_COLUMNS.index("opt") == RUN_COLUMNS.index("libc") + 1
    assert RUN_COLUMNS.index("kernel") == RUN_COLUMNS.index("opt") + 1


from orchestrator.scenarios import DEFAULT_SCENARIOS, Scenario, scenario_columns
from orchestrator.storage import run_columns


def test_run_columns_follow_given_factors():
    columns = run_columns(DEFAULT_SCENARIOS, ("libc", "fs"))
    assert columns[4:6] == ("libc", "fs")
    assert "kernel" not in columns


def test_run_columns_reject_factor_named_like_column():
    with pytest.raises(StorageError):
        run_columns(DEFAULT_SCENARIOS, ("status",))

EXTENDED = DEFAULT_SCENARIOS + (Scenario("mem_read", "memory", (("memory-oper", "read"),)),)


def test_default_run_columns_unchanged():
    assert run_columns(DEFAULT_SCENARIOS) == RUN_COLUMNS


def test_run_columns_for_extra_scenario():
    columns = run_columns(EXTENDED)
    assert columns[: len(RUN_COLUMNS)] == RUN_COLUMNS
    assert columns[len(RUN_COLUMNS):] == scenario_columns(EXTENDED[2])


def test_run_columns_rejects_duplicates():
    with pytest.raises(StorageError, match="powtórzone kolumny"):
        run_columns((DEFAULT_SCENARIOS[0], DEFAULT_SCENARIOS[0]))


def test_store_accepts_custom_columns(tmp_path):
    store = ExperimentStore(tmp_path, "e", columns=run_columns(EXTENDED))
    row = make_row()
    row["mem_read_mib_per_s"] = 123.0
    store.append_run(row)
    assert read_rows(store)[0]["mem_read_mib_per_s"] == "123.0"


def test_default_store_rejects_extra_column(tmp_path):
    store = ExperimentStore(tmp_path, "e")
    row = make_row()
    row["mem_read_mib_per_s"] = 1.0
    with pytest.raises(StorageError):
        store.append_run(row)
