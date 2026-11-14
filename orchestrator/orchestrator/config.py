import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path

from orchestrator.scenarios import DEFAULT_SCENARIOS, Scenario, ScenarioError, parse_scenarios

REQUIRED_DIRS = ("buildroot", "custom-layer", "dl")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
LEVEL_PATTERN = NAME_PATTERN
FACTOR_NAMES = ("libc", "opt", "kernel")
VARIANT_KEYS = {"name", "defconfig", "label", *FACTOR_NAMES}
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
    libc: str | None = None
    opt: str | None = None
    kernel: str | None = None


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


def available_levels(factors_dir: Path, factor: str) -> list[str]:
    directory = factors_dir / factor
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.fragment"))


def _factor_levels(name: str, entry: dict, factors_dir: Path) -> dict[str, str]:
    missing = [factor for factor in FACTOR_NAMES if factor not in entry]
    if missing:
        raise ConfigError(f"wariant {name}: brak czynników {', '.join(missing)}")
    levels = {factor: entry[factor] for factor in FACTOR_NAMES}
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
        unknown = set(entry) - VARIANT_KEYS
        if unknown:
            raise ConfigError(f"wariant {name}: nieznane klucze {', '.join(sorted(unknown))}")
        defconfig = entry.get("defconfig")
        has_factors = any(factor in entry for factor in FACTOR_NAMES)
        if defconfig and has_factors:
            raise ConfigError(f"wariant {name}: podaj albo defconfig, albo czynniki, nie oba naraz")
        if defconfig:
            if not (configs_dir / defconfig).is_file():
                raise ConfigError(f"brak defconfigu: {configs_dir / defconfig}")
            variants.append(Variant(name=name, defconfig=defconfig, label=entry.get("label", name)))
            continue
        if not has_factors:
            raise ConfigError(f"wariant {name}: wymaga pola defconfig albo czynników libc, opt i kernel")
        base_fragment = factors_dir / "base.fragment"
        if not base_fragment.is_file():
            raise ConfigError(f"brak pliku bazowego: {base_fragment}")
        levels = _factor_levels(name, entry, factors_dir)
        label = entry.get("label") or f"{levels['libc']}, -{levels['opt']}, {levels['kernel']} kernel"
        variants.append(Variant(name=name, defconfig=None, label=label, **levels))
    return tuple(variants)


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
    return Config(
        project_dir=project_dir,
        buildroot_dir=project_dir / "buildroot",
        external_dir=external_dir,
        dl_dir=project_dir / "dl",
        out_dir=project_dir / "out",
        results_dir=project_dir / "orchestrator" / "results",
        experiment=parse_experiment(raw.get("experiment", {})),
        variants=_parse_variants(raw.get("variant", []), external_dir),
        scenarios=scenarios,
    )
