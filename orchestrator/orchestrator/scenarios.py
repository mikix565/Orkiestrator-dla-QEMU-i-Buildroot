import re
from collections.abc import Sequence
from dataclasses import dataclass

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
VALUE_PATTERN = re.compile(r"^[A-Za-z0-9._]+$")
MAX_ENCODED_LENGTH = 1200
MAX_THREADS = 256
SCENARIO_KEYS = {"name", "test", "options", "threads"}

TEST_METRICS = {
    "cpu": (
        "events_per_s",
        "total_time_s",
        "total_events",
        "lat_min_ms",
        "lat_avg_ms",
        "lat_max_ms",
        "lat_p95_ms",
    ),
    "memory": (
        "ops_total",
        "ops_per_s",
        "mib_total",
        "mib_per_s",
        "total_time_s",
        "lat_avg_ms",
        "lat_max_ms",
    ),
}
HEADLINE_METRIC = {"cpu": "events_per_s", "memory": "mib_per_s"}
HEADLINE_UNIT = {"cpu": "zdarzeń/s", "memory": "MiB/s"}
ALLOWED_OPTIONS = {
    "cpu": ("cpu-max-prime", "events", "time"),
    "memory": (
        "events",
        "memory-access-mode",
        "memory-block-size",
        "memory-oper",
        "memory-scope",
        "memory-total-size",
        "time",
    ),
}


class ScenarioError(Exception):
    pass


@dataclass(frozen=True)
class Scenario:
    name: str
    test: str
    options: tuple[tuple[str, str], ...] = ()
    threads: int | None = None


DEFAULT_SCENARIOS = (
    Scenario("cpu", "cpu", (("cpu-max-prime", "20000"),)),
    Scenario(
        "mem",
        "memory",
        (
            ("memory-block-size", "64M"),
            ("memory-scope", "local"),
            ("memory-total-size", "100T"),
            ("time", "10"),
        ),
    ),
)


def scenario_columns(scenario: Scenario) -> tuple[str, ...]:
    return tuple(f"{scenario.name}_{metric}" for metric in TEST_METRICS[scenario.test])


def headline_column(scenario: Scenario) -> str:
    return f"{scenario.name}_{HEADLINE_METRIC[scenario.test]}"


def encode_scenarios(scenarios: Sequence[Scenario]) -> str:
    parts = []
    for scenario in scenarios:
        threads = "" if scenario.threads is None else str(scenario.threads)
        arguments = ",".join(f"--{key}={value}" for key, value in scenario.options)
        parts.append(f"{scenario.name}:{scenario.test}:{threads}:{arguments}")
    return "+".join(parts)


def _option_value(name: str, key: str, value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ScenarioError(f"scenariusz {name}: opcja {key} musi być liczbą lub tekstem")
    text = str(value)
    if not VALUE_PATTERN.match(text):
        raise ScenarioError(
            f"scenariusz {name}: niepoprawna wartość opcji {key}: {text!r} "
            "(dozwolone litery, cyfry, kropka i podkreślenie)"
        )
    return text


def parse_scenarios(entries) -> tuple[Scenario, ...]:
    if not entries:
        return DEFAULT_SCENARIOS
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ScenarioError("sekcja scenario musi być listą tabel [[scenario]]")
    scenarios = []
    seen = set()
    for entry in entries:
        name = entry.get("name")
        if not isinstance(name, str) or not NAME_PATTERN.match(name):
            raise ScenarioError(
                f"niepoprawna nazwa scenariusza: {name!r} "
                "(małe litery, cyfry i _, do 24 znaków, zaczyna się literą)"
            )
        unknown = set(entry) - SCENARIO_KEYS
        if unknown:
            raise ScenarioError(f"scenariusz {name}: nieznane klucze {', '.join(sorted(unknown))}")
        if name in seen:
            raise ScenarioError(f"powtórzona nazwa scenariusza: {name}")
        seen.add(name)
        test = entry.get("test")
        if not isinstance(test, str) or test not in TEST_METRICS:
            raise ScenarioError(
                f"scenariusz {name}: nieznany test {test!r} (dostępne: {', '.join(TEST_METRICS)})"
            )
        raw_options = entry.get("options", {})
        if not isinstance(raw_options, dict):
            raise ScenarioError(f"scenariusz {name}: options musi być tabelą")
        allowed = ALLOWED_OPTIONS[test]
        for key in raw_options:
            if key not in allowed:
                raise ScenarioError(
                    f"scenariusz {name}: opcja {key!r} niedozwolona dla testu {test} "
                    f"(dozwolone: {', '.join(allowed)})"
                )
        options = tuple(
            sorted((key, _option_value(name, key, value)) for key, value in raw_options.items())
        )
        threads = entry.get("threads")
        if threads is not None and (type(threads) is not int or not 1 <= threads <= MAX_THREADS):
            raise ScenarioError(
                f"scenariusz {name}: threads musi być liczbą całkowitą od 1 do {MAX_THREADS}"
            )
        scenarios.append(Scenario(name, test, options, threads))
    result = tuple(scenarios)
    length = len(encode_scenarios(result))
    if length > MAX_ENCODED_LENGTH:
        raise ScenarioError(
            f"opisy scenariuszy są za długie ({length} znaków, limit {MAX_ENCODED_LENGTH})"
        )
    return result
