import socket
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from orchestrator.config import Experiment
from orchestrator.scenarios import Scenario, encode_scenarios

BOOT_MARKER = "BOOT_DONE uptime="
END_MARKER = "=== END ==="
EXIT_GRACE_S = 20.0


class RunnerError(Exception):
    pass


@dataclass(frozen=True)
class RunResult:
    status: str
    boot_host_s: float | None
    total_s: float
    report: str
    console: str
    exit_code: int | None
    command: tuple[str, ...]


def build_command(
    experiment: Experiment,
    images_dir: Path,
    qemu_binary: str = "qemu-system-x86_64",
    scenarios: Sequence[Scenario] | None = None,
) -> list[str]:
    drive = f"file={images_dir / 'rootfs.ext2'},if=virtio,format=raw,snapshot=on"
    append = (
        "rootwait root=/dev/vda console=ttyS0 "
        f"bench.threads={experiment.smp} bench.port={experiment.port}"
    )
    if scenarios:
        append += f" bench.scenarios={encode_scenarios(scenarios)}"
    command = [
        qemu_binary,
        "-M", "pc",
        "-smp", str(experiment.smp),
        "-m", str(experiment.mem_mb),
        "-kernel", str(images_dir / "bzImage"),
        "-drive", drive,
        "-append", append,
        "-nic", "user,model=virtio-net-pci",
        "-display", "none",
        "-monitor", "none",
        "-serial", "stdio",
        "-no-reboot",
    ]
    if experiment.kvm:
        command += ["-enable-kvm", "-cpu", "host"]
    else:
        command += ["-cpu", "max"]
    if experiment.cpu_affinity:
        command = ["taskset", "-c", experiment.cpu_affinity] + command
    return command


def _open_listener(port: int) -> socket.socket:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("127.0.0.1", port))
    except OSError as error:
        server.close()
        raise RunnerError(f"nie można otworzyć portu {port}: {error}") from None
    server.listen(1)
    server.settimeout(0.5)
    return server


def _read_console(process, lines: list[str], state: dict, started: float) -> None:
    for line in process.stdout:
        lines.append(line)
        if BOOT_MARKER in line and "boot" not in state:
            state["boot"] = time.monotonic() - started


def _receive_report(server: socket.socket, process, deadline: float) -> str:
    connection = None
    exit_seen = False
    while connection is None:
        if time.monotonic() >= deadline:
            return ""
        try:
            connection, _ = server.accept()
        except socket.timeout:
            if exit_seen:
                return ""
            exit_seen = process.poll() is not None
    chunks = []
    with connection:
        connection.settimeout(1.0)
        while time.monotonic() < deadline:
            try:
                data = connection.recv(65536)
            except socket.timeout:
                continue
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode(errors="replace")


def _classify(report: str, boot: float | None, killed: bool, exit_code: int | None) -> str:
    if END_MARKER in report:
        return "ok"
    if report:
        return "truncated_report"
    if killed:
        return "no_boot" if boot is None else "timeout"
    if boot is None:
        return "qemu_failed" if exit_code else "no_boot"
    return "no_report"


def run_vm(
    experiment: Experiment,
    images_dir: Path,
    qemu_binary: str = "qemu-system-x86_64",
    scenarios: Sequence[Scenario] | None = None,
) -> RunResult:
    command = build_command(experiment, images_dir, qemu_binary, scenarios)
    server = _open_listener(experiment.port)
    lines: list[str] = []
    state: dict = {}
    started = time.monotonic()
    deadline = started + experiment.timeout_s
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
    except OSError as error:
        server.close()
        raise RunnerError(f"nie można uruchomić {command[0]}: {error}") from None
    reader = threading.Thread(
        target=_read_console, args=(process, lines, state, started), daemon=True
    )
    reader.start()
    try:
        report = _receive_report(server, process, deadline)
        wait_s = max(1.0, min(EXIT_GRACE_S, deadline - time.monotonic()))
        try:
            process.wait(timeout=wait_s)
        except subprocess.TimeoutExpired:
            pass
    finally:
        killed = process.poll() is None
        if killed:
            process.kill()
        process.wait()
        server.close()
        reader.join(timeout=2)
    total = time.monotonic() - started
    boot = state.get("boot")
    return RunResult(
        status=_classify(report, boot, killed, process.returncode),
        boot_host_s=boot,
        total_s=total,
        report=report,
        console="".join(lines),
        exit_code=process.returncode,
        command=tuple(command),
    )
