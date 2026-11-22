import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path

from orchestrator.scenarios import DEFAULT_SCENARIOS, Scenario, ScenarioError, parse_scenarios

REQUIRED_DIRS = ("buildroot", "custom-layer", "dl")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
LEVEL_PATTERN = NAME_PATTERN
LEGACY_FACTORS = ("libc", "opt", "kernel")
VARIANT_KEYS = {"name", "defconfig", "label"}
PLOT_KEYS = {"columns", "rows", "color_by", "hatch_by", "colors", "hatches", "order", "sort_by"}
HATCH_PATTERN = re.compile(r"^[/\\|\-+xoO.*]*$")
INT_KEYS = ("repetitions", "warmup_runs", "smp", "mem_mb", "timeout_s", "port", "seed")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Experiment:
    repetitions: int = 10
    warmup_runs: int = 1
    smp: int = 2
    mem_mb: int = 512
    kvm: bool = True
    timeout_s: int = 120
    port: int = 1234
    seed: int = 42
    cpu_affinity: str = ""
    max_idle_busy_pct: float = 10.0


@dataclass(frozen=True)
class Variant:
    name: str
    defconfig: str | None
    label: str
    factors: tuple[tuple[str, str], ...] = ()

    @property
    def levels(self) -> dict[str, str]:
        return dict(self.factors)


@dataclass(frozen=True)
class PlotOptions:
    columns: int | None = None
    rows: int | None = None
    color_by: str | None = None
    hatch_by: str | None = None
    colors: tuple[tuple[str, str], ...] = ()
    hatches: tuple[tuple[str, str], ...] = ()
    order: tuple[tuple[str, tuple[str, ...]], ...] = ()
    sort_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    project_dir: Path
    buildroot_dir: Path
    external_dir: Path
    dl_dir: Path
    out_dir: Path
    results_dir: Path
    experiment: Experiment
    variants: tuple[Variant, ...]
    scenarios: tuple[Scenario, ...] = DEFAULT_SCENARIOS
    plot: PlotOptions = PlotOptions()

    @property
    def factor_names(self) -> tuple[str, ...]:
        return factor_names(self.variants)


def factor_names(variants) -> tuple[str, ...]:
    names: list[str] = []
    for variant in variants:
        for factor, _ in variant.factors:
            if factor not in names:
                names.append(factor)
    return tuple(names)


def factor_levels(variants) -> dict[str, list[str]]:
    levels: dict[str, list[str]] = {}
    for variant in variants:
        for factor, level in variant.factors:
            known = levels.setdefault(factor, [])
            if level not in known:
                known.append(level)
    return levels


def missing_dirs(path: Path) -> list[str]:
    return [name for name in REQUIRED_DIRS if not (path / name).is_dir()]


def find_project_dir(
    cli_value: str | None,
    default: Path,
    ask: Callable[[str], str] | None = None,
) -> Path:
    candidate = Path(cli_value).expanduser().resolve() if cli_value else default
    while True:
        missing = missing_dirs(candidate)
        if not missing:
            return candidate
        message = f"{candidate}: brak katalogów {', '.join(missing)}"
        if ask is None:
            raise ConfigError(message)
        print(message)
        answer = ask("Podaj ścieżkę do katalogu projektu: ").strip()
        if answer:
            candidate = Path(answer).expanduser().resolve()


def parse_experiment(section: dict) -> Experiment:
    allowed = {item.name for item in fields(Experiment)}
    unknown = set(section) - allowed
    if unknown:
        raise ConfigError(f"nieznane klucze w [experiment]: {', '.join(sorted(unknown))}")
    experiment = Experiment(**section)
    for key in INT_KEYS:
        if type(getattr(experiment, key)) is not int:
            raise ConfigError(f"[experiment] {key} musi być liczbą całkowitą")
    if type(experiment.kvm) is not bool:
        raise ConfigError("[experiment] kvm musi być wartością true lub false")
    if type(experiment.cpu_affinity) is not str:
        raise ConfigError("[experiment] cpu_affinity musi być tekstem")
    if type(experiment.max_idle_busy_pct) not in (int, float):
        raise ConfigError("[experiment] max_idle_busy_pct musi być liczbą")
    if not 0 < experiment.max_idle_busy_pct <= 100:
        raise ConfigError("[experiment] max_idle_busy_pct musi być w zakresie (0, 100]")
    for key in ("repetitions", "smp", "mem_mb", "timeout_s"):
        if getattr(experiment, key) < 1:
            raise ConfigError(f"[experiment] {key} musi być większe od zera")
    if experiment.warmup_runs < 0:
        raise ConfigError("[experiment] warmup_runs nie może być ujemne")
    if not 1024 <= experiment.port <= 65535:
        raise ConfigError("[experiment] port musi być w zakresie 1024-65535")
    return experiment


def available_factors(factors_dir: Path) -> list[str]:
    if not factors_dir.is_dir():
        return []
    return sorted(
        path.name
        for path in factors_dir.iterdir()
        if path.is_dir() and any(path.glob("*.fragment"))
    )


def available_levels(factors_dir: Path, factor: str) -> list[str]:
    directory = factors_dir / factor
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.fragment"))


def _factor_levels(name: str, entry: dict, factors_dir: Path, factors: list[str]) -> dict[str, str]:
    missing = [factor for factor in factors if factor not in entry]
    if missing:
        raise ConfigError(f"wariant {name}: brak czynników {', '.join(missing)}")
    levels = {key: entry[key] for key in entry if key in factors}
    for factor, level in levels.items():
        if not isinstance(level, str) or not LEVEL_PATTERN.match(level):
            raise ConfigError(f"wariant {name}: niepoprawny poziom czynnika {factor}: {level!r}")
        if not (factors_dir / factor / f"{level}.fragment").is_file():
            options = ", ".join(available_levels(factors_dir, factor)) or "brak"
            raise ConfigError(
                f"wariant {name}: nieznany poziom {level!r} czynnika {factor} (dostępne: {options})"
            )
    return levels


def _parse_variants(entries: list, external_dir: Path) -> tuple[Variant, ...]:
    if not entries:
        raise ConfigError("brak sekcji [[variant]]")
    configs_dir = external_dir / "configs"
    factors_dir = external_dir / "factors"
    factors = available_factors(factors_dir)
    variants = []
    seen = set()
    for entry in entries:
        name = entry.get("name")
        if not name or not isinstance(name, str):
            raise ConfigError("każdy [[variant]] wymaga pola name")
        if not NAME_PATTERN.match(name):
            raise ConfigError(f"niepoprawna nazwa wariantu: {name!r}")
        if name in seen:
            raise ConfigError(f"powtórzona nazwa wariantu: {name}")
        seen.add(name)
        unknown = set(entry) - VARIANT_KEYS - set(factors)
        if unknown:
            known = ", ".join(factors) or "brak"
            raise ConfigError(
                f"wariant {name}: nieznane klucze {', '.join(sorted(unknown))} "
                f"(czynniki z katalogu {factors_dir}: {known})"
            )
        defconfig = entry.get("defconfig")
        has_factors = any(factor in entry for factor in factors)
        if defconfig and has_factors:
            raise ConfigError(f"wariant {name}: podaj albo defconfig, albo czynniki, nie oba naraz")
        if defconfig:
            if not (configs_dir / defconfig).is_file():
                raise ConfigError(f"brak defconfigu: {configs_dir / defconfig}")
            variants.append(Variant(name=name, defconfig=defconfig, label=entry.get("label", name)))
            continue
        if not has_factors:
            known = ", ".join(factors) or "brak katalogów czynników"
            raise ConfigError(f"wariant {name}: wymaga pola defconfig albo czynników ({known})")
        base_fragment = factors_dir / "base.fragment"
        if not base_fragment.is_file():
            raise ConfigError(f"brak pliku bazowego: {base_fragment}")
        levels = _factor_levels(name, entry, factors_dir, factors)
        label = entry.get("label") or ", ".join(levels.values())
        variants.append(
            Variant(name=name, defconfig=None, label=label, factors=tuple(levels.items()))
        )
    order = factor_names(variants)
    return tuple(
        Variant(
            name=variant.name,
            defconfig=variant.defconfig,
            label=variant.label,
            factors=tuple((factor, variant.levels[factor]) for factor in order if factor in variant.levels),
        )
        for variant in variants
    )


def _positive_int(section: dict, key: str) -> int | None:
    value = section.get(key)
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise ConfigError(f"[plot] {key} musi być dodatnią liczbą całkowitą")
    return value


def _factor_key(section: dict, key: str, factors: tuple[str, ...]) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if value not in factors:
        raise ConfigError(f"[plot] {key}: nieznany czynnik {value!r} (dostępne: {', '.join(factors) or 'brak'})")
    return value


def _level_map(section: dict, key: str, by_key: str, factor: str | None, levels: dict[str, list[str]]) -> dict:
    value = section.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[plot] {key} musi być tabelą poziom = wartość")
    if value and factor is None:
        raise ConfigError(f"[plot] {key} wymaga wskazania czynnika ({by_key})")
    known = levels.get(factor, [])
    for level, item in value.items():
        if level not in known:
            raise ConfigError(
                f"[plot] {key}: {level!r} nie jest poziomem czynnika {factor} (dostępne: {', '.join(known)})"
            )
        if not isinstance(item, str):
            raise ConfigError(f"[plot] {key}.{level} musi być tekstem")
    return value


def parse_plot(section: dict, variants: tuple[Variant, ...]) -> PlotOptions:
    unknown = set(section) - PLOT_KEYS
    if unknown:
        raise ConfigError(f"nieznane klucze w [plot]: {', '.join(sorted(unknown))}")
    factors = factor_names(variants)
    levels = factor_levels(variants)
    columns = _positive_int(section, "columns")
    rows = _positive_int(section, "rows")
    color_by = _factor_key(section, "color_by", factors)
    hatch_by = _factor_key(section, "hatch_by", factors)
    colors = _level_map(section, "colors", "color_by", color_by or (factors[0] if factors else None), levels)
    if colors:
        from matplotlib.colors import is_color_like

        for level, color in colors.items():
            if not is_color_like(color):
                raise ConfigError(f"[plot] colors.{level}: nieznany kolor {color!r}")
    hatches = _level_map(section, "hatches", "hatch_by", hatch_by, levels)
    for level, pattern in hatches.items():
        if not HATCH_PATTERN.match(pattern):
            raise ConfigError(f"[plot] hatches.{level}: niepoprawny wzór kreskowania {pattern!r}")
    order = section.get("order", {})
    if not isinstance(order, dict):
        raise ConfigError("[plot] order musi być tabelą czynnik = [poziomy]")
    for factor, listed in order.items():
        if factor not in factors:
            raise ConfigError(f"[plot] order: nieznany czynnik {factor!r}")
        if not isinstance(listed, list) or not all(isinstance(level, str) for level in listed):
            raise ConfigError(f"[plot] order.{factor} musi być listą poziomów")
        missing = [level for level in listed if level not in levels.get(factor, [])]
        if missing:
            raise ConfigError(f"[plot] order.{factor}: nieznane poziomy {', '.join(missing)}")
    sort_by = section.get("sort_by", [])
    if not isinstance(sort_by, list) or any(factor not in factors for factor in sort_by):
        raise ConfigError(f"[plot] sort_by musi być listą czynników spośród: {', '.join(factors) or 'brak'}")
    if len(set(sort_by)) != len(sort_by):
        raise ConfigError("[plot] sort_by zawiera powtórzony czynnik")
    return PlotOptions(
        columns=columns,
        rows=rows,
        color_by=color_by,
        hatch_by=hatch_by,
        colors=tuple(colors.items()),
        hatches=tuple(hatches.items()),
        order=tuple((factor, tuple(listed)) for factor, listed in order.items()),
        sort_by=tuple(sort_by),
    )


def load_config(project_dir: Path, config_file: Path | None = None) -> Config:
    config_file = config_file or project_dir / "orchestrator" / "variants.toml"
    try:
        with open(config_file, "rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"brak pliku konfiguracji: {config_file}") from None
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"błąd składni w {config_file}: {error}") from None
    external_dir = project_dir / "custom-layer"
    try:
        scenarios = parse_scenarios(raw.get("scenario", []))
    except ScenarioError as error:
        raise ConfigError(str(error)) from None
    variants = _parse_variants(raw.get("variant", []), external_dir)
    plot = raw.get("plot", {})
    if not isinstance(plot, dict):
        raise ConfigError("[plot] musi być tabelą")
    return Config(
        project_dir=project_dir,
        buildroot_dir=project_dir / "buildroot",
        external_dir=external_dir,
        dl_dir=project_dir / "dl",
        out_dir=project_dir / "out",
        results_dir=project_dir / "orchestrator" / "results",
        experiment=parse_experiment(raw.get("experiment", {})),
        variants=variants,
        scenarios=scenarios,
        plot=parse_plot(plot, variants),
    )
