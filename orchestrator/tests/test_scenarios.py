from dataclasses import fields

import pytest

from orchestrator.parser import Report
from orchestrator.scenarios import (
    DEFAULT_SCENARIOS,
    HEADLINE_METRIC,
    TEST_METRICS,
    Scenario,
    ScenarioError,
    encode_scenarios,
    parse_scenarios,
    scenario_columns,
)

DEFAULT_ENCODED = (
    "cpu:cpu::--cpu-max-prime=20000"
    "+mem:memory::--memory-block-size=64M,--memory-scope=local,"
    "--memory-total-size=100T,--time=10"
)


def entry(**fields_):
    return {"name": "s", "test": "cpu", **fields_}


def test_default_scenarios_match_current_script():
    assert [scenario.name for scenario in DEFAULT_SCENARIOS] == ["cpu", "mem"]
    assert [scenario.test for scenario in DEFAULT_SCENARIOS] == ["cpu", "memory"]
    assert dict(DEFAULT_SCENARIOS[0].options) == {"cpu-max-prime": "20000"}
    assert dict(DEFAULT_SCENARIOS[1].options) == {
        "memory-block-size": "64M",
        "memory-scope": "local",
        "memory-total-size": "100T",
        "time": "10",
    }


def test_encode_default_scenarios():
    assert encode_scenarios(DEFAULT_SCENARIOS) == DEFAULT_ENCODED


def test_encode_threads_and_empty_options():
    scenarios = (
        Scenario("a", "cpu", (), 4),
        Scenario("b", "memory", (("time", "5"),), None),
    )
    assert encode_scenarios(scenarios) == "a:cpu:4:+b:memory::--time=5"


def test_empty_entries_give_defaults():
    assert parse_scenarios([]) == DEFAULT_SCENARIOS
    assert parse_scenarios(None) == DEFAULT_SCENARIOS


def test_parse_valid_scenario():
    result = parse_scenarios(
        [entry(name="mem_read", test="memory", options={"memory-oper": "read", "time": 5}, threads=4)]
    )
    assert result == (
        Scenario("mem_read", "memory", (("memory-oper", "read"), ("time", "5")), 4),
    )


def test_options_are_sorted():
    result = parse_scenarios([entry(options={"time": 5, "cpu-max-prime": 100})])
    assert result[0].options == (("cpu-max-prime", "100"), ("time", "5"))


@pytest.mark.parametrize("name", [None, "", "Cpu", "1cpu", "a:b", "x" * 25])
def test_invalid_names(name):
    with pytest.raises(ScenarioError, match="niepoprawna nazwa"):
        parse_scenarios([entry(name=name)])


def test_unknown_keys():
    with pytest.raises(ScenarioError, match="nieznane klucze"):
        parse_scenarios([entry(dziwny=1)])


def test_duplicate_names():
    with pytest.raises(ScenarioError, match="powtórzona nazwa"):
        parse_scenarios([entry(), entry()])


@pytest.mark.parametrize("test", ["disk", None])
def test_unknown_test(test):
    with pytest.raises(ScenarioError, match="nieznany test"):
        parse_scenarios([entry(test=test)])


@pytest.mark.parametrize(
    "test, option",
    [("cpu", "memory-oper"), ("memory", "cpu-max-prime"), ("cpu", "threads")],
)
def test_option_not_allowed_for_test(test, option):
    with pytest.raises(ScenarioError, match="niedozwolona dla testu"):
        parse_scenarios([entry(test=test, options={option: 1})])


@pytest.mark.parametrize("value", [True, -5, "a b", 1.5, "x;y"])
def test_invalid_option_values(value):
    with pytest.raises(ScenarioError):
        parse_scenarios([entry(options={"cpu-max-prime": value})])


@pytest.mark.parametrize("threads", [0, 257, True, "2"])
def test_invalid_threads(threads):
    with pytest.raises(ScenarioError, match="threads"):
        parse_scenarios([entry(threads=threads)])


def test_encoded_length_limit():
    entries = [
        {"name": f"s{index:02d}", "test": "cpu", "options": {"cpu-max-prime": 20000}}
        for index in range(45)
    ]
    with pytest.raises(ScenarioError, match="za długie"):
        parse_scenarios(entries)


def test_entries_must_be_tables():
    with pytest.raises(ScenarioError, match="listą tabel"):
        parse_scenarios("x")
    with pytest.raises(ScenarioError, match="listą tabel"):
        parse_scenarios([1, 2])


LEGACY_COLUMNS = {
    "cpu_events_per_s",
    "cpu_total_time_s",
    "cpu_total_events",
    "cpu_lat_min_ms",
    "cpu_lat_avg_ms",
    "cpu_lat_max_ms",
    "cpu_lat_p95_ms",
    "mem_ops_total",
    "mem_ops_per_s",
    "mem_mib_total",
    "mem_mib_per_s",
    "mem_total_time_s",
    "mem_lat_avg_ms",
    "mem_lat_max_ms",
}


def test_columns_match_legacy_report_fields():
    columns = set(scenario_columns(DEFAULT_SCENARIOS[0])) | set(scenario_columns(DEFAULT_SCENARIOS[1]))
    assert columns == LEGACY_COLUMNS


def test_headline_units_cover_all_tests():
    from orchestrator.scenarios import HEADLINE_UNIT

    assert set(HEADLINE_UNIT) == set(TEST_METRICS)


def test_headline_metrics_are_known():
    for test, metric in HEADLINE_METRIC.items():
        assert metric in TEST_METRICS[test]
