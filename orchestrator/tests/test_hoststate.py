import pytest

from orchestrator.hoststate import (
    CpuTimes,
    busy_percent,
    idle_probe,
    read_cpu_times,
    read_load1,
)


def test_read_cpu_times(tmp_path):
    stat = tmp_path / "stat"
    stat.write_text("cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1 2 3\n")
    assert read_cpu_times(stat) == CpuTimes(busy=150, total=1000)


def test_read_cpu_times_bad_file(tmp_path):
    assert read_cpu_times(tmp_path / "missing") is None
    bad = tmp_path / "bad"
    bad.write_text("nonsense\n")
    assert read_cpu_times(bad) is None


def test_busy_percent():
    assert busy_percent(CpuTimes(100, 1000), CpuTimes(150, 1100)) == 50.0


def test_busy_percent_without_data():
    assert busy_percent(None, CpuTimes(1, 2)) is None
    assert busy_percent(CpuTimes(1, 2), CpuTimes(1, 2)) is None


def test_read_load1(tmp_path):
    loadavg = tmp_path / "loadavg"
    loadavg.write_text("0.52 0.40 0.31 1/500 12345\n")
    assert read_load1(loadavg) == pytest.approx(0.52)


def test_idle_probe_uses_given_sleep():
    slept = []
    result = idle_probe(0.25, sleep=slept.append)
    assert slept == [0.25]
    assert result is None or 0.0 <= result <= 100.0
