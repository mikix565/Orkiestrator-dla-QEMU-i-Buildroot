from orchestrator import cli
from orchestrator.check import missing_combinations, run_check
from orchestrator.config import load_config

FRAGMENTS = {
    ("libc", "glibc"): "BR2_TOOLCHAIN_BUILDROOT_GLIBC=y\n",
    ("libc", "musl"): "BR2_TOOLCHAIN_BUILDROOT_MUSL=y\n",
    ("opt", "O2"): "BR2_OPTIMIZE_2=y\n",
    ("opt", "Os"): "BR2_OPTIMIZE_S=y\n",
    ("opt", "O3"): "BR2_OPTIMIZE_3=y\n",
}
FULL = [("a", "glibc", "O2"), ("b", "glibc", "Os"), ("c", "musl", "O2"), ("d", "musl", "Os")]


def make_project(tmp_path, variants=FULL):
    for name in ("buildroot", "dl"):
        (tmp_path / name).mkdir()
    factors = tmp_path / "custom-layer" / "factors"
    for (factor, level), text in FRAGMENTS.items():
        (factors / factor).mkdir(parents=True, exist_ok=True)
        (factors / factor / f"{level}.fragment").write_text(text)
    (factors / "base.fragment").write_text("BR2_x86_64=y\n")
    (tmp_path / "orchestrator").mkdir()
    toml = "".join(
        f'[[variant]]\nname = "{name}"\nlibc = "{libc}"\nopt = "{opt}"\n\n' for name, libc, opt in variants
    )
    (tmp_path / "orchestrator" / "variants.toml").write_text(toml)
    return tmp_path


def build(tmp_path, name, config_text, images=True):
    directory = tmp_path / "out" / name
    (directory / "images").mkdir(parents=True)
    (directory / ".config").write_text(config_text)
    if images:
        (directory / "images" / "bzImage").write_bytes(b"k")
        (directory / "images" / "rootfs.ext2").write_bytes(b"r")


def check(tmp_path):
    lines = []
    code = run_check(load_config(tmp_path), log=lines.append)
    return code, "\n".join(lines)


def test_full_grid_is_correct(tmp_path):
    code, output = check(make_project(tmp_path))
    assert code == 0
    assert "Konfiguracja poprawna." in output
    assert "Ostrzeżenie" not in output


def test_unused_level_is_listed(tmp_path):
    code, output = check(make_project(tmp_path))
    assert "O2, O3 (nieużywany), Os" in output


def test_missing_combination_is_warning(tmp_path):
    code, output = check(make_project(tmp_path, FULL[:3]))
    assert code == 0
    assert "Ostrzeżenie: brak wariantu dla kombinacji: musl, Os" in output
    assert "ostrzeżenia: 1" in output


def test_duplicated_combination_is_warning(tmp_path):
    project = make_project(tmp_path, FULL + [("e", "glibc", "O2")])
    missing, duplicated = missing_combinations(load_config(project))
    assert missing == []
    assert duplicated == [("glibc, O2", ["a", "e"])]


def test_duplicated_option_in_fragments_is_error(tmp_path):
    project = make_project(tmp_path)
    (project / "custom-layer" / "factors" / "opt" / "Os.fragment").write_text(
        "BR2_OPTIMIZE_S=y\nBR2_TOOLCHAIN_BUILDROOT_GLIBC=y\n"
    )
    code, output = check(project)
    assert code == 1
    assert "Błąd: wariant b:" in output


def test_built_variant_statuses(tmp_path):
    project = make_project(tmp_path)
    build(project, "a", "BR2_TOOLCHAIN_BUILDROOT_GLIBC=y\nBR2_OPTIMIZE_2=y\n")
    build(project, "b", "BR2_TOOLCHAIN_BUILDROOT_GLIBC=y\nBR2_OPTIMIZE_S=y\n", images=False)
    code, output = check(project)
    assert code == 0
    assert "zbudowany, zgodny" in output
    assert "skonfigurowany, brak obrazów" in output
    assert "niezbudowany" in output


def test_inconsistent_build_is_error(tmp_path):
    project = make_project(tmp_path)
    build(project, "c", "BR2_TOOLCHAIN_BUILDROOT_GLIBC=y\nBR2_OPTIMIZE_2=y\n")
    code, output = check(project)
    assert code == 1
    assert "zbudowany, niezgodny z fragmentami" in output
    assert "libc=musl: oczekiwano BR2_TOOLCHAIN_BUILDROOT_MUSL=y" in output


def test_cli_check_command(tmp_path, capsys):
    project = make_project(tmp_path)
    assert cli.main(["--project-dir", str(project), "check"]) == 0
    assert "Warianty (4):" in capsys.readouterr().out
