import hashlib
from pathlib import Path

from orchestrator.config import Config, ConfigError, Variant


class VariantError(ConfigError):
    pass


def uses_factors(variant: Variant) -> bool:
    return variant.defconfig is None


def option_name(line: str) -> str | None:
    if line.startswith("# ") and line.endswith(" is not set"):
        return line[2:-len(" is not set")]
    if line.startswith("#"):
        return None
    if "=" in line:
        return line.split("=", 1)[0]
    return None


def factor_fragments(config: Config, variant: Variant) -> list[tuple[str, str, Path]]:
    factors_dir = config.external_dir / "factors"
    return [
        (factor, level, factors_dir / factor / f"{level}.fragment")
        for factor, level in variant.factors
    ]


def fragment_options(text: str) -> list[tuple[str, str]]:
    options = []
    for line in text.splitlines():
        line = line.rstrip()
        name = option_name(line) if line.strip() else None
        if name is not None:
            options.append((name, line))
    return options


def compose_defconfig(config: Config, variant: Variant) -> str:
    if not uses_factors(variant):
        raise VariantError(f"wariant {variant.name} używa gotowego defconfigu, nie czynników")
    factors_dir = config.external_dir / "factors"
    parts = [factors_dir / "base.fragment"]
    parts += [path for _, _, path in factor_fragments(config, variant)]
    seen: dict[str, Path] = {}
    lines: list[str] = []
    for part in parts:
        try:
            text = part.read_text(encoding="utf-8")
        except OSError as error:
            raise VariantError(f"nie można odczytać {part}: {error}") from None
        for line in text.splitlines():
            line = line.rstrip()
            if not line.strip():
                continue
            name = option_name(line)
            if name is not None:
                if name in seen:
                    raise VariantError(
                        f"opcja {name} ustawiona w {seen[name].relative_to(factors_dir)} "
                        f"i w {part.relative_to(factors_dir)}"
                    )
                seen[name] = part
            lines.append(line)
    return "\n".join(lines) + "\n"


def generated_defconfig_path(config: Config, variant: Variant) -> Path:
    return config.out_dir / "_defconfigs" / f"{variant.name}_defconfig"


def prepare_defconfig(config: Config, variant: Variant) -> Path | None:
    if not uses_factors(variant):
        return None
    text = compose_defconfig(config, variant)
    path = generated_defconfig_path(config, variant)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def defconfig_sha256(config: Config, variant: Variant) -> str:
    if uses_factors(variant):
        return hashlib.sha256(compose_defconfig(config, variant).encode("utf-8")).hexdigest()
    try:
        return hashlib.sha256(
            (config.external_dir / "configs" / variant.defconfig).read_bytes()
        ).hexdigest()
    except OSError:
        return "unknown"
