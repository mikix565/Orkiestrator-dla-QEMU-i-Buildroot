from pathlib import Path

from orchestrator.config import Experiment
from orchestrator.qemu_runner import build_command

IMAGES = Path("/tmp/images")


def test_kvm_enabled_uses_host_cpu():
    command = build_command(Experiment(kvm=True), IMAGES)
    assert "-enable-kvm" in command
    assert command[command.index("-cpu") + 1] == "host"


def test_kvm_disabled_uses_max_cpu():
    command = build_command(Experiment(kvm=False), IMAGES)
    assert "-enable-kvm" not in command
    assert command[command.index("-cpu") + 1] == "max"


def test_threads_follow_smp():
    command = build_command(Experiment(smp=4), IMAGES)
    assert command[command.index("-smp") + 1] == "4"
    assert "bench.threads=4" in command[command.index("-append") + 1]


def test_disk_is_snapshot():
    command = build_command(Experiment(), IMAGES)
    assert "snapshot=on" in command[command.index("-drive") + 1]


def test_cpu_affinity_adds_taskset():
    command = build_command(Experiment(cpu_affinity="2,3"), IMAGES)
    assert command[:3] == ["taskset", "-c", "2,3"]
    assert command[3] == "qemu-system-x86_64"


def test_no_affinity_starts_with_qemu():
    command = build_command(Experiment(), IMAGES)
    assert command[0] == "qemu-system-x86_64"


from orchestrator.scenarios import DEFAULT_SCENARIOS, encode_scenarios


def test_scenarios_in_kernel_command_line():
    command = build_command(Experiment(), IMAGES, scenarios=DEFAULT_SCENARIOS)
    append = command[command.index("-append") + 1]
    assert f"bench.scenarios={encode_scenarios(DEFAULT_SCENARIOS)}" in append


def test_no_scenarios_no_parameter():
    command = build_command(Experiment(), IMAGES)
    assert "bench.scenarios" not in command[command.index("-append") + 1]
