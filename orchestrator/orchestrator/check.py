import itertools
from collections.abc import Callable

from orchestrator.config import Config, available_factors, available_levels, factor_levels
from orchestrator.experiment import ExperimentError, check_fragments, images_dir
from orchestrator.variants import VariantError, compose_defconfig, uses_factors


def variant_status(config: Config, variant) -> tuple[str, str | None]:
    directory = config.out_dir / variant.name
    if not (directory / ".config").is_file():
        return "niezbudowany", None
    images = images_dir(config, variant.name)
    if not all((images / name).is_file() for name in ("bzImage", "rootfs.ext2")):
        return "skonfigurowany, brak obrazów", None
    if not uses_factors(variant):
        return "zbudowany (gotowy defconfig)", None
    try:
        check_fragments(config, [variant])
    except ExperimentError as error:
        return "zbudowany, niezgodny z fragmentami", str(error)
    return "zbudowany, zgodny", None


def missing_combinations(config: Config) -> tuple[list[tuple[str, ...]], list[tuple[str, list[str]]]]:
    factors = config.factor_names
    levels = factor_levels(config.variants)
    seen: dict[tuple[str, ...], list[str]] = {}
    for variant in config.variants:
        if uses_factors(variant):
            combination = tuple(variant.levels[factor] for factor in factors)
            seen.setdefault(combination, []).append(variant.name)
    expected = list(itertools.product(*(levels[factor] for factor in factors)))
    missing = [combination for combination in expected if combination not in seen]
    duplicated = [
        (", ".join(combination), names) for combination, names in seen.items() if len(names) > 1
    ]
    return missing, duplicated


def run_check(config: Config, log: Callable[[str], None] = print) -> int:
    errors: list[str] = []
    warnings: list[str] = []
    factors_dir = config.external_dir / "factors"
    factors = config.factor_names
    used = factor_levels(config.variants)

    log(f"Czynniki (z {factors_dir}):")
    names = list(factors) + [name for name in available_factors(factors_dir) if name not in factors]
    if not names:
        log("  brak")
    width = max((len(name) for name in names), default=0)
    for factor in names:
        levels = available_levels(factors_dir, factor)
        if factor not in factors:
            log(f"  {factor:<{width}}  {', '.join(levels)} (czynnik nieużywany w wariantach)")
            continue
        described = [level if level in used[factor] else f"{level} (nieużywany)" for level in levels]
        log(f"  {factor:<{width}}  {', '.join(described)}")

    log("")
    log(f"Warianty ({len(config.variants)}):")
    name_width = max(len(variant.name) for variant in config.variants)
    label_width = max(len(variant.label) for variant in config.variants)
    for variant in config.variants:
        if uses_factors(variant):
            try:
                compose_defconfig(config, variant)
            except VariantError as error:
                errors.append(f"wariant {variant.name}: {error}")
        status, problem = variant_status(config, variant)
        if problem:
            errors.append(problem)
        log(f"  {variant.name:<{name_width}}  {variant.label:<{label_width}}  {status}")

    if factors:
        missing, duplicated = missing_combinations(config)
        for combination in missing:
            warnings.append(f"brak wariantu dla kombinacji: {', '.join(combination)}")
        for combination, variants in duplicated:
            warnings.append(f"kombinacja {combination} powtarza się w wariantach: {', '.join(variants)}")

    log("")
    log(f"Scenariusze ({len(config.scenarios)}): {', '.join(scenario.name for scenario in config.scenarios)}")
    plot = config.plot
    if plot.rows or plot.columns:
        layout = f"wiersze: {plot.rows or 'auto'}, kolumny: {plot.columns or 'auto'}"
    else:
        layout = "automatyczny"
    color_by = plot.color_by or (factors[0] if factors else "brak")
    log(f"Wykresy: układ {layout}, kolor według: {color_by}, kreskowanie według: {plot.hatch_by or 'brak'}")

    log("")
    for warning in warnings:
        log(f"Ostrzeżenie: {warning}")
    for error in errors:
        log(f"Błąd: {error}")
    if errors:
        log(f"Konfiguracja zawiera błędy ({len(errors)}).")
        return 1
    suffix = f", ostrzeżenia: {len(warnings)}" if warnings else ""
    log(f"Konfiguracja poprawna{suffix}.")
    return 0
