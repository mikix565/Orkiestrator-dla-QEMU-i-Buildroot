import hashlib

import pytest

from orchestrator.builder import build_variant, configure_command
from orchestrator.config import Config, ConfigError, Experiment, Variant
from orchestrator.variants import (
    VariantError,
    compose_defconfig,
    defconfig_sha256,
    generated_defconfig_path,
    option_name,
    prepare_defconfig,
)

FACTOR = Variant("v", None, "v", libc="musl", opt="Os", kernel="tiny")
LEGACY = Variant("old", "old_defconfig", "old")


def make_config(tmp_path):
    factors = tmp_path / "custom-layer" / "factors"
    factors.mkdir(parents=True)
    (factors / "base.fragment").write_text("A=1\n# B is not set\n")
    for factor, level, text in (
        ("libc", "musl", "C=y\n"),
        ("opt", "Os", "D=y\n"),
        ("kernel", "tiny", 'E="x"\n'),
    ):
        (factors / factor).mkdir()
        (factors / factor / f"{level}.fragment").write_text(text)
    (tmp_path / "custom-layer" / "configs").mkdir()
    return Config(
        project_dir=tmp_path,
        buildroot_dir=tmp_path / "buildroot",
        external_dir=tmp_path / "custom-layer",
        dl_dir=tmp_path / "dl",
        out_dir=tmp_path / "out",
        results_dir=tmp_path / "results",
        experiment=Experiment(),
        variants=(FACTOR, LEGACY),
    )


@pytest.mark.parametrize(
    "line, expected",
    [("A=1", "A"), ("# B is not set", "B"), ("# komentarz", None), ("śmieci", None)],
)
def test_option_name(line, expected):
    assert option_name(line) == expected


def test_compose_order(tmp_path):
    config = make_config(tmp_path)
    assert compose_defconfig(config, FACTOR) == 'A=1\n# B is not set\nC=y\nD=y\nE="x"\n'


def test_compose_rejects_duplicate_option(tmp_path):
    config = make_config(tmp_path)
    (config.external_dir / "factors" / "kernel" / "tiny.fragment").write_text("A=2\n")
    with pytest.raises(VariantError, match="opcja A"):
        compose_defconfig(config, FACTOR)


def test_compose_rejects_set_and_not_set_of_same_option(tmp_path):
    config = make_config(tmp_path)
    (config.external_dir / "factors" / "libc" / "musl.fragment").write_text("B=y\n")
    with pytest.raises(VariantError, match="opcja B"):
        compose_defconfig(config, FACTOR)


def test_compose_missing_fragment(tmp_path):
    config = make_config(tmp_path)
    (config.external_dir / "factors" / "libc" / "musl.fragment").unlink()
    with pytest.raises(VariantError, match="musl.fragment"):
        compose_defconfig(config, FACTOR)


def test_variant_error_is_config_error():
    assert issubclass(VariantError, ConfigError)


def test_compose_rejects_legacy_variant(tmp_path):
    with pytest.raises(VariantError):
        compose_defconfig(make_config(tmp_path), LEGACY)


def test_prepare_defconfig_writes_file(tmp_path):
    config = make_config(tmp_path)
    path = prepare_defconfig(config, FACTOR)
    assert path == config.out_dir / "_defconfigs" / "v_defconfig"
    assert path.read_text() == compose_defconfig(config, FACTOR)


def test_prepare_defconfig_none_for_legacy(tmp_path):
    assert prepare_defconfig(make_config(tmp_path), LEGACY) is None


def test_sha256_of_composed_defconfig(tmp_path):
    config = make_config(tmp_path)
    expected = hashlib.sha256(compose_defconfig(config, FACTOR).encode()).hexdigest()
    assert defconfig_sha256(config, FACTOR) == expected


def test_sha256_legacy_missing_file(tmp_path):
    assert defconfig_sha256(make_config(tmp_path), LEGACY) == "unknown"


def test_sha256_legacy_existing_file(tmp_path):
    config = make_config(tmp_path)
    (config.external_dir / "configs" / "old_defconfig").write_text("X=y\n")
    assert defconfig_sha256(config, LEGACY) == hashlib.sha256(b"X=y\n").hexdigest()


def test_configure_command_for_factor_variant(tmp_path):
    config = make_config(tmp_path)
    assert configure_command(config, FACTOR) == [
        "make",
        f"O={config.out_dir / 'v'}",
        f"BR2_EXTERNAL={config.external_dir}",
        "defconfig",
        f"BR2_DEFCONFIG={generated_defconfig_path(config, FACTOR)}",
    ]


def test_configure_command_for_legacy_variant(tmp_path):
    config = make_config(tmp_path)
    command = configure_command(config, LEGACY)
    assert command[-1] == "old_defconfig"
    assert "defconfig" not in command[:-1]


def test_build_variant_prepares_defconfig_before_configure(tmp_path):
    config = make_config(tmp_path)
    seen = []

    def runner(command, cwd, log_path):
        seen.append((command, generated_defconfig_path(config, FACTOR).is_file()))
        return 0

    result = build_variant(config, FACTOR, runner=runner, log=lambda message: None)
    assert result.status == "ok"
    assert [exists for _, exists in seen] == [True, True]
    assert seen[0][0][-2] == "defconfig"
