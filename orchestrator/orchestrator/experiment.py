import hashlib
import os
import platform
import random
import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

from orchestrator.config import Config, Variant
from orchestrator.hoststate import busy_percent, idle_probe, read_cpu_times, read_load1
from orchestrator.parser import ParseError, parse_report
from orchestrator.variants import defconfig_sha256, factor_fragments, fragment_options, option_name, uses_factors
from orchestrator.qemu_runner import RunResult, run_vm
from orchestrator.scenarios import HEADLINE_UNIT, headline_column
from orchestrator.storage import (
    ExperimentStore,
    build_row,
    make_run_id,
    new_experiment_id,
    run_columns,
)

LIBC_PATTERN = re.compile(r"^BR2_TOOLCHAIN_BUILDROOT_(GLIBC|MUSL|UCLIBC)=y$", re.MULTILINE)
LIBC_NAMES = {"GLIBC": "glibc", "MUSL": "musl", "UCLIBC": "uclibc-ng"}
PHASE_LABEL = {"warmup": "rozgrzewka", "measure": "pomiar"}
HOST_BUSY_WARN_PCT = 5.0


class ExperimentError(Exception):
    pass


@dataclass(frozen=True)
class Slot:
    variant: str
    phase: str
    index: int
    order: int


def images_dir(config: Config, variant: str) -> Path:
    return config.out_dir / variant / "images"


def detect_libc(out_dir: Path, variant: str) -> str:
    config_file = out_dir / variant / ".config"
    try:
        text = config_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ExperimentError(
            f"brak {config_file}; najpierw skonfiguruj i zbuduj wariant {variant}"
        ) from None
    match = LIBC_PATTERN.search(text)
    return LIBC_NAMES[match.group(1)] if match else "unknown"


def _buildroot_config(out_dir: Path, variant: str) -> str:
    config_file = out_dir / variant / ".config"
    try:
        return config_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ExperimentError(
            f"brak {config_file}; najpierw skonfiguruj i zbuduj wariant {variant}"
        ) from None


def check_fragments(config: Config, variants: list[Variant]) -> None:
    for variant in variants:
        if not uses_factors(variant):
            continue
        actual = {}
        for line in _buildroot_config(config.out_dir, variant.name).splitlines():
            name = option_name(line.rstrip())
            if name is not None:
                actual[name] = line.rstrip()
        wrong = []
        for factor, level, path in factor_fragments(config, variant):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as error:
                raise ExperimentError(f"nie można odczytać {path}: {error}") from None
            for name, line in fragment_options(text):
                found = actual.get(name)
                if found == line or (found is None and line.endswith(" is not set")):
                    continue
                wrong.append(f"{factor}={level}: oczekiwano {line}, w .config {found or 'brak opcji'}")
        if wrong:
            raise ExperimentError(
                f"wariant {variant.name} nie zgadza się z fragmentami czynników "
                f"(out/{variant.name}/.config: {'; '.join(wrong)}); przebuduj wariant (build --clean)"
            )


def make_plan(variants: list[str], repetitions: int, warmup_runs: int, seed: int) -> list[Slot]:
    rng = random.Random(seed)
    plan = []
    order = 0
    for index in range(1, warmup_runs + 1):
        for name in variants:
            order += 1
            plan.append(Slot(name, "warmup", index, order))
    for index in range(1, repetitions + 1):
        block = list(variants)
        rng.shuffle(block)
        for name in block:
            order += 1
            plan.append(Slot(name, "measure", index, order))
    return plan


def check_images(config: Config, variants: list[Variant]) -> None:
    for variant in variants:
        directory = images_dir(config, variant.name)
        for name in ("bzImage", "rootfs.ext2"):
            if not (directory / name).is_file():
                raise ExperimentError(
                    f"brak {directory / name}; najpierw zbuduj wariant {variant.name}"
                )


def select_variants(config: Config, names: list[str] | None) -> list[Variant]:
    if not names:
        return list(config.variants)
    known = {variant.name: variant for variant in config.variants}
    names = list(dict.fromkeys(names))
    unknown = [name for name in names if name not in known]
    if unknown:
        raise ExperimentError(f"nieznane warianty: {', '.join(unknown)}")
    return [known[name] for name in names]


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _run_text(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _first_line(text: str | None) -> str:
    if not text or not text.strip():
        return "unknown"
    return text.strip().splitlines()[0].strip()


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unknown"


def _cpu_model() -> str:
    for line in (_read_text(Path("/proc/cpuinfo")) or "").splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return "unknown"


def collect_meta(
    config: Config,
    experiment_id: str,
    variants: list[Variant],
    libc: dict[str, str],
    plan: list[Slot],
    qemu_binary: str,
) -> dict:
    git_status = _run_text(["git", "-C", str(config.project_dir), "status", "--porcelain"])
    makefile = _read_text(config.buildroot_dir / "Makefile") or ""
    version = re.search(r"^export BR2_VERSION := (\S+)", makefile, re.MULTILINE)
    meminfo = _read_text(Path("/proc/meminfo")) or ""
    memory = re.search(r"^MemTotal:\s+(\d+) kB", meminfo, re.MULTILINE)
    governor = _read_text(Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"))
    return {
        "experiment_id": experiment_id,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "host": {
            "system": platform.system(),
            "kernel": platform.release(),
            "machine": platform.machine(),
            "cpu_model": _cpu_model(),
            "cpu_count": os.cpu_count(),
            "mem_total_kb": int(memory.group(1)) if memory else None,
            "cpu_governor": governor.strip() if governor else "unknown",
            "kvm_available": os.access("/dev/kvm", os.R_OK | os.W_OK),
        },
        "software": {
            "python": platform.python_version(),
            "qemu": _first_line(_run_text([qemu_binary, "--version"])),
            "buildroot": version.group(1) if version else "unknown",
            "git_commit": _first_line(
                _run_text(["git", "-C", str(config.project_dir), "rev-parse", "HEAD"])
            ),
            "git_dirty": None if git_status is None else bool(git_status),
        },
        "experiment": asdict(config.experiment),
        "factors": list(config.factor_names),
        "scenarios": [asdict(scenario) for scenario in config.scenarios],
        "variants": [
            {
                "name": variant.name,
                "label": variant.label,
                "defconfig": variant.defconfig,
                "defconfig_sha256": defconfig_sha256(config, variant),
                "libc": libc[variant.name],
                "factors": variant.levels,
            }
            for variant in variants
        ],
        "plan": [asdict(slot) for slot in plan],
    }


def run_experiment(
    config: Config,
    variant_names: list[str] | None = None,
    qemu_binary: str = "qemu-system-x86_64",
    runner: Callable[..., RunResult] = run_vm,
    log: Callable[[str], None] = print,
) -> ExperimentStore:
    experiment = config.experiment
    if experiment.kvm and not os.access("/dev/kvm", os.R_OK | os.W_OK):
        raise ExperimentError("brak dostępu do /dev/kvm (ustaw kvm = false albo dodaj użytkownika do grupy kvm)")
    variants = select_variants(config, variant_names)
    check_images(config, variants)
    libc = {variant.name: detect_libc(config.out_dir, variant.name) for variant in variants}
    check_fragments(config, variants)
    factors = config.factor_names
    by_name = {variant.name: variant for variant in variants}
    plan = make_plan(
        [variant.name for variant in variants],
        experiment.repetitions,
        experiment.warmup_runs,
        experiment.seed,
    )
    experiment_id = new_experiment_id()
    store = ExperimentStore(config.results_dir, experiment_id, run_columns(config.scenarios, factors))
    meta = collect_meta(config, experiment_id, variants, libc, plan, qemu_binary)
    store.write_meta(meta)
    log(f"Eksperyment {experiment_id}: {len(plan)} przebiegów, wyniki w {store.directory}")
    counts: dict[str, int] = {}
    completed = False
    try:
        for position, slot in enumerate(plan, start=1):
            variant = by_name[slot.variant]
            run_id = make_run_id(experiment_id, slot.variant, slot.phase, slot.index)
            log(
                f"[{position}/{len(plan)}] {run_id} | wariant: {variant.name} "
                f"({variant.label}) | libc: {libc[slot.variant]} | "
                f"{PHASE_LABEL[slot.phase]} {slot.index}"
            )
            idle_busy = idle_probe()
            if idle_busy is not None and idle_busy > HOST_BUSY_WARN_PCT:
                log(f"    UWAGA: host zajęty przed startem ({idle_busy:.1f}% CPU)")
            load1 = read_load1()
            cpu_before = read_cpu_times()
            result = runner(experiment, images_dir(config, slot.variant), qemu_binary, config.scenarios)
            run_busy = busy_percent(cpu_before, read_cpu_times())
            host = {
                "host_idle_busy_pct": idle_busy,
                "host_run_busy_pct": run_busy,
                "host_load1": load1,
            }
            report = None
            note = ""
            if result.status == "ok":
                try:
                    report = parse_report(result.report, config.scenarios)
                except ParseError as error:
                    result = replace(result, status="parse_error")
                    note = str(error)
            store.save_raw(run_id, result.report, result.console)
            store.append_run(
                build_row(
                    run_id=run_id,
                    experiment_id=experiment_id,
                    variant=slot.variant,
                    label=variant.label,
                    factors={factor: variant.levels.get(factor, "") for factor in factors},
                    phase=slot.phase,
                    index=slot.index,
                    order=slot.order,
                    result=result,
                    report=report,
                    host=host,
                    note=note,
                )
            )
            counts[result.status] = counts.get(result.status, 0) + 1
            boot = f"{result.boot_host_s:.2f} s" if result.boot_host_s is not None else "brak"
            results = (
                ", ".join(
                    f"{scenario.name}: {report.values[headline_column(scenario)]:.1f} "
                    f"{HEADLINE_UNIT[scenario.test]}"
                    for scenario in config.scenarios
                )
                if report
                else "brak"
            )
            log(f"    status: {result.status} | boot (host): {boot} | {results}")
            if note:
                log(f"    {note}")
        completed = True
    finally:
        meta["finished_at"] = datetime.now().isoformat(timespec="seconds")
        meta["completed"] = completed
        meta["status_counts"] = counts
        store.write_meta(meta)
    return store
