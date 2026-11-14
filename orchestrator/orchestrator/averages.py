import json
import math
import re
from collections.abc import Callable
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch

from orchestrator.config import Config
from orchestrator.scenarios import DEFAULT_SCENARIOS, HEADLINE_METRIC, HEADLINE_UNIT

EXPERIMENT_ID = re.compile(r"^\d{8}-\d{6}$")
DPI = 150
FACTOR_COLUMNS = ("libc", "opt", "kernel")
LIBC_COLORS = {"glibc": "tab:blue", "musl": "tab:orange"}
OPT_ORDER = {"O2": 0, "O3": 1, "Os": 2}
KERNEL_ORDER = {"default": 0, "tiny": 1}
PANELS_PER_ROW = 4
SYSTEM_PANELS = (
    ("boot_host_s", "Czas rozruchu (zegar hosta)", "s", "%.2f"),
    ("boot_uptime_s", "Czas rozruchu (zegar jądra)", "s", "%.2f"),
    ("mem_used_kb", "Zajęta pamięć po starcie", "kB", "%.0f"),
)


class AnalysisError(Exception):
    pass


def latest_experiment(results_dir: Path) -> str:
    if not results_dir.is_dir():
        raise AnalysisError(f"brak katalogu wyników: {results_dir}")
    names = sorted(
        path.name
        for path in results_dir.iterdir()
        if path.is_dir() and EXPERIMENT_ID.match(path.name) and (path / "runs.csv").is_file()
    )
    if not names:
        raise AnalysisError(f"brak eksperymentów w {results_dir}")
    return names[-1]


def read_runs(runs_file: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(runs_file)
    except FileNotFoundError:
        raise AnalysisError(f"brak pliku {runs_file}") from None
    except pd.errors.EmptyDataError:
        raise AnalysisError(f"pusty plik {runs_file}") from None


def measured(frame: pd.DataFrame) -> pd.DataFrame:
    selected = frame[(frame["phase"] == "measure") & (frame["status"] == "ok")].copy()
    if selected.empty:
        raise AnalysisError("brak udanych przebiegów pomiarowych")
    selected["mem_used_kb"] = selected["mem_total_kb"] - selected["mem_available_kb"]
    return selected


def split_by_host_load(
    frame: pd.DataFrame, max_idle_busy: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if "host_idle_busy_pct" not in frame.columns:
        return frame, frame.iloc[0:0]
    noisy = frame["host_idle_busy_pct"] > max_idle_busy
    kept = frame[~noisy]
    if kept.empty:
        raise AnalysisError(
            f"wszystkie przebiegi odrzucone (host zajęty > {max_idle_busy:g}% przed startem); "
            "powtórz pomiar na cichym hoście"
        )
    return kept, frame[noisy]


def save_figure(figure, directory: Path, name: str) -> list[Path]:
    paths = []
    for extension in ("png", "svg"):
        path = directory / f"{name}.{extension}"
        figure.savefig(path, dpi=DPI, bbox_inches="tight")
        paths.append(path)
    plt.close(figure)
    return paths


def scenario_pairs(directory: Path) -> list[tuple[str, str]]:
    default = [(scenario.name, scenario.test) for scenario in DEFAULT_SCENARIOS]
    meta_file = directory / "meta.json"
    if not meta_file.is_file():
        return default
    try:
        entries = json.loads(meta_file.read_text(encoding="utf-8")).get("scenarios")
    except (OSError, ValueError, AttributeError) as error:
        raise AnalysisError(f"nie można odczytać {meta_file}: {error}") from None
    if not entries:
        return default
    try:
        pairs = [(entry["name"], entry["test"]) for entry in entries]
    except (KeyError, TypeError) as error:
        raise AnalysisError(f"niepoprawny opis scenariuszy w {meta_file}: {error!r}") from None
    for name, test in pairs:
        if test not in HEADLINE_METRIC:
            raise AnalysisError(f"scenariusz {name}: nieznany test {test!r} w {meta_file}")
    return pairs


def panels_for(pairs: list[tuple[str, str]]) -> list[tuple[str, str, str, str]]:
    panels = [
        (
            f"{name}_{HEADLINE_METRIC[test]}",
            f"Scenariusz {name} ({test})",
            HEADLINE_UNIT[test],
            "%.0f",
        )
        for name, test in pairs
    ]
    return panels + list(SYSTEM_PANELS)


def compute_averages(frame: pd.DataFrame, panels) -> pd.DataFrame:
    columns = [panel[0] for panel in panels]
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise AnalysisError(f"brak kolumn w runs.csv: {', '.join(missing)}")
    grouped = frame.groupby("variant")
    table = grouped[columns].mean()
    table.insert(0, "n", grouped.size())
    info = frame.drop_duplicates("variant").set_index("variant")
    for factor in reversed(FACTOR_COLUMNS):
        if factor in info.columns:
            table.insert(0, factor, info[factor])
    return table


def order_variants(table: pd.DataFrame) -> list[str]:
    if all(factor in table.columns for factor in FACTOR_COLUMNS):
        return sorted(
            table.index,
            key=lambda name: (
                KERNEL_ORDER.get(table.at[name, "kernel"], 9),
                str(table.at[name, "libc"]),
                OPT_ORDER.get(table.at[name, "opt"], 9),
                name,
            ),
        )
    return sorted(table.index)


def plot_averages(table: pd.DataFrame, panels, directory: Path, title: str) -> list[Path]:
    rows = math.ceil(len(panels) / PANELS_PER_ROW)
    figure, axes = plt.subplots(
        rows, PANELS_PER_ROW, figsize=(19, 4.6 * rows + 0.8), squeeze=False
    )
    flat = axes.flatten()
    has_factors = all(factor in table.columns for factor in FACTOR_COLUMNS)
    colors = [
        LIBC_COLORS.get(table.at[name, "libc"], "tab:gray") if has_factors else "tab:gray"
        for name in table.index
    ]
    hatches = [
        "//" if has_factors and table.at[name, "kernel"] == "tiny" else ""
        for name in table.index
    ]
    positions = list(range(len(table)))
    for axis, (column, label, unit, fmt) in zip(flat, panels):
        bars = axis.bar(positions, table[column], color=colors, edgecolor="black")
        for bar, hatch in zip(bars, hatches):
            bar.set_hatch(hatch)
        axis.bar_label(bars, fmt=fmt, rotation=90, fontsize=7, padding=2)
        axis.set_xticks(positions, list(table.index), rotation=45, ha="right")
        axis.set_title(label)
        axis.set_ylabel(unit)
        axis.margins(y=0.15)
        axis.grid(axis="y", alpha=0.3)
    for axis in flat[len(panels):]:
        axis.set_visible(False)
    handles = []
    if has_factors:
        handles += [
            Patch(facecolor=color, edgecolor="black", label=f"libc: {libc}")
            for libc, color in LIBC_COLORS.items()
            if libc in set(table["libc"])
        ]
        if "tiny" in set(table["kernel"]):
            handles.append(
                Patch(facecolor="white", edgecolor="black", hatch="//", label="jądro tiny (kreskowanie)")
            )
    if handles:
        figure.legend(
            handles=handles, loc="lower center", ncol=len(handles), title="Legenda", frameon=False
        )
    figure.suptitle(title)
    figure.tight_layout(rect=(0, 0.06, 1, 0.96))
    return save_figure(figure, directory, "averages")


def add_legend(figure, table, has_factors):
    handles = []
    if has_factors:
        handles += [
            Patch(facecolor=color, edgecolor="black", label=f"libc: {libc}")
            for libc, color in LIBC_COLORS.items()
            if libc in set(table["libc"])
        ]
        if "tiny" in set(table["kernel"]):
            handles.append(
                Patch(facecolor="white", edgecolor="black", hatch="//", label="jądro tiny (kreskowanie)")
            )
    if handles:
        figure.legend(
            handles=handles, loc="lower center", ncol=len(handles), title="Legenda", frameon=False
        )


def plot_boxplots(frame, table, panels, directory, title):
    names = list(table.index)
    positions = list(range(len(names)))
    rows = math.ceil(len(panels) / PANELS_PER_ROW)
    figure, axes = plt.subplots(
        rows, PANELS_PER_ROW, figsize=(19, 4.6 * rows + 1.2), squeeze=False
    )
    flat = axes.flatten()
    has_factors = all(factor in table.columns for factor in FACTOR_COLUMNS)
    generator = np.random.default_rng(0)
    for axis, (column, label, unit, _) in zip(flat, panels):
        groups = [
            frame.loc[frame["variant"] == name, column].dropna().to_numpy() for name in names
        ]
        boxes = axis.boxplot(
            groups,
            positions=positions,
            widths=0.6,
            patch_artist=True,
            showfliers=False,
            medianprops={"color": "black", "linewidth": 1.5},
        )
        for box, name in zip(boxes["boxes"], names):
            color = LIBC_COLORS.get(table.at[name, "libc"], "tab:gray") if has_factors else "tab:gray"
            box.set_facecolor(color)
            box.set_edgecolor("black")
            if has_factors and table.at[name, "kernel"] == "tiny":
                box.set_hatch("//")
        for position, values in zip(positions, groups):
            jitter = generator.uniform(-0.18, 0.18, size=len(values))
            axis.scatter(position + jitter, values, s=9, color="black", alpha=0.65, zorder=3)
        axis.set_xticks(positions, names, rotation=45, ha="right")
        axis.set_title(label)
        axis.set_ylabel(unit)
        axis.grid(axis="y", alpha=0.3)
    for axis in flat[len(panels):]:
        axis.set_visible(False)
    add_legend(figure, table, has_factors)
    figure.suptitle(title)
    figure.tight_layout(rect=(0, 0.06, 1, 0.93))
    return save_figure(figure, directory, "boxplots")


def run_averages(
    config: Config,
    experiment_id: str | None = None,
    log: Callable[[str], None] = print,
) -> pd.DataFrame:
    if experiment_id is not None and not EXPERIMENT_ID.match(experiment_id):
        raise AnalysisError(f"niepoprawny identyfikator eksperymentu: {experiment_id!r}")
    experiment_id = experiment_id or latest_experiment(config.results_dir)
    directory = config.results_dir / experiment_id
    frame, excluded = split_by_host_load(
        measured(read_runs(directory / "runs.csv")), config.experiment.max_idle_busy_pct
    )
    panels = panels_for(scenario_pairs(directory))
    table = compute_averages(frame, panels)
    table = table.loc[order_variants(table)]
    output = directory / "analysis"
    output.mkdir(exist_ok=True)
    csv_path = output / "averages.csv"
    table.to_csv(csv_path, index_label="variant")
    sizes = sorted({int(value) for value in table["n"]})
    size_text = f"n = {sizes[0]} na wariant" if len(sizes) == 1 else "różna liczba przebiegów"
    title = f"Eksperyment {experiment_id}: średnie z przebiegów pomiarowych ({size_text})"
    paths = plot_averages(table, panels, output, title)
    box_title = (
        f"Eksperyment {experiment_id}: rozkład pojedynczych przebiegów ({size_text})\n"
        "Pudełko: kwartyle, kreska: mediana, wąsy: do 1,5 IQR, punkty: przebiegi; "
        "oś Y nie zaczyna się od zera"
    )
    paths += plot_boxplots(frame, table, panels, output, box_title)
    note = f", wykluczono {len(excluded)} przebiegów z zajętym hostem" if len(excluded) else ""
    log(f"Średnie wyniki, eksperyment {experiment_id}{note}")
    log(table.to_string(float_format=lambda value: f"{value:.2f}"))
    for path in [csv_path, *paths]:
        log(f"Zapisano: {path}")
    return table
