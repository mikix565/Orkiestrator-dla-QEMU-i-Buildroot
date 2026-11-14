import csv
from pathlib import Path

import pytest

from orchestrator.builder import (
    BuilderError,
    build_all,
    build_variant,
    directory_size,
    log_tail,
    remove_variant_dir,
)
from orchestrator.config import Config, Experiment, Variant

BASE = Variant("base", "base_defconfig", "glibc")
MUSL = Variant("musl", "musl_defconfig", "musl")


def quiet(_message):
    return None


def make_config(tmp_path):
    configs = tmp_path / "custom-layer" / "configs"
    configs.mkdir(parents=True)
    for variant in (BASE, MUSL):
        (configs / variant.defconfig).write_text("BR2_x86_64=y\n")
    return Config(
        project_dir=tmp_path,
        buildroot_dir=tmp_path / "buildroot",
        external_dir=tmp_path / "custom-layer",
        dl_dir=tmp_path / "dl",
        out_dir=tmp_path / "out",
        results_dir=tmp_path / "results",
        experiment=Experiment(),
        variants=(BASE, MUSL),
    )


def fake_runner(config, codes=None, log_text=""):
    codes = codes or {}
    calls = []

    def runner(command, cwd, log_path):
        calls.append((command, cwd, log_path))
        name = Path(command[1][2:]).name
        step = "configure" if command[-1].endswith("_defconfig") else "build"
        code = codes.get((name, step), 0)
        out = config.out_dir / name
        if code == 0 and step == "configure":
            out.mkdir(parents=True, exist_ok=True)
            libc = "MUSL" if name == "musl" else "GLIBC"
            (out / ".config").write_text(f"BR2_TOOLCHAIN_BUILDROOT_{libc}=y\n")
        if code == 0 and step == "build":
            (out / "images").mkdir(parents=True, exist_ok=True)
            (out / "images" / "bzImage").write_bytes(b"12345")
            (out / "images" / "rootfs.ext2").write_bytes(b"1234567")
            (out / "target" / "usr" / "bin").mkdir(parents=True, exist_ok=True)
            (out / "target" / "usr" / "bin" / "x").write_bytes(b"0" * 10)
            (out / "target" / "etc").mkdir(parents=True, exist_ok=True)
            (out / "target" / "etc" / "y").write_bytes(b"0" * 3)
        if code != 0:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(log_text)
        return code

    runner.calls = calls
    return runner


def read_builds(config):
    with open(config.results_dir / "builds.csv", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_fresh_build_runs_configure_then_build(tmp_path):
    config = make_config(tmp_path)
    runner = fake_runner(config)
    result = build_variant(config, BASE, runner=runner, log=quiet)
    assert result.status == "ok"
    assert result.clean_build is True
    configure, build = runner.calls
    assert configure[0] == [
        "make",
        f"O={config.out_dir / 'base'}",
        f"BR2_EXTERNAL={config.external_dir}",
        "base_defconfig",
    ]
    assert build[0] == ["make", f"O={config.out_dir / 'base'}"]
    assert configure[1] == build[1] == config.buildroot_dir


def test_artifacts_measured(tmp_path):
    config = make_config(tmp_path)
    result = build_variant(config, BASE, runner=fake_runner(config), log=quiet)
    assert result.artifacts == {
        "bzimage_bytes": 5,
        "rootfs_bytes": 7,
        "target_bytes": 13,
        "target_files": 2,
    }


def test_existing_config_means_incremental_build(tmp_path):
    config = make_config(tmp_path)
    out = config.out_dir / "base"
    out.mkdir(parents=True)
    (out / ".config").write_text("BR2_x86_64=y\n")
    runner = fake_runner(config)
    result = build_variant(config, BASE, runner=runner, log=quiet)
    assert result.clean_build is False
    assert len(runner.calls) == 2


def test_clean_removes_previous_output(tmp_path):
    config = make_config(tmp_path)
    out = config.out_dir / "base"
    out.mkdir(parents=True)
    (out / ".config").write_text("x")
    (out / "stale").write_text("x")
    result = build_variant(config, BASE, clean=True, runner=fake_runner(config), log=quiet)
    assert result.clean_build is True
    assert not (out / "stale").exists()


def test_remove_variant_dir_refuses_escape(tmp_path):
    config = make_config(tmp_path)
    with pytest.raises(BuilderError):
        remove_variant_dir(config, Variant("..", "x_defconfig", "x"))
    assert tmp_path.exists()


def test_failed_configure_skips_build(tmp_path):
    config = make_config(tmp_path)
    runner = fake_runner(config, codes={("base", "configure"): 2})
    result = build_variant(config, BASE, runner=runner, log=quiet)
    assert result.status == "failed"
    assert len(runner.calls) == 1
    assert result.artifacts == {}


def test_failed_build_logs_tail_and_records_row(tmp_path):
    config = make_config(tmp_path)
    messages = []
    runner = fake_runner(config, codes={("base", "build"): 2}, log_text="boom\n")
    result = build_variant(config, BASE, runner=runner, log=messages.append)
    assert result.status == "failed"
    assert any("boom" in message for message in messages)
    row = read_builds(config)[0]
    assert row["status"] == "failed"
    assert row["bzimage_bytes"] == ""


def test_builds_csv_row(tmp_path):
    config = make_config(tmp_path)
    build_variant(config, MUSL, runner=fake_runner(config), log=quiet)
    row = read_builds(config)[0]
    assert row["variant"] == "musl"
    assert row["libc"] == "musl"
    assert row["clean_build"] == "True"
    assert row["status"] == "ok"
    assert row["target_bytes"] == "13"
    assert len(row["defconfig_sha256"]) == 64
    assert float(row["duration_s"]) >= 0


def test_directory_size_counts_regular_files_only(tmp_path):
    (tmp_path / "a").write_bytes(b"123")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b").write_bytes(b"12")
    (tmp_path / "link").symlink_to(tmp_path / "a")
    assert directory_size(tmp_path) == (5, 2)


def test_log_tail(tmp_path):
    path = tmp_path / "log"
    path.write_text("\n".join(str(number) for number in range(100)))
    assert log_tail(path, 3) == "97\n98\n99"
    assert log_tail(tmp_path / "missing") == ""


def test_build_all_stops_at_first_failure(tmp_path):
    config = make_config(tmp_path)
    runner = fake_runner(config, codes={("base", "build"): 2})
    results = build_all(config, runner=runner, log=quiet)
    assert [result.variant for result in results] == ["base"]


def test_build_all_selected_variant(tmp_path):
    config = make_config(tmp_path)
    runner = fake_runner(config)
    results = build_all(config, ["musl"], runner=runner, log=quiet)
    assert [result.variant for result in results] == ["musl"]
    assert all("musl" in str(call[0][1]) for call in runner.calls)
