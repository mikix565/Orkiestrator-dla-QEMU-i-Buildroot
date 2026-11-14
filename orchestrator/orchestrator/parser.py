import re
from collections.abc import Sequence
from dataclasses import dataclass

from orchestrator.scenarios import Scenario

META_COLUMNS = ("threads", "boot_uptime_s", "mem_total_kb", "mem_available_kb")
SECTION_PATTERN = re.compile(r"^=== (SCENARIO \S+ \S+|CPU|MEMORY|END) ===\s*$", re.MULTILINE)
LEGACY_SECTIONS = {"CPU": ("cpu", "cpu"), "MEMORY": ("mem", "memory")}

CPU_EXTRACTORS = (
    ("events_per_s", r"events per second:\s+([\d.]+)", float),
    ("total_time_s", r"total time:\s+([\d.]+)s", float),
    ("total_events", r"total number of events:\s+(\d+)", int),
    ("lat_min_ms", r"^\s+min:\s+([\d.]+)", float),
    ("lat_avg_ms", r"^\s+avg:\s+([\d.]+)", float),
    ("lat_max_ms", r"^\s+max:\s+([\d.]+)", float),
    ("lat_p95_ms", r"95th percentile:\s+([\d.]+)", float),
)
MEMORY_EXTRACTORS = (
    ("ops_total", r"Total operations:\s+(\d+)", int),
    ("ops_per_s", r"Total operations:\s+\d+ \(\s*([\d.]+) per second\)", float),
    ("mib_total", r"^([\d.]+) MiB transferred", float),
    ("mib_per_s", r"MiB transferred \(([\d.]+) MiB/sec\)", float),
    ("total_time_s", r"total time:\s+([\d.]+)s", float),
    ("lat_avg_ms", r"^\s+avg:\s+([\d.]+)", float),
    ("lat_max_ms", r"^\s+max:\s+([\d.]+)", float),
)
EXTRACTORS = {"cpu": CPU_EXTRACTORS, "memory": MEMORY_EXTRACTORS}


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class Report:
    values: dict

    def __getattr__(self, name):
        values = self.__dict__.get("values", {})
        if name in values:
            return values[name]
        raise AttributeError(name)

    def as_dict(self) -> dict:
        return dict(self.values)


def _num(pattern: str, text: str, cast=float):
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        raise ParseError(f"brak wzorca: {pattern}")
    return cast(match.group(1))


def _split(text: str) -> tuple[str, list[tuple[str, str, str]]]:
    parts = SECTION_PATTERN.split(text)
    sections = []
    ended = False
    for index in range(1, len(parts) - 1, 2):
        label, body = parts[index], parts[index + 1]
        if label == "END":
            ended = True
            break
        if label in LEGACY_SECTIONS:
            name, test = LEGACY_SECTIONS[label]
        else:
            _, name, test = label.split(" ")
        sections.append((name, test, body))
    if not ended:
        raise ParseError("brak znacznika === END === (raport ucięty)")
    return parts[0], sections


def _check_expected(seen: dict[str, str], expected: Sequence[Scenario]) -> None:
    expected_tests = {scenario.name: scenario.test for scenario in expected}
    for name, test in expected_tests.items():
        if name not in seen:
            raise ParseError(f"brak scenariusza {name} w raporcie")
        if seen[name] != test:
            raise ParseError(
                f"scenariusz {name}: raport zawiera test {seen[name]}, oczekiwano {test}"
            )
    for name in seen:
        if name not in expected_tests:
            raise ParseError(f"nieoczekiwany scenariusz {name} w raporcie")


def parse_report(text: str, expected: Sequence[Scenario] | None = None) -> Report:
    head, sections = _split(text)
    if not sections:
        raise ParseError("raport nie zawiera żadnego scenariusza")
    values = {
        "threads": _num(r"^=== META threads=(\d+)", head, int),
        "boot_uptime_s": _num(r"boot_uptime=([\d.]+)", head),
        "mem_total_kb": _num(r"^MemTotal:\s+(\d+) kB", head, int),
        "mem_available_kb": _num(r"^MemAvailable:\s+(\d+) kB", head, int),
    }
    seen: dict[str, str] = {}
    for name, test, body in sections:
        if name in seen:
            raise ParseError(f"powtórzony scenariusz w raporcie: {name}")
        if test not in EXTRACTORS:
            raise ParseError(f"scenariusz {name}: nieznany test {test}")
        seen[name] = test
        for metric, pattern, cast in EXTRACTORS[test]:
            values[f"{name}_{metric}"] = _num(pattern, body, cast)
    if expected is not None:
        _check_expected(seen, expected)
    return Report(values)
