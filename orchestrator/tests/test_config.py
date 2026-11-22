from pathlib import Path

import pytest

from orchestrator.config import (
    ConfigError,
    Experiment,
    find_project_dir,
    load_config,
    missing_dirs,
    parse_experiment,
)

VARIANT = '[[variant]]\nname = "base"\ndefconfig = "base_defconfig"\n'


def make_project(root, toml=VARIANT):
    for name in ("buildroot", "dl"):
        (root / name).mkdir()
    configs = root / "custom-layer" / "configs"
    configs.mkdir(parents=True)
    (configs / "base_defconfig").write_text("BR2_x86_64=y\n")
    (root / "orchestrator").mkdir()
    (root / "orchestrator" / "variants.toml").write_text(toml)
    return root


def test_missing_dirs(tmp_path):
    assert missing_dirs(tmp_path) == ["buildroot", "custom-layer", "dl"]


def test_find_project_dir_uses_cli_value(tmp_path):
    project = make_project(tmp_path)
    assert find_project_dir(str(project), Path("/nonexistent")) == project.resolve()


def test_find_project_dir_falls_back_to_default(tmp_path):
    project = make_project(tmp_path)
    assert find_project_dir(None, project) == project


def test_find_project_dir_raises_without_ask(tmp_path):
    with pytest.raises(ConfigError):
        find_project_dir(str(tmp_path), tmp_path)


def test_find_project_dir_asks_until_valid(tmp_path):
    valid = tmp_path / "valid"
    valid.mkdir()
    make_project(valid)
    answers = iter(["", str(valid)])
    result = find_project_dir(
        str(tmp_path / "wrong"), tmp_path, ask=lambda prompt: next(answers)
    )
    assert result == valid.resolve()


def test_parse_experiment_defaults():
    assert parse_experiment({}) == Experiment()


def test_parse_experiment_rejects_unknown_key():
    with pytest.raises(ConfigError):
        parse_experiment({"nieznany": 1})


@pytest.mark.parametrize("section", [{"smp": "2"}, {"kvm": 1}, {"cpu_affinity": 3}])
def test_parse_experiment_rejects_wrong_types(section):
    with pytest.raises(ConfigError):
        parse_experiment(section)


@pytest.mark.parametrize(
    "section",
    [
        {"repetitions": 0},
        {"warmup_runs": -1},
        {"port": 80},
        {"max_idle_busy_pct": 0},
        {"max_idle_busy_pct": 101},
        {"max_idle_busy_pct": "x"},
        {"max_idle_busy_pct": True},
    ],
)
def test_parse_experiment_rejects_bad_values(section):
    with pytest.raises(ConfigError):
        parse_experiment(section)


def test_parse_experiment_accepts_integer_threshold():
    assert parse_experiment({"max_idle_busy_pct": 10}).max_idle_busy_pct == 10


def test_load_config_reads_variants_and_paths(tmp_path):
    config = load_config(make_project(tmp_path))
    assert [variant.name for variant in config.variants] == ["base"]
    assert config.variants[0].label == "base"
    assert config.buildroot_dir == tmp_path / "buildroot"
    assert config.external_dir == tmp_path / "custom-layer"
    assert config.out_dir == tmp_path / "out"
    assert config.results_dir == tmp_path / "orchestrator" / "results"


def test_load_config_missing_file(tmp_path):
    project = make_project(tmp_path)
    (project / "orchestrator" / "variants.toml").unlink()
    with pytest.raises(ConfigError):
        load_config(project)


def test_load_config_bad_toml(tmp_path):
    with pytest.raises(ConfigError):
        load_config(make_project(tmp_path, "to nie jest toml"))


@pytest.mark.parametrize(
    "toml",
    [
        VARIANT + VARIANT,
        '[[variant]]\nname = "a b"\ndefconfig = "base_defconfig"\n',
        '[[variant]]\nname = "x"\ndefconfig = "nope_defconfig"\n',
    ],
)
def test_load_config_rejects_bad_variants(tmp_path, toml):
    with pytest.raises(ConfigError):
        load_config(make_project(tmp_path, toml))


FACTOR_VARIANT = '[[variant]]\nname = "v"\nlibc = "musl"\nopt = "Os"\nkernel = "tiny"\n'


def add_factors(root, base=True):
    factors = root / "custom-layer" / "factors"
    for factor, levels in (
        ("libc", ("glibc", "musl")),
        ("opt", ("O2", "O3", "Os")),
        ("kernel", ("default", "tiny")),
    ):
        (factors / factor).mkdir(parents=True)
        for level in levels:
            (factors / factor / f"{level}.fragment").write_text("X=y\n")
    if base:
        (factors / "base.fragment").write_text("Y=y\n")


def test_factor_variant_loads(tmp_path):
    project = make_project(tmp_path, FACTOR_VARIANT)
    add_factors(project)
    variant = load_config(project).variants[0]
    assert variant.factors == (("libc", "musl"), ("opt", "Os"), ("kernel", "tiny"))
    assert variant.defconfig is None
    assert variant.label == "musl, Os, tiny"


def test_factor_variant_custom_label(tmp_path):
    project = make_project(tmp_path, FACTOR_VARIANT + 'label = "wlasna"\n')
    add_factors(project)
    assert load_config(project).variants[0].label == "wlasna"


@pytest.mark.parametrize(
    "toml, fragment",
    [
        (FACTOR_VARIANT + 'defconfig = "base_defconfig"\n', "albo"),
        ('[[variant]]\nname = "v"\nlibc = "musl"\n', "brak czynników"),
        ('[[variant]]\nname = "v"\nlibc = "nope"\nopt = "Os"\nkernel = "tiny"\n', "dostępne: glibc, musl"),
        ('[[variant]]\nname = "v"\nlibc = "../x"\nopt = "Os"\nkernel = "tiny"\n', "niepoprawny poziom"),
        (FACTOR_VARIANT + "dziwny = 1\n", "nieznane klucze"),
        ('[[variant]]\nname = "v"\n', "wymaga pola"),
    ],
)
def test_factor_variant_errors(tmp_path, toml, fragment):
    project = make_project(tmp_path, toml)
    add_factors(project)
    with pytest.raises(ConfigError, match=fragment):
        load_config(project)


def test_factor_variant_requires_base_fragment(tmp_path):
    project = make_project(tmp_path, FACTOR_VARIANT)
    add_factors(project, base=False)
    with pytest.raises(ConfigError, match="base.fragment"):
        load_config(project)


def test_legacy_and_factor_variants_can_be_mixed(tmp_path):
    project = make_project(tmp_path, VARIANT + "\n" + FACTOR_VARIANT)
    add_factors(project)
    variants = load_config(project).variants
    assert [variant.name for variant in variants] == ["base", "v"]
    assert variants[0].defconfig == "base_defconfig"
    assert variants[0].factors == ()


def test_factor_names_come_from_factor_directories(tmp_path):
    project = make_project(tmp_path, FACTOR_VARIANT.replace('kernel = "tiny"\n', 'kernel = "tiny"\nfs = "ext4"\n'))
    add_factors(project)
    (project / "custom-layer" / "factors" / "fs").mkdir()
    (project / "custom-layer" / "factors" / "fs" / "ext4.fragment").write_text("Z=y\n")
    config = load_config(project)
    assert config.factor_names == ("libc", "opt", "kernel", "fs")
    assert config.variants[0].label == "musl, Os, tiny, ext4"


def test_new_factor_directory_is_required_in_variants(tmp_path):
    project = make_project(tmp_path, FACTOR_VARIANT)
    add_factors(project)
    (project / "custom-layer" / "factors" / "fs").mkdir()
    (project / "custom-layer" / "factors" / "fs" / "ext4.fragment").write_text("Z=y\n")
    with pytest.raises(ConfigError, match="brak czynników fs"):
        load_config(project)


def test_factor_order_follows_first_variant(tmp_path):
    toml = '[[variant]]\nname = "v"\nkernel = "tiny"\nlibc = "musl"\nopt = "Os"\n'
    project = make_project(tmp_path, toml)
    add_factors(project)
    assert load_config(project).factor_names == ("kernel", "libc", "opt")


PLOT = FACTOR_VARIANT + '[[variant]]\nname = "w"\nlibc = "glibc"\nopt = "O2"\nkernel = "default"\n'


def test_plot_options_loaded(tmp_path):
    toml = PLOT + (
        '[plot]\ncolumns = 4\ncolor_by = "libc"\nhatch_by = "kernel"\n'
        'colors = { glibc = "tab:blue", musl = "#ff8800" }\nhatches = { tiny = "//" }\n'
        'order = { opt = ["O2", "Os"] }\nsort_by = ["kernel", "libc", "opt"]\n'
    )
    project = make_project(tmp_path, toml)
    add_factors(project)
    plot = load_config(project).plot
    assert plot.columns == 4 and plot.rows is None
    assert (plot.color_by, plot.hatch_by) == ("libc", "kernel")
    assert dict(plot.colors) == {"glibc": "tab:blue", "musl": "#ff8800"}
    assert dict(plot.hatches) == {"tiny": "//"}
    assert plot.order == (("opt", ("O2", "Os")),)
    assert plot.sort_by == ("kernel", "libc", "opt")


@pytest.mark.parametrize(
    "plot, fragment",
    [
        ("columns = 0\n", "columns"),
        ('color_by = "fs"\n', "nieznany czynnik"),
        ('color_by = "libc"\ncolors = { glibcx = "red" }\n', "nie jest poziomem"),
        ('color_by = "libc"\ncolors = { glibc = "niekolor" }\n', "nieznany kolor"),
        ('hatch_by = "kernel"\nhatches = { tiny = "ab" }\n', "wzór kreskowania"),
        ('hatches = { tiny = "//" }\n', "hatch_by"),
        ('order = { opt = ["O9"] }\n', "nieznane poziomy"),
        ('sort_by = ["fs"]\n', "sort_by"),
        ("dziwne = 1\n", "nieznane klucze"),
    ],
)
def test_plot_options_errors(tmp_path, plot, fragment):
    project = make_project(tmp_path, PLOT + "[plot]\n" + plot)
    add_factors(project)
    with pytest.raises(ConfigError, match=fragment):
        load_config(project)


from orchestrator.scenarios import DEFAULT_SCENARIOS, Scenario


def test_load_config_default_scenarios(tmp_path):
    assert load_config(make_project(tmp_path)).scenarios == DEFAULT_SCENARIOS


def test_load_config_reads_scenarios(tmp_path):
    toml = VARIANT + (
        '\n[[scenario]]\nname = "mem_read"\ntest = "memory"\nthreads = 4\n'
        'options = { memory-oper = "read", time = 5 }\n'
    )
    config = load_config(make_project(tmp_path, toml))
    assert config.scenarios == (
        Scenario("mem_read", "memory", (("memory-oper", "read"), ("time", "5")), 4),
    )


def test_load_config_rejects_bad_scenario(tmp_path):
    toml = VARIANT + '\n[[scenario]]\nname = "x"\ntest = "disk"\n'
    with pytest.raises(ConfigError, match="nieznany test"):
        load_config(make_project(tmp_path, toml))
