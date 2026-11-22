import csv
import hashlib
import os
import shutil
import stat
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from orchestrator.config import Config, Variant
from orchestrator.experiment import ExperimentError, detect_libc, select_variants
from orchestrator.storage import new_experiment_id
from orchestrator.variants import defconfig_sha256, generated_defconfig_path, prepare_defconfig

BUILD_COLUMNS = (
    "timestamp",
    "variant",
    "libc",
    "clean_build",
    "status",
    "duration_s",
    "bzimage_bytes",
    "rootfs_bytes",
    "target_bytes",
    "target_files",
    "defconfig_sha256",
    "log",
)
LOG_TAIL_LINES = 30


class BuilderError(Exception):
    pass


@dataclass(frozen=True)
class BuildResult:
    variant: str
    clean_build: bool
    status: str
    duration_s: float
    returncode: int
    log_path: Path
    artifacts: dict


def run_logged(command: list[str], cwd: Path, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as handle:
        handle.write(("$ " + " ".join(command) + "\n").encode())
        handle.flush()
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
            )
        except OSError as error:
            raise BuilderError(f"nie można uruchomić {command[0]}: {error}") from None
    return completed.returncode


def configure_command(config: Config, variant: Variant) -> list[str]:
    command = [
        "make",
        f"O={config.out_dir / variant.name}",
        f"BR2_EXTERNAL={config.external_dir}",
    ]
    if variant.defconfig:
        return command + [variant.defconfig]
    return command + ["defconfig", f"BR2_DEFCONFIG={generated_defconfig_path(config, variant)}"]


def build_command(config: Config, variant: Variant) -> list[str]:
    return ["make", f"O={config.out_dir / variant.name}"]


def remove_variant_dir(config: Config, variant: Variant) -> None:
    target = (config.out_dir / variant.name).resolve()
    if target.parent != config.out_dir.resolve() or target.name != variant.name:
        raise BuilderError(f"odmowa usunięcia {target}")
    if target.exists():
        shutil.rmtree(target)


def directory_size(path: Path) -> tuple[int, int]:
    total = 0
    files = 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            info = os.lstat(os.path.join(root, name))
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
                files += 1
    return total, files


def file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def measure_artifacts(config: Config, variant: Variant) -> dict:
    out = config.out_dir / variant.name
    target = out / "target"
    target_bytes, target_files = directory_size(target) if target.is_dir() else (None, None)
    return {
        "bzimage_bytes": file_size(out / "images" / "bzImage"),
        "rootfs_bytes": file_size(out / "images" / "rootfs.ext2"),
        "target_bytes": target_bytes,
        "target_files": target_files,
    }


def sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unknown"


def log_tail(path: Path, lines: int = LOG_TAIL_LINES) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def append_build_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=BUILD_COLUMNS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)
        handle.flush()
        os.fsync(handle.fileno())


def build_variant(
    config: Config,
    variant: Variant,
    clean: bool = False,
    runner: Callable[[list[str], Path, Path], int] = run_logged,
    log: Callable[[str], None] = print,
) -> BuildResult:
    log_path = config.results_dir / "builds" / f"{new_experiment_id()}_{variant.name}.log"
    out = config.out_dir / variant.name
    if clean:
        log(f"[{variant.name}] usuwam {out}")
        remove_variant_dir(config, variant)
    clean_build = not (out / ".config").exists()
    prepare_defconfig(config, variant)
    started = time.monotonic()
    source = variant.defconfig or "czynniki: " + ", ".join(
        f"{factor}={level}" for factor, level in variant.factors
    )
    log(f"[{variant.name}] konfiguracja ({source})")
    code = runner(configure_command(config, variant), config.buildroot_dir, log_path)
    if code == 0:
        log(f"[{variant.name}] budowanie, log: {log_path}")
        code = runner(build_command(config, variant), config.buildroot_dir, log_path)
    duration = time.monotonic() - started
    status = "ok" if code == 0 else "failed"
    artifacts = measure_artifacts(config, variant) if code == 0 else {}
    try:
        libc = detect_libc(config.out_dir, variant.name)
    except ExperimentError:
        libc = "unknown"
    append_build_row(
        config.results_dir / "builds.csv",
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "variant": variant.name,
            "libc": libc,
            "clean_build": clean_build,
            "status": status,
            "duration_s": round(duration, 1),
            "bzimage_bytes": artifacts.get("bzimage_bytes"),
            "rootfs_bytes": artifacts.get("rootfs_bytes"),
            "target_bytes": artifacts.get("target_bytes"),
            "target_files": artifacts.get("target_files"),
            "defconfig_sha256": defconfig_sha256(config, variant),
            "log": str(log_path),
        },
    )
    if code != 0:
        log(f"[{variant.name}] błąd budowy (kod {code}); ostatnie linie logu:")
        log(log_tail(log_path))
    else:
        log(f"[{variant.name}] gotowe w {duration:.0f} s")
    return BuildResult(variant.name, clean_build, status, duration, code, log_path, artifacts)


def build_all(
    config: Config,
    variant_names: list[str] | None = None,
    clean: bool = False,
    runner: Callable[[list[str], Path, Path], int] = run_logged,
    log: Callable[[str], None] = print,
) -> list[BuildResult]:
    results = []
    for variant in select_variants(config, variant_names):
        result = build_variant(config, variant, clean, runner, log)
        results.append(result)
        if result.status != "ok":
            break
    return results
