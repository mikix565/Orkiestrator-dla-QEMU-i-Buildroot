import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch

from orchestrator.config import LEGACY_FACTORS, Config, PlotOptions
from orchestrator.scenarios import DEFAULT_SCENARIOS, HEADLINE_METRIC, HEADLINE_UNIT

EXPERIMENT_ID = re.compile(r"^\d{8}-\d{6}$")
DPI = 150
DEFAULT_COLORS = (
    "tab:blue", "tab:orange", "tab:green", "tab:red", "tab:purple",
    "tab:brown", "tab:pink", "tab:olive", "tab:cyan", "tab:gray",
)
DEFAULT_HATCHES = ("", "//", "\\\\", "xx", "..", "++", "--", "||")
PANEL_HEIGHT = 4.6
MIN_PANEL_WIDTH = 4.0
WIDTH_PER_VARIANT = 0.32
SYSTEM_PANELS = (
    ("boot_host_s", "Czas rozruchu (zegar hosta)", "s", "%.2f"),
    ("boot_uptime_s", "Czas rozruchu (zegar jądra)", "s", "%.2f"),
    ("mem_used_kb", "Zajęta pamięć po starcie", "kB", "%.0f"),
)


class AnalysisError(Exception):
    pass


@dataclass(frozen=True)
class Style:
    colors: tuple[str, ...]
    hatches: tuple[str, ...]
    legend: tuple[tuple[str, str, str], ...]


def campaign_factors(directory: Path, frame: pd.DataFrame) -> tuple[list[str], dict[str, list[str]]]:
    meta = _read_meta(directory)
    entries = meta.get("variants") or []
    if "factors" in meta:
        factors = [factor for factor in meta["factors"] if factor in frame.columns]
        rows = [entry.get("factors") or {} for entry in entries]
    else:
        factors = [factor for factor in LEGACY_FACTORS if factor in frame.columns]
        rows = [{factor: entry[factor] for factor in factors if factor in entry} for entry in entries]
    appearance: dict[str, list[str]] = {factor: [] for factor in factors}
    for row in rows:
        for factor in factors:
            level = row.get(factor)
            if level is not None and level not in appearance[factor]:
                appearance[factor].append(level)
    for factor in factors:
        for level in frame[factor].dropna().astype(str):
            if level not in appearance[factor]:
                appearance[factor].append(level)
    return factors, appearance


def _read_meta(directory: Path) -> dict:
    meta_file = directory / "meta.json"
    if not meta_file.is_file():
        return {}
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AnalysisError(f"nie można odczytać {meta_file}: {error}") from None
    return meta if isinstance(meta, dict) else {}


def level_order(factor: str, plot: PlotOptions, appearance: dict[str, list[str]]) -> list[str]:
    listed = list(dict(plot.order).get(factor, ()))
    return listed + [level for level in appearance.get(factor, []) if level not in listed]


def make_style(
    table: pd.DataFrame,
    factors: list[str],
    plot: PlotOptions,
    appearance: dict[str, list[str]],
) -> Style:
    names = list(table.index)
    color_by = plot.color_by if plot.color_by is not None else (factors[0] if factors else None)
    if color_by not in factors:
        color_by = None
    hatch_by = plot.hatch_by if plot.hatch_by in factors else None
    legend = []
    colors = ["tab:gray"] * len(names)
    if color_by is not None:
        chosen = dict(plot.colors)
        levels = level_order(color_by, plot, appearance)
        palette = {
            level: chosen.get(level, DEFAULT_COLORS[index % len(DEFAULT_COLORS)])
            for index, level in enumerate(levels)
        }
        colors = [palette.get(str(table.at[name, color_by]), "tab:gray") for name in names]
        present = {str(value) for value in table[color_by]}
        legend += [(palette[level], "", f"{color_by}: {level}") for level in levels if level in present]
    hatches = [""] * len(names)
    if hatch_by is not None:
        levels = level_order(hatch_by, plot, appearance)
        if plot.hatches:
            patterns = {level: dict(plot.hatches).get(level, "") for level in levels}
        else:
            patterns = {
                level: DEFAULT_HATCHES[index % len(DEFAULT_HATCHES)] for index, level in enumerate(levels)
            }
        hatches = [patterns.get(str(table.at[name, hatch_by]), "") for name in names]
        present = {str(value) for value in table[hatch_by]}
        legend += [
            ("white", patterns[level], f"{hatch_by}: {level} (kreskowanie)")
            for level in levels
            if level in present and patterns[level]
        ]
    return Style(tuple(colors), tuple(hatches), tuple(legend))


def grid_shape(count: int, plot: PlotOptions) -> tuple[int, int]:
    if plot.columns and plot.rows:
        if plot.columns * plot.rows < count:
            raise AnalysisError(
                f"[plot] rows x columns = {plot.rows} x {plot.columns} to za mało dla {count} wykresów"
            )
        return plot.rows, plot.columns
    if plot.columns:
        return math.ceil(count / plot.columns), plot.columns
    if plot.rows:
        return plot.rows, math.ceil(count / plot.rows)
    columns = math.ceil(math.sqrt(count))
    return math.ceil(count / columns), columns


def make_axes(count: int, variants: int, plot: PlotOptions, extra_height: float):
    rows, columns = grid_shape(count, plot)
    width = max(MIN_PANEL_WIDTH, WIDTH_PER_VARIANT * variants + 1.2) * columns
    figure, axes = plt.subplots(
        rows, columns, figsize=(width, PANEL_HEIGHT * rows + extra_height), squeeze=False
    )
    flat = axes.flatten()
    for axis in flat[count:]:
        axis.set_visible(False)
    return figure, flat


def add_legend(figure, style: Style):
    handles = [
        Patch(facecolor=color, edgecolor="black", hatch=hatch, label=label)
        for color, hatch, label in style.legend
    ]
    if handles:
        figure.legend(
            handles=handles, loc="lower center", ncol=min(len(handles), 6), title="Legenda", frameon=False
        )


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


def compute_averages(frame: pd.DataFrame, panels, factors=None) -> pd.DataFrame:
    columns = [panel[0] for panel in panels]
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise AnalysisError(f"brak kolumn w runs.csv: {', '.join(missing)}")
    grouped = frame.groupby("variant")
    table = grouped[columns].mean()
    table.insert(0, "n", grouped.size())
    info = frame.drop_duplicates("variant").set_index("variant")
    if factors is None:
        factors = [factor for factor in LEGACY_FACTORS if factor in info.columns]
    for factor in reversed(list(factors)):
        if factor in info.columns:
            table.insert(0, factor, info[factor])
    return table


def compute_stats(frame: pd.DataFrame, panels, names: list[str]) -> pd.DataFrame:
    rows = []
    for name in names:
        group = frame[frame["variant"] == name]
        for panel in panels:
            column = panel[0]
            values = group[column].dropna()
            rows.append(
                {
                    "variant": name,
                    "metric": column,
                    "n": len(values),
                    "mean": values.mean(),
                    "std": values.std(ddof=1),
                    "median": values.median(),
                    "q1": values.quantile(0.25),
                    "q3": values.quantile(0.75),
                    "min": values.min(),
                    "max": values.max(),
                }
            )
    return pd.DataFrame(rows)


def order_variants(
    table: pd.DataFrame,
    factors=None,
    plot: PlotOptions = PlotOptions(),
    appearance: dict[str, list[str]] | None = None,
) -> list[str]:
    if factors is None:
        factors = [factor for factor in LEGACY_FACTORS if factor in table.columns]
    keys = [factor for factor in (plot.sort_by or factors) if factor in table.columns]
    if not keys:
        return sorted(table.index)
    if appearance is None:
        appearance = {factor: sorted({str(value) for value in table[factor]}) for factor in keys}
    orders = {factor: level_order(factor, plot, appearance) for factor in keys}

    def key(name):
        positions = []
        for factor in keys:
            level = str(table.at[name, factor])
            order = orders[factor]
            positions.append(order.index(level) if level in order else len(order))
        return (*positions, name)

    return sorted(table.index, key=key)


def plot_averages(
    table: pd.DataFrame,
    panels,
    directory: Path,
    title: str,
    style: Style | None = None,
    plot: PlotOptions = PlotOptions(),
) -> list[Path]:
    style = style or Style(("tab:gray",) * len(table), ("",) * len(table), ())
    figure, flat = make_axes(len(panels), len(table), plot, 0.8)
    positions = list(range(len(table)))
    for axis, (column, label, unit, fmt) in zip(flat, panels):
        bars = axis.bar(positions, table[column], color=list(style.colors), edgecolor="black")
        for bar, hatch in zip(bars, style.hatches):
            bar.set_hatch(hatch)
        axis.bar_label(bars, fmt=fmt, rotation=90, fontsize=7, padding=2)
        axis.set_xticks(positions, list(table.index), rotation=45, ha="right")
        axis.set_title(label)
        axis.set_ylabel(unit)
        axis.margins(y=0.15)
        axis.grid(axis="y", alpha=0.3)
    add_legend(figure, style)
    figure.suptitle(title)
    figure.tight_layout(rect=(0, 0.06, 1, 0.96))
    return save_figure(figure, directory, "averages")


def plot_boxplots(
    frame,
    table,
    panels,
    directory,
    title,
    style: Style | None = None,
    plot: PlotOptions = PlotOptions(),
):
    names = list(table.index)
    style = style or Style(("tab:gray",) * len(names), ("",) * len(names), ())
    positions = list(range(len(names)))
    figure, flat = make_axes(len(panels), len(names), plot, 1.2)
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
        for box, color, hatch in zip(boxes["boxes"], style.colors, style.hatches):
            box.set_facecolor(color)
            box.set_edgecolor("black")
            box.set_hatch(hatch)
        for position, values in zip(positions, groups):
            jitter = generator.uniform(-0.18, 0.18, size=len(values))
            axis.scatter(position + jitter, values, s=9, color="black", alpha=0.65, zorder=3)
        axis.set_xticks(positions, names, rotation=45, ha="right")
        axis.set_title(label)
        axis.set_ylabel(unit)
        axis.grid(axis="y", alpha=0.3)
    add_legend(figure, style)
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
    factors, appearance = campaign_factors(directory, frame)
    table = compute_averages(frame, panels, factors)
    table = table.loc[order_variants(table, factors, config.plot, appearance)]
    style = make_style(table, factors, config.plot, appearance)
    output = directory / "analysis"
    output.mkdir(exist_ok=True)
    csv_path = output / "averages.csv"
    table.to_csv(csv_path, index_label="variant")
    stats = compute_stats(frame, panels, list(table.index))
    stats_path = output / "stats.csv"
    stats.to_csv(stats_path, index=False)
    sizes = sorted({int(value) for value in table["n"]})
    size_text = f"n = {sizes[0]} na wariant" if len(sizes) == 1 else "różna liczba przebiegów"
    title = f"Eksperyment {experiment_id}: średnie z przebiegów pomiarowych ({size_text})"
    paths = plot_averages(table, panels, output, title, style, config.plot)
    box_title = (
        f"Eksperyment {experiment_id}: rozkład pojedynczych przebiegów ({size_text})\n"
        "Pudełko: kwartyle, kreska: mediana, wąsy: do 1,5 IQR, punkty: przebiegi; "
        "oś Y nie zaczyna się od zera"
    )
    paths += plot_boxplots(frame, table, panels, output, box_title, style, config.plot)
    note = f", wykluczono {len(excluded)} przebiegów z zajętym hostem" if len(excluded) else ""
    log(f"Średnie wyniki, eksperyment {experiment_id}{note}")
    log(table.to_string(float_format=lambda value: f"{value:.2f}"))
    for path in [csv_path, stats_path, *paths]:
        log(f"Zapisano: {path}")
    return table
