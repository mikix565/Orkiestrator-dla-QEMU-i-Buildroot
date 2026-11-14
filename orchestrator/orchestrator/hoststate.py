import time
from dataclasses import dataclass
from pathlib import Path

PROC_STAT = Path("/proc/stat")
PROC_LOADAVG = Path("/proc/loadavg")


@dataclass(frozen=True)
class CpuTimes:
    busy: int
    total: int


def read_cpu_times(path: Path = PROC_STAT) -> CpuTimes | None:
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0]
        parts = first.split()
        if not parts or parts[0] != "cpu":
            return None
        values = [int(item) for item in parts[1:]]
    except (OSError, IndexError, ValueError):
        return None
    if len(values) < 5:
        return None
    total = sum(values[:8])
    idle = values[3] + values[4]
    return CpuTimes(busy=total - idle, total=total)


def busy_percent(before: CpuTimes | None, after: CpuTimes | None) -> float | None:
    if before is None or after is None:
        return None
    total = after.total - before.total
    if total <= 0:
        return None
    return round(100.0 * (after.busy - before.busy) / total, 2)


def idle_probe(seconds: float = 1.0, sleep=time.sleep) -> float | None:
    before = read_cpu_times()
    sleep(seconds)
    return busy_percent(before, read_cpu_times())


def read_load1(path: Path = PROC_LOADAVG) -> float | None:
    try:
        return float(path.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None
