import io
from types import SimpleNamespace

import pytest

from orchestrator import cli
from orchestrator.analysis import AnalysisError
from orchestrator.config import ConfigError, load_config
from orchestrator.experiment import ExperimentError

EXPERIMENT_ID = "20261027-000000"


def make_project(tmp_path):
    for name in ("buildroot", "dl"):
        (tmp_path / name).mkdir()
    configs = tmp_path / "custom-layer" / "configs"
    configs.mkdir(parents=True)
    (configs / "base_defconfig").write_text("BR2_x86_64=y\n")
    (tmp_path / "orchestrator").mkdir()
    (tmp_path / "orchestrator" / "variants.toml").write_text(
        '[experiment]\nrepetitions = 4\n\n[[variant]]\nname = "base"\ndefconfig = "base_defconfig"\n'
    )
    return tmp_path


def record_calls(monkeypatch):
    calls = []
    built = SimpleNamespace(variant="base", status="ok")
    monkeypatch.setattr(
        cli,
        "build_all",
        lambda config, names, clean: calls.append(("build", names, clean)) or [built],
    )

    def fake_run(config, names, qemu):
        calls.append(("run", config.experiment.repetitions, names, qemu))
        return SimpleNamespace(directory="/x", experiment_id=EXPERIMENT_ID)

    monkeypatch.setattr(cli, "run_experiment", fake_run)
    monkeypatch.setattr(
        cli, "run_analysis", lambda config, experiment_id: calls.append(("analysis", experiment_id))
    )
    return calls


def test_parser_run_options():
    args = cli.build_parser().parse_args(
        ["run", "--variant", "base", "--variant", "musl", "--reps", "3", "--warmup", "0"]
    )
    assert args.variants == ["base", "musl"]
    assert args.reps == 3
    assert args.warmup == 0
    assert args.qemu == "qemu-system-x86_64"


def test_apply_overrides_changes_only_given(tmp_path):
    config = load_config(make_project(tmp_path))
    changed = cli.apply_overrides(config, 9, None)
    assert changed.experiment.repetitions == 9
    assert changed.experiment.warmup_runs == config.experiment.warmup_runs
    assert cli.apply_overrides(config, None, None) is config


def test_apply_overrides_validates(tmp_path):
    config = load_config(make_project(tmp_path))
    with pytest.raises(ConfigError):
        cli.apply_overrides(config, 0, None)


def test_main_run_passes_overrides(tmp_path, monkeypatch, capsys):
    project = make_project(tmp_path)
    captured = {}

    def fake_run(config, variant_names, qemu_binary):
        captured["reps"] = config.experiment.repetitions
        captured["warmup"] = config.experiment.warmup_runs
        captured["variants"] = variant_names
        captured["qemu"] = qemu_binary
        return SimpleNamespace(directory=tmp_path / "results" / "x")

    monkeypatch.setattr(cli, "run_experiment", fake_run)
    code = cli.main(["--project-dir", str(project), "run", "--reps", "7", "--variant", "base"])
    assert code == 0
    assert captured == {
        "reps": 7,
        "warmup": 1,
        "variants": ["base"],
        "qemu": "qemu-system-x86_64",
    }
    assert "Gotowe" in capsys.readouterr().out


def test_main_reports_config_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert cli.main(["--project-dir", str(tmp_path), "run"]) == 1
    assert "brak katalogów" in capsys.readouterr().err


def test_main_reports_runtime_error(tmp_path, monkeypatch, capsys):
    project = make_project(tmp_path)

    def failing(*args):
        raise ExperimentError("brak obrazu")

    monkeypatch.setattr(cli, "run_experiment", failing)
    assert cli.main(["--project-dir", str(project), "run"]) == 1
    assert "brak obrazu" in capsys.readouterr().err


def test_main_interrupt_returns_130(tmp_path, monkeypatch, capsys):
    project = make_project(tmp_path)

    def interrupted(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_experiment", interrupted)
    assert cli.main(["--project-dir", str(project), "run"]) == 130
    assert "Przerwano" in capsys.readouterr().err


def test_parser_build_options():
    args = cli.build_parser().parse_args(["build", "--variant", "base", "--clean"])
    assert args.command == "build"
    assert args.variants == ["base"]
    assert args.clean is True


def test_main_build_failure_returns_1(tmp_path, monkeypatch, capsys):
    project = make_project(tmp_path)
    failed = SimpleNamespace(variant="base", status="failed")
    monkeypatch.setattr(cli, "build_all", lambda config, names, clean: [failed])
    assert cli.main(["--project-dir", str(project), "build"]) == 1
    assert "base" in capsys.readouterr().err


def test_main_build_success(tmp_path, monkeypatch, capsys):
    project = make_project(tmp_path)
    built = SimpleNamespace(variant="base", status="ok")
    monkeypatch.setattr(cli, "build_all", lambda config, names, clean: [built])
    assert cli.main(["--project-dir", str(project), "build", "--clean"]) == 0
    assert "Gotowe" in capsys.readouterr().out


def test_parser_analyze_option():
    args = cli.build_parser().parse_args(["analyze", "--experiment", "20261027-212235"])
    assert args.command == "analyze"
    assert args.experiment == "20261027-212235"


def test_main_analyze_calls_run_analysis(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    captured = []
    monkeypatch.setattr(
        cli, "run_analysis", lambda config, experiment_id: captured.append(experiment_id)
    )
    arguments = ["--project-dir", str(project), "analyze", "--experiment", "20261027-212235"]
    assert cli.main(arguments) == 0
    assert captured == ["20261027-212235"]


def test_main_analyze_defaults_to_latest_experiment(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    captured = []
    monkeypatch.setattr(
        cli, "run_analysis", lambda config, experiment_id: captured.append(experiment_id)
    )
    assert cli.main(["--project-dir", str(project), "analyze"]) == 0
    assert captured == [None]


@pytest.mark.parametrize(
    "arguments",
    [["analyze", "--plots"], ["analyze", "--factorial"], ["all", "--baseline", "base"]],
)
def test_removed_analysis_options_are_rejected(arguments):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(arguments)


def test_parser_all_options():
    args = cli.build_parser().parse_args(
        ["all", "--variant", "base", "--clean", "--reps", "3", "--warmup", "0", "--qemu", "q"]
    )
    assert (args.command, args.variants, args.clean, args.reps, args.warmup, args.qemu) == (
        "all",
        ["base"],
        True,
        3,
        0,
        "q",
    )


def test_main_all_runs_build_run_analysis_in_order(tmp_path, monkeypatch, capsys):
    project = make_project(tmp_path)
    calls = record_calls(monkeypatch)
    arguments = ["--project-dir", str(project), "all", "--variant", "base", "--clean", "--reps", "3"]
    assert cli.main(arguments) == 0
    assert calls == [
        ("build", ["base"], True),
        ("run", 3, ["base"], "qemu-system-x86_64"),
        ("analysis", EXPERIMENT_ID),
    ]
    output = capsys.readouterr().out
    assert "Pomiary zakończone" in output
    assert "Gotowe" in output


def test_main_all_stops_when_build_fails(tmp_path, monkeypatch, capsys):
    project = make_project(tmp_path)
    calls = record_calls(monkeypatch)
    failed = SimpleNamespace(variant="base", status="failed")
    monkeypatch.setattr(cli, "build_all", lambda config, names, clean: [failed])
    assert cli.main(["--project-dir", str(project), "all"]) == 1
    assert calls == []
    assert "base" in capsys.readouterr().err


def test_main_all_analysis_error_reports_after_measurements(tmp_path, monkeypatch, capsys):
    project = make_project(tmp_path)
    record_calls(monkeypatch)

    def failing(config, experiment_id):
        raise AnalysisError("brak danych")

    monkeypatch.setattr(cli, "run_analysis", failing)
    assert cli.main(["--project-dir", str(project), "all"]) == 1
    captured = capsys.readouterr()
    assert "Pomiary zakończone" in captured.out
    assert "brak danych" in captured.err
