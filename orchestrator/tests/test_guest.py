import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator.scenarios import DEFAULT_SCENARIOS, encode_scenarios

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "custom-layer"
    / "board"
    / "overlay"
    / "etc"
    / "init.d"
    / "S99benchmark"
)

pytestmark = pytest.mark.skipif(
    shutil.which("sh") is None or not SCRIPT.is_file(),
    reason="brak powłoki sh albo skryptu gościa",
)

SOURCE_AND_RUN = (
    'set -- stop; . "$GUEST_SCRIPT"; '
    'sysbench() { echo "SYSBENCH $*"; }; '
    'THREADS=$GUEST_THREADS; run_scenarios "$GUEST_SCENARIOS"'
)


def run_guest(scenarios, threads="2"):
    environment = {
        "PATH": os.environ["PATH"],
        "GUEST_SCRIPT": str(SCRIPT),
        "GUEST_THREADS": threads,
        "GUEST_SCENARIOS": scenarios,
    }
    completed = subprocess.run(
        ["sh", "-c", SOURCE_AND_RUN], env=environment, capture_output=True, text=True, timeout=20
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.splitlines()


def test_guest_runs_default_scenarios():
    assert run_guest(encode_scenarios(DEFAULT_SCENARIOS)) == [
        "=== SCENARIO cpu cpu ===",
        "SYSBENCH cpu --threads=2 --cpu-max-prime=20000 run",
        "=== SCENARIO mem memory ===",
        "SYSBENCH memory --threads=2 --memory-block-size=64M --memory-scope=local "
        "--memory-total-size=100T --time=10 run",
    ]


def test_guest_scenario_threads_override():
    assert run_guest("a:cpu:4:+b:memory::--time=5") == [
        "=== SCENARIO a cpu ===",
        "SYSBENCH cpu --threads=4 run",
        "=== SCENARIO b memory ===",
        "SYSBENCH memory --threads=2 --time=5 run",
    ]


def test_guest_empty_scenarios_run_nothing():
    assert run_guest("") == []


def test_guest_default_scenarios_match_python_defaults():
    match = re.search(r'^DEFAULT_SCENARIOS="(.*)"$', SCRIPT.read_text(), re.MULTILINE)
    assert match is not None
    assert match.group(1) == encode_scenarios(DEFAULT_SCENARIOS)


def test_guest_script_is_executable():
    assert os.access(SCRIPT, os.X_OK)
