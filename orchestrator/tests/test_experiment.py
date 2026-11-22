import csv
import json
from collections import Counter
from pathlib import Path

import pytest

from orchestrator.config import Config, Experiment, Variant
from orchestrator.experiment import (
    ExperimentError,
    detect_libc,
    make_plan,
    run_experiment,
)
from orchestrator.qemu_runner import RunResult

SAMPLE = Path(__file__).parent / "data" / "report_base_sample.txt"
QEMU = "/nonexistent/qemu"


def quiet(_message):
    return None


def make_config(tmp_path):
    out = tmp_path / "out"
    for name, libc in (("base", "GLIBC"), ("musl", "MUSL")):
        images = out / name / "images"
        images.mkdir(parents=True)
        (images / "bzImage").write_bytes(b"k")
        (images / "rootfs.ext2").write_bytes(b"r")
        (out / name / ".config").write_text(f"BR2_x86_64=y\nBR2_TOOLCHAIN_BUILDROOT_{libc}=y\n")
    return Config(
        project_dir=tmp_path,
        buildroot_dir=tmp_path / "buildroot",
        external_dir=tmp_path / "custom-layer",
        dl_dir=tmp_path / "dl",
        out_dir=out,
        results_dir=tmp_path / "results",
        experiment=Experiment(repetitions=2, warmup_runs=1, kvm=False),
        variants=(
            Variant("base", "base_defconfig", "glibc"),
            Variant("musl", "musl_defconfig", "musl"),
        ),
    )


def fake_runner(report_text, status="ok"):
    calls = []

    def runner(experiment, images_dir, qemu_binary, scenarios=None):
        calls.append(images_dir)
        return RunResult(
            status=status,
            boot_host_s=1.0,
            total_s=2.0,
            report=report_text,
            console="c",
            exit_code=0,
            command=("qemu",),
        )

    runner.calls = calls
    return runner


def read_rows(store):
    with open(store.runs_file, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_plan_counts_and_order():
    plan = make_plan(["base", "musl"], 3, 1, 42)
    assert len(plan) == 8
    assert [slot.order for slot in plan] == list(range(1, 9))
    assert [slot.phase for slot in plan[:2]] == ["warmup", "warmup"]
    assert {slot.phase for slot in plan[2:]} == {"measure"}


def test_plan_blocks_contain_every_variant_once():
    plan = make_plan(["base", "musl"], 5, 0, 42)
    for index in range(1, 6):
        block = [slot.variant for slot in plan if slot.index == index]
        assert sorted(block) == ["base", "musl"]


def test_plan_is_deterministic_for_seed():
    names = ["base", "musl"]
    assert make_plan(names, 20, 1, 1) == make_plan(names, 20, 1, 1)
    assert make_plan(names, 20, 1, 1) != make_plan(names, 20, 1, 2)


def test_detect_libc_glibc(tmp_path):
    make_config(tmp_path)
    assert detect_libc(tmp_path / "out", "base") == "glibc"


def test_detect_libc_musl(tmp_path):
    make_config(tmp_path)
    assert detect_libc(tmp_path / "out", "musl") == "musl"


def test_detect_libc_unknown(tmp_path):
    make_config(tmp_path)
    (tmp_path / "out" / "base" / ".config").write_text("BR2_x86_64=y\n")
    assert detect_libc(tmp_path / "out", "base") == "unknown"


def test_detect_libc_missing_config(tmp_path):
    with pytest.raises(ExperimentError):
        detect_libc(tmp_path / "out", "base")


def test_run_experiment_writes_all_rows(tmp_path):
    config = make_config(tmp_path)
    store = run_experiment(
        config,
        qemu_binary=QEMU,
        runner=fake_runner(SAMPLE.read_text()),
        log=quiet,
    )
    rows = read_rows(store)
    assert len(rows) == 6
    assert len({row["run_id"] for row in rows}) == 6
    assert Counter(row["phase"] for row in rows) == {"warmup": 2, "measure": 4}
    assert len(list(store.raw_dir.iterdir())) == 12
    meta = json.loads(store.meta_file.read_text(encoding="utf-8"))
    assert meta["completed"] is True
    assert meta["status_counts"] == {"ok": 6}
    assert len(meta["plan"]) == 6
    assert meta["variants"][0]["libc"] == "glibc"


def test_parse_error_status(tmp_path):
    config = make_config(tmp_path)
    store = run_experiment(
        config, qemu_binary=QEMU, runner=fake_runner("bzdury"), log=quiet
    )
    rows = read_rows(store)
    assert {row["status"] for row in rows} == {"parse_error"}
    assert rows[0]["cpu_events_per_s"] == ""


def test_failed_run_passes_status_through(tmp_path):
    config = make_config(tmp_path)
    store = run_experiment(
        config, qemu_binary=QEMU, runner=fake_runner("", status="timeout"), log=quiet
    )
    rows = read_rows(store)
    assert len(rows) == 6
    assert {row["status"] for row in rows} == {"timeout"}


def test_missing_images_raises(tmp_path):
    config = make_config(tmp_path)
    (config.out_dir / "musl" / "images" / "bzImage").unlink()
    with pytest.raises(ExperimentError):
        run_experiment(
            config, qemu_binary=QEMU, runner=fake_runner(SAMPLE.read_text()), log=quiet
        )
    assert not config.results_dir.exists()


def test_unknown_variant_raises(tmp_path):
    config = make_config(tmp_path)
    with pytest.raises(ExperimentError):
        run_experiment(
            config,
            variant_names=["nope"],
            qemu_binary=QEMU,
            runner=fake_runner(SAMPLE.read_text()),
            log=quiet,
        )


def test_only_selected_variants(tmp_path):
    config = make_config(tmp_path)
    runner = fake_runner(SAMPLE.read_text())
    run_experiment(
        config, variant_names=["musl"], qemu_binary=QEMU, runner=runner, log=quiet
    )
    assert len(runner.calls) == 3
    assert set(runner.calls) == {config.out_dir / "musl" / "images"}


def test_interrupt_still_writes_meta(tmp_path):
    config = make_config(tmp_path)
    good = fake_runner(SAMPLE.read_text())

    def runner(experiment, images_dir, qemu_binary, scenarios=None):
        if good.calls:
            raise KeyboardInterrupt
        return good(experiment, images_dir, qemu_binary, scenarios)

    with pytest.raises(KeyboardInterrupt):
        run_experiment(config, qemu_binary=QEMU, runner=runner, log=quiet)
    (directory,) = list(config.results_dir.iterdir())
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    assert meta["completed"] is False
    assert meta["status_counts"] == {"ok": 1}
    with open(directory / "runs.csv", newline="", encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 1


def test_host_columns_recorded(tmp_path):
    config = make_config(tmp_path)
    store = run_experiment(
        config, qemu_binary=QEMU, runner=fake_runner(SAMPLE.read_text()), log=quiet
    )
    rows = read_rows(store)
    assert {row["host_idle_busy_pct"] for row in rows} == {"0.5"}
    assert "host_run_busy_pct" in rows[0]
    assert "host_load1" in rows[0]


def test_busy_host_warning(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.experiment.idle_probe", lambda *args, **kwargs: 20.0)
    messages = []
    run_experiment(
        make_config(tmp_path),
        qemu_binary=QEMU,
        runner=fake_runner(SAMPLE.read_text()),
        log=messages.append,
    )
    assert any("UWAGA" in message for message in messages)


def test_parse_error_note_recorded(tmp_path):
    messages = []
    store = run_experiment(
        make_config(tmp_path),
        qemu_binary=QEMU,
        runner=fake_runner("bzdury"),
        log=messages.append,
    )
    rows = read_rows(store)
    assert all(row["note"] for row in rows)
    assert any("END" in message for message in messages)


def test_ok_run_has_empty_note(tmp_path):
    store = run_experiment(
        make_config(tmp_path),
        qemu_binary=QEMU,
        runner=fake_runner(SAMPLE.read_text()),
        log=quiet,
    )
    assert {row["note"] for row in read_rows(store)} == {""}


def write_config(tmp_path, name, text):
    directory = tmp_path / "out" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".config").write_text(text)


from dataclasses import replace

FRAGMENTS = {
    ("libc", "glibc"): "BR2_TOOLCHAIN_BUILDROOT_GLIBC=y\n",
    ("libc", "musl"): "BR2_TOOLCHAIN_BUILDROOT_MUSL=y\n",
    ("opt", "Os"): "BR2_OPTIMIZE_S=y\n",
    ("opt", "O3"): "BR2_OPTIMIZE_3=y\n",
    ("kernel", "default"): 'BR2_LINUX_KERNEL_CUSTOM_CONFIG_FILE="board/qemu/x86_64/linux.config"\n',
    ("kernel", "tiny"): 'BR2_LINUX_KERNEL_CUSTOM_CONFIG_FILE="$(BR2_EXTERNAL_CUSTOM_LAYER_PATH)/board/linux-tiny.defconfig"\n',
    ("fs", "ext2"): "# BR2_TARGET_ROOTFS_TAR is not set\n",
}


def write_fragments(tmp_path):
    factors = tmp_path / "custom-layer" / "factors"
    for (factor, level), text in FRAGMENTS.items():
        (factors / factor).mkdir(parents=True, exist_ok=True)
        (factors / factor / f"{level}.fragment").write_text(text)


def factor_variant(name, **levels):
    return Variant(name, None, ", ".join(levels.values()), factors=tuple(levels.items()))


def factor_config(tmp_path, declared_libc="glibc", declared_kernel="default"):
    config = make_config(tmp_path)
    write_fragments(tmp_path)
    write_config(
        tmp_path,
        "base",
        "BR2_TOOLCHAIN_BUILDROOT_GLIBC=y\nBR2_OPTIMIZE_S=y\n"
        'BR2_LINUX_KERNEL_CUSTOM_CONFIG_FILE="board/qemu/x86_64/linux.config"\n',
    )
    write_config(
        tmp_path,
        "musl",
        "BR2_TOOLCHAIN_BUILDROOT_MUSL=y\nBR2_OPTIMIZE_3=y\n"
        'BR2_LINUX_KERNEL_CUSTOM_CONFIG_FILE="$(BR2_EXTERNAL_CUSTOM_LAYER_PATH)/board/linux-tiny.defconfig"\n',
    )
    variants = (
        factor_variant("base", libc=declared_libc, opt="Os", kernel=declared_kernel),
        factor_variant("musl", libc="musl", opt="O3", kernel="tiny"),
    )
    return replace(config, variants=variants)


def test_declared_factors_match_fragments(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.experiment.defconfig_sha256", lambda config, variant: "x")
    store = run_experiment(
        factor_config(tmp_path),
        qemu_binary=QEMU,
        runner=fake_runner(SAMPLE.read_text()),
        log=quiet,
    )
    rows = read_rows(store)
    assert len(rows) == 6
    assert {(row["libc"], row["opt"], row["kernel"]) for row in rows if row["variant"] == "musl"} == {
        ("musl", "O3", "tiny")
    }
    meta = json.loads(store.meta_file.read_text(encoding="utf-8"))
    assert meta["factors"] == ["libc", "opt", "kernel"]
    assert meta["variants"][1]["factors"] == {"libc": "musl", "opt": "O3", "kernel": "tiny"}
    assert meta["variants"][1]["libc"] == "musl"


def test_declared_libc_mismatch_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.experiment.defconfig_sha256", lambda config, variant: "x")
    config = factor_config(tmp_path, declared_libc="musl")
    with pytest.raises(ExperimentError, match="libc=musl: oczekiwano BR2_TOOLCHAIN_BUILDROOT_MUSL=y"):
        run_experiment(config, qemu_binary=QEMU, runner=fake_runner(SAMPLE.read_text()), log=quiet)
    assert not config.results_dir.exists()


def test_declared_kernel_mismatch_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.experiment.defconfig_sha256", lambda config, variant: "x")
    config = factor_config(tmp_path, declared_kernel="tiny")
    with pytest.raises(ExperimentError, match="kernel=tiny"):
        run_experiment(config, qemu_binary=QEMU, runner=fake_runner(SAMPLE.read_text()), log=quiet)


def test_option_missing_from_config_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.experiment.defconfig_sha256", lambda config, variant: "x")
    config = factor_config(tmp_path)
    write_config(tmp_path, "base", "BR2_TOOLCHAIN_BUILDROOT_GLIBC=y\n")
    with pytest.raises(ExperimentError, match="w .config brak opcji"):
        run_experiment(config, qemu_binary=QEMU, runner=fake_runner(SAMPLE.read_text()), log=quiet)


def test_additional_factor_needs_no_code(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.experiment.defconfig_sha256", lambda config, variant: "x")
    config = factor_config(tmp_path)
    variants = tuple(
        replace(variant, factors=variant.factors + (("fs", "ext2"),)) for variant in config.variants
    )
    store = run_experiment(
        replace(config, variants=variants),
        qemu_binary=QEMU,
        runner=fake_runner(SAMPLE.read_text()),
        log=quiet,
    )
    rows = read_rows(store)
    assert {row["fs"] for row in rows} == {"ext2"}
    assert list(rows[0]).index("fs") == list(rows[0]).index("kernel") + 1


def test_legacy_variants_have_empty_factor_columns(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.experiment.defconfig_sha256", lambda config, variant: "x")
    config = factor_config(tmp_path)
    legacy = Variant("old", "old_defconfig", "old")
    images = config.out_dir / "old" / "images"
    images.mkdir(parents=True)
    (images / "bzImage").write_bytes(b"k")
    (images / "rootfs.ext2").write_bytes(b"r")
    write_config(tmp_path, "old", "BR2_TOOLCHAIN_BUILDROOT_GLIBC=y\n")
    store = run_experiment(
        replace(config, variants=config.variants + (legacy,)),
        qemu_binary=QEMU,
        runner=fake_runner(SAMPLE.read_text()),
        log=quiet,
    )
    rows = [row for row in read_rows(store) if row["variant"] == "old"]
    assert {(row["libc"], row["opt"], row["kernel"]) for row in rows} == {("", "", "")}


from orchestrator.scenarios import DEFAULT_SCENARIOS, Scenario


def scenario_report():
    text = SAMPLE.read_text()
    body = text.split("=== MEMORY ===")[1].split("=== END ===")[0]
    return (
        text.replace("=== CPU ===", "=== SCENARIO cpu cpu ===")
        .replace("=== MEMORY ===", "=== SCENARIO mem memory ===")
        .replace("=== END ===", "=== SCENARIO mem_read memory ===" + body + "=== END ===")
    )


def scenario_config(tmp_path):
    scenarios = DEFAULT_SCENARIOS + (Scenario("mem_read", "memory", (("memory-oper", "read"),)),)
    return replace(make_config(tmp_path), scenarios=scenarios)


def test_configured_scenarios_become_columns(tmp_path):
    store = run_experiment(
        scenario_config(tmp_path),
        qemu_binary=QEMU,
        runner=fake_runner(scenario_report()),
        log=quiet,
    )
    rows = read_rows(store)
    assert {row["status"] for row in rows} == {"ok"}
    assert float(rows[0]["mem_read_mib_per_s"]) > 0
    meta = json.loads(store.meta_file.read_text(encoding="utf-8"))
    assert [scenario["name"] for scenario in meta["scenarios"]] == ["cpu", "mem", "mem_read"]


def test_report_missing_configured_scenario_is_parse_error(tmp_path):
    store = run_experiment(
        scenario_config(tmp_path),
        qemu_binary=QEMU,
        runner=fake_runner(SAMPLE.read_text()),
        log=quiet,
    )
    rows = read_rows(store)
    assert {row["status"] for row in rows} == {"parse_error"}
    assert all("brak scenariusza mem_read" in row["note"] for row in rows)


def test_runner_receives_configured_scenarios(tmp_path):
    seen = []
    inner = fake_runner(SAMPLE.read_text())

    def runner(experiment, images_dir, qemu_binary, scenarios=None):
        seen.append(scenarios)
        return inner(experiment, images_dir, qemu_binary, scenarios)

    config = make_config(tmp_path)
    run_experiment(config, qemu_binary=QEMU, runner=runner, log=quiet)
    assert seen
    assert all(item == config.scenarios for item in seen)
