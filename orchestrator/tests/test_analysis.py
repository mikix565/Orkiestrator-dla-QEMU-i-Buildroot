import json

import pandas as pd
import pytest

from orchestrator.analysis import (
    AnalysisError,
    compute_averages,
    compute_stats,
    grid_shape,
    latest_experiment,
    make_style,
    measured,
    order_variants,
    panels_for,
    plot_boxplots,
    read_runs,
    run_analysis,
    scenario_pairs,
    split_by_host_load,
)
from orchestrator.config import Config, Experiment, PlotOptions, Variant

SCENARIOS = [
    {"name": "cpu", "test": "cpu"},
    {"name": "mem", "test": "memory"},
    {"name": "mem_read", "test": "memory"},
    {"name": "cpu_1t", "test": "cpu"},
]
VARIANTS = {
    "base": ("glibc", "O2", "default"),
    "musl_os_tiny": ("musl", "Os", "tiny"),
    "os": ("glibc", "Os", "default"),
}


def make_config(tmp_path):
    return Config(
        project_dir=tmp_path,
        buildroot_dir=tmp_path / "buildroot",
        external_dir=tmp_path / "custom-layer",
        dl_dir=tmp_path / "dl",
        out_dir=tmp_path / "out",
        results_dir=tmp_path / "results",
        experiment=Experiment(),
        variants=(Variant("v", "v_defconfig", "v"),),
    )


def write_runs(directory, with_factors=True, with_extra=True, with_meta=True):
    directory.mkdir(parents=True)
    rows = []
    for name, (libc, opt, kernel) in VARIANTS.items():
        for index in range(3):
            row = {
                "run_id": f"{name}_{index}",
                "variant": name,
                "phase": "measure",
                "status": "ok",
                "boot_host_s": 1.0 + index / 10,
                "boot_uptime_s": 0.6 + index / 10,
                "cpu_events_per_s": 1000.0 + index,
                "mem_mib_per_s": 20000.0,
                "mem_total_kb": 500000,
                "mem_available_kb": 480000 + index,
                "host_idle_busy_pct": 2.0,
            }
            if with_extra:
                row["mem_read_mib_per_s"] = 30000.0 + index * 10
                row["cpu_1t_events_per_s"] = 500.0
            if with_factors:
                row.update({"libc": libc, "opt": opt, "kernel": kernel})
            rows.append(row)
    pd.DataFrame(rows).to_csv(directory / "runs.csv", index=False)
    if with_meta:
        (directory / "meta.json").write_text(json.dumps({"scenarios": SCENARIOS}))


def test_compute_averages_means_and_columns():
    frame = pd.DataFrame(
        {
            "variant": ["a", "a", "b", "b"],
            "libc": ["glibc", "glibc", "musl", "musl"],
            "opt": ["O2"] * 4,
            "kernel": ["default"] * 4,
            "cpu_events_per_s": [10.0, 20.0, 30.0, 50.0],
            "boot_host_s": [1.0, 1.2, 0.8, 0.8],
        }
    )
    panels = [("cpu_events_per_s", "t", "u", "%.0f"), ("boot_host_s", "t", "s", "%.2f")]
    table = compute_averages(frame, panels)
    assert list(table.columns) == ["libc", "opt", "kernel", "n", "cpu_events_per_s", "boot_host_s"]
    assert table.loc["a", "cpu_events_per_s"] == pytest.approx(15.0)
    assert table.loc["b", "cpu_events_per_s"] == pytest.approx(40.0)
    assert table.loc["a", "boot_host_s"] == pytest.approx(1.1)
    assert list(table["n"]) == [2, 2]
    assert table.loc["b", "libc"] == "musl"


def test_order_groups_by_kernel_then_libc_then_opt():
    table = pd.DataFrame(
        {
            "libc": ["glibc", "musl", "glibc", "musl", "glibc"],
            "opt": ["O2", "Os", "Os", "O2", "O3"],
            "kernel": ["default", "tiny", "tiny", "default", "default"],
        },
        index=["base", "musl_os_tiny", "os_tiny", "musl", "o3"],
    )
    plot = PlotOptions(sort_by=("kernel", "libc", "opt"), order=(("opt", ("O2", "O3", "Os")),))
    assert order_variants(table, plot=plot) == ["base", "o3", "musl", "os_tiny", "musl_os_tiny"]


def test_order_default_follows_factor_and_level_appearance():
    table = pd.DataFrame(
        {"libc": ["musl", "glibc", "glibc"], "opt": ["O2", "Os", "O2"]},
        index=["m", "gs", "g"],
    )
    appearance = {"libc": ["glibc", "musl"], "opt": ["O2", "Os"]}
    assert order_variants(table, ["libc", "opt"], appearance=appearance) == ["g", "gs", "m"]


@pytest.mark.parametrize(
    "count, plot, expected",
    [
        (7, PlotOptions(), (3, 3)),
        (16, PlotOptions(), (4, 4)),
        (5, PlotOptions(), (2, 3)),
        (7, PlotOptions(columns=4), (2, 4)),
        (7, PlotOptions(rows=1), (1, 7)),
        (6, PlotOptions(rows=2, columns=3), (2, 3)),
    ],
)
def test_grid_shape(count, plot, expected):
    assert grid_shape(count, plot) == expected


def test_grid_shape_too_small_raises():
    with pytest.raises(AnalysisError):
        grid_shape(7, PlotOptions(rows=2, columns=3))


def test_style_uses_configured_colors_and_hatches():
    table = pd.DataFrame(
        {"libc": ["glibc", "musl"], "kernel": ["default", "tiny"]}, index=["a", "b"]
    )
    plot = PlotOptions(
        color_by="libc",
        hatch_by="kernel",
        colors=(("musl", "red"),),
        hatches=(("tiny", "//"),),
    )
    appearance = {"libc": ["glibc", "musl"], "kernel": ["default", "tiny"]}
    style = make_style(table, ["libc", "kernel"], plot, appearance)
    assert style.colors == ("tab:blue", "red")
    assert style.hatches == ("", "//")
    assert [label for _, _, label in style.legend] == [
        "libc: glibc",
        "libc: musl",
        "kernel: tiny (kreskowanie)",
    ]


def test_style_defaults_to_first_factor_without_hatching():
    table = pd.DataFrame({"fs": ["a", "b"], "libc": ["x", "x"]}, index=["v1", "v2"])
    style = make_style(table, ["fs", "libc"], PlotOptions(), {"fs": ["a", "b"], "libc": ["x"]})
    assert style.colors == ("tab:blue", "tab:orange")
    assert style.hatches == ("", "")


def test_style_ignores_factor_missing_in_campaign():
    table = pd.DataFrame({"libc": ["glibc"]}, index=["a"])
    style = make_style(table, ["libc"], PlotOptions(color_by="fs"), {"libc": ["glibc"]})
    assert style.colors == ("tab:gray",)


def test_order_without_factors_is_alphabetical():
    table = pd.DataFrame({"n": [1, 1]}, index=["b", "a"])
    assert order_variants(table) == ["a", "b"]


def test_panels_follow_scenarios():
    panels = panels_for([("cpu", "cpu"), ("mem_read", "memory")])
    assert [panel[0] for panel in panels] == [
        "cpu_events_per_s",
        "mem_read_mib_per_s",
        "boot_host_s",
        "boot_uptime_s",
        "mem_used_kb",
    ]
    assert [panel[2] for panel in panels] == ["zdarzeń/s", "MiB/s", "s", "s", "kB"]


def test_scenario_pairs_default_without_meta(tmp_path):
    assert scenario_pairs(tmp_path) == [("cpu", "cpu"), ("mem", "memory")]


def test_scenario_pairs_from_meta(tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps({"scenarios": SCENARIOS}))
    assert scenario_pairs(tmp_path) == [
        ("cpu", "cpu"),
        ("mem", "memory"),
        ("mem_read", "memory"),
        ("cpu_1t", "cpu"),
    ]


def test_scenario_pairs_malformed_meta(tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps({"scenarios": [{"name": "x"}]}))
    with pytest.raises(AnalysisError, match="niepoprawny opis"):
        scenario_pairs(tmp_path)
    (tmp_path / "meta.json").write_text(
        json.dumps({"scenarios": [{"name": "x", "test": "disk"}]})
    )
    with pytest.raises(AnalysisError, match="nieznany test"):
        scenario_pairs(tmp_path)


def test_run_analysis_writes_files(tmp_path):
    config = make_config(tmp_path)
    directory = config.results_dir / "20261027-212235"
    write_runs(directory)
    messages = []
    table = run_analysis(config, "20261027-212235", log=messages.append)
    output = directory / "analysis"
    for name in (
        "averages.csv",
        "stats.csv",
        "averages.png",
        "averages.svg",
        "boxplots.png",
        "boxplots.svg",
    ):
        assert (output / name).stat().st_size > 0
    saved = pd.read_csv(output / "averages.csv")
    assert list(saved["variant"]) == ["base", "os", "musl_os_tiny"]
    assert {"mem_read_mib_per_s", "cpu_1t_events_per_s", "boot_host_s", "mem_used_kb", "n"} <= set(
        saved.columns
    )
    base = saved[saved["variant"] == "base"].iloc[0]
    assert base["cpu_events_per_s"] == pytest.approx(1001.0)
    assert base["mem_read_mib_per_s"] == pytest.approx(30010.0)
    assert base["boot_host_s"] == pytest.approx(1.1)
    assert base["mem_used_kb"] == pytest.approx(19999.0)
    assert list(table.index) == ["base", "os", "musl_os_tiny"]
    assert any("Zapisano" in message for message in messages)


def test_compute_stats_values():
    frame = pd.DataFrame({"variant": ["a"] * 4 + ["b"], "x": [1.0, 2.0, 3.0, 4.0, 7.0]})
    stats = compute_stats(frame, [("x", "t", "u", "%.0f")], ["b", "a"])
    assert list(stats["variant"]) == ["b", "a"]
    assert list(stats["metric"]) == ["x", "x"]
    a = stats[stats["variant"] == "a"].iloc[0]
    assert a["n"] == 4
    assert a["mean"] == pytest.approx(2.5)
    assert a["std"] == pytest.approx(1.2909944487)
    assert a["median"] == pytest.approx(2.5)
    assert a["q1"] == pytest.approx(1.75)
    assert a["q3"] == pytest.approx(3.25)
    assert a["min"] == 1.0
    assert a["max"] == 4.0
    b = stats[stats["variant"] == "b"].iloc[0]
    assert b["n"] == 1
    assert pd.isna(b["std"])
    assert b["median"] == 7.0


def test_run_analysis_writes_stats(tmp_path):
    config = make_config(tmp_path)
    directory = config.results_dir / "20261027-212235"
    write_runs(directory)
    run_analysis(config, "20261027-212235", log=lambda message: None)
    stats = pd.read_csv(directory / "analysis" / "stats.csv")
    assert list(stats.columns) == [
        "variant", "metric", "n", "mean", "std", "median", "q1", "q3", "min", "max"
    ]
    assert len(stats) == 3 * 7
    assert list(stats["variant"].unique()) == ["base", "os", "musl_os_tiny"]
    row = stats[(stats["variant"] == "base") & (stats["metric"] == "boot_host_s")].iloc[0]
    assert row["n"] == 3
    assert row["mean"] == pytest.approx(1.1)
    assert row["std"] == pytest.approx(0.1)
    assert row["median"] == pytest.approx(1.1)
    assert row["q1"] == pytest.approx(1.05)
    assert row["q3"] == pytest.approx(1.15)
    assert row["min"] == pytest.approx(1.0)
    assert row["max"] == pytest.approx(1.2)


def test_run_analysis_excludes_noisy_runs(tmp_path):
    config = make_config(tmp_path)
    directory = config.results_dir / "20261027-212235"
    write_runs(directory)
    frame = pd.read_csv(directory / "runs.csv")
    frame.loc[frame["run_id"] == "base_0", "host_idle_busy_pct"] = 50.0
    frame.to_csv(directory / "runs.csv", index=False)
    table = run_analysis(config, log=lambda message: None)
    assert table.loc["base", "n"] == 2
    assert table.loc["base", "boot_host_s"] == pytest.approx(1.15)
    assert table.loc["os", "n"] == 3


def test_run_analysis_missing_scenario_column_raises(tmp_path):
    config = make_config(tmp_path)
    write_runs(config.results_dir / "20261027-212235", with_extra=False)
    with pytest.raises(AnalysisError, match="brak kolumn"):
        run_analysis(config, log=lambda message: None)


def test_run_analysis_rejects_unsafe_id(tmp_path):
    with pytest.raises(AnalysisError):
        run_analysis(make_config(tmp_path), "../x")


def test_run_analysis_without_factor_columns(tmp_path):
    config = make_config(tmp_path)
    write_runs(config.results_dir / "20261027-212235", with_factors=False, with_meta=False)
    table = run_analysis(config, log=lambda message: None)
    assert list(table.index) == ["base", "musl_os_tiny", "os"]
    assert "libc" not in table.columns


def load_frame(values):
    return pd.DataFrame({"variant": "base", "host_idle_busy_pct": values})


def test_latest_experiment(tmp_path):
    for name in ("20261024-100000", "20261024-123023"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "runs.csv").write_text("x\n")
    (tmp_path / "20261025-000000").mkdir()
    (tmp_path / "builds").mkdir()
    assert latest_experiment(tmp_path) == "20261024-123023"


def test_read_runs_missing_file(tmp_path):
    with pytest.raises(AnalysisError):
        read_runs(tmp_path / "missing.csv")


def test_measured_filters_and_adds_memory_used(tmp_path):
    write_runs(tmp_path / "e")
    frame = pd.read_csv(tmp_path / "e" / "runs.csv")
    frame.loc[0, "status"] = "timeout"
    frame.loc[1, "phase"] = "warmup"
    result = measured(frame)
    assert len(result) == len(frame) - 2
    assert set(result["status"]) == {"ok"}
    assert set(result["phase"]) == {"measure"}
    assert (result["mem_used_kb"] == result["mem_total_kb"] - result["mem_available_kb"]).all()


def test_measured_without_rows_raises():
    frame = pd.DataFrame(
        [{"variant": "base", "phase": "warmup", "status": "ok", "mem_total_kb": 1, "mem_available_kb": 1}]
    )
    with pytest.raises(AnalysisError):
        measured(frame)


def test_split_by_host_load_separates_noisy():
    kept, excluded = split_by_host_load(load_frame([2.0, 5.0, 15.0, float("nan")]), 10.0)
    assert list(kept["host_idle_busy_pct"].fillna(-1)) == [2.0, 5.0, -1.0]
    assert list(excluded["host_idle_busy_pct"]) == [15.0]


def test_split_by_host_load_threshold_is_inclusive():
    kept, excluded = split_by_host_load(load_frame([10.0, 10.01]), 10.0)
    assert len(kept) == 1
    assert len(excluded) == 1


def test_split_by_host_load_all_noisy_raises():
    with pytest.raises(AnalysisError):
        split_by_host_load(load_frame([20.0, 30.0]), 10.0)


def test_split_by_host_load_without_column_keeps_all():
    frame = pd.DataFrame({"variant": ["base", "base"]})
    kept, excluded = split_by_host_load(frame, 10.0)
    assert len(kept) == 2
    assert excluded.empty


def test_plot_boxplots_marks_every_run(tmp_path, monkeypatch):
    directory = tmp_path / "20261027-212235"
    write_runs(directory)
    frame = measured(read_runs(directory / "runs.csv"))
    panels = panels_for(scenario_pairs(directory))
    table = compute_averages(frame, panels)
    table = table.loc[order_variants(table)]
    figures = []
    monkeypatch.setattr(
        "orchestrator.analysis.save_figure",
        lambda figure, target, name: figures.append(figure) or [],
    )
    plot_boxplots(frame, table, panels, directory, "test")
    axes = [axis for axis in figures[0].axes if axis.get_visible()]
    assert len(axes) == len(panels)
    for axis in axes:
        assert sum(len(collection.get_offsets()) for collection in axis.collections) == len(frame)
