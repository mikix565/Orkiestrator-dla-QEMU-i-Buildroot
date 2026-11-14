from pathlib import Path

import pytest

from orchestrator.parser import EXTRACTORS, ParseError, parse_report
from orchestrator.scenarios import DEFAULT_SCENARIOS, TEST_METRICS, Scenario

DATA = Path(__file__).parent / "data"

CASES = {
    "report_base_sample.txt": {
        "threads": 2,
        "boot_uptime_s": 0.86,
        "mem_total_kb": 498516,
        "mem_available_kb": 477384,
        "cpu_events_per_s": 1049.59,
        "cpu_total_events": 10499,
        "cpu_lat_avg_ms": 1.90,
        "cpu_lat_p95_ms": 1.96,
        "mem_ops_total": 80467463,
        "mem_mib_per_s": 7857.14,
    },
    "report_musl_sample.txt": {
        "threads": 2,
        "boot_uptime_s": 0.75,
        "mem_total_kb": 498516,
        "mem_available_kb": 475536,
        "cpu_events_per_s": 1036.96,
        "cpu_total_events": 10372,
        "cpu_lat_avg_ms": 1.93,
        "cpu_lat_p95_ms": 2.03,
        "mem_ops_total": 81459500,
        "mem_mib_per_s": 7954.05,
    },
}


def load(filename):
    return (DATA / filename).read_text()


@pytest.mark.parametrize("filename", sorted(CASES))
def test_known_values(filename):
    report = parse_report(load(filename))
    for field, expected in CASES[filename].items():
        assert getattr(report, field) == pytest.approx(expected), field


@pytest.mark.parametrize("filename", sorted(CASES))
def test_memory_test_lasts_about_ten_seconds(filename):
    report = parse_report(load(filename))
    assert report.mem_total_time_s == pytest.approx(10.0, abs=0.1)


def test_truncated_report_raises():
    text = load("report_base_sample.txt").split("=== END ===")[0]
    with pytest.raises(ParseError):
        parse_report(text)


def test_missing_memory_section_raises():
    text = load("report_base_sample.txt").split("=== MEMORY ===")[0] + "=== END ===\n"
    with pytest.raises(ParseError, match="brak scenariusza mem"):
        parse_report(text, DEFAULT_SCENARIOS)


def test_missing_meta_raises():
    text = load("report_base_sample.txt").replace("=== META", "=== XXXX")
    with pytest.raises(ParseError):
        parse_report(text)


def test_empty_text_raises():
    with pytest.raises(ParseError):
        parse_report("")


def test_memory_rate_with_padding():
    report = parse_report(load("report_base_64m_sample.txt"))
    assert report.mem_ops_total == 3094
    assert report.mem_ops_per_s == pytest.approx(309.23)
    assert report.mem_mib_total == pytest.approx(198016.00)
    assert report.mem_mib_per_s == pytest.approx(19790.65)
    assert report.mem_lat_avg_ms == pytest.approx(6.47)
    assert report.mem_lat_max_ms == pytest.approx(7.85)


def new_format(text):
    return text.replace("=== CPU ===", "=== SCENARIO cpu cpu ===").replace(
        "=== MEMORY ===", "=== SCENARIO mem memory ==="
    )


def memory_body(text):
    return text.split("=== MEMORY ===")[1].split("=== END ===")[0]


def with_extra_scenario(text, name="mem_read"):
    return new_format(text).replace(
        "=== END ===", f"=== SCENARIO {name} memory ===" + memory_body(text) + "=== END ==="
    )


def test_new_marker_format_gives_same_values_as_legacy():
    text = load("report_base_sample.txt")
    assert parse_report(new_format(text)).as_dict() == parse_report(text).as_dict()


def test_extra_scenario_adds_prefixed_columns():
    report = parse_report(with_extra_scenario(load("report_base_sample.txt")))
    values = report.as_dict()
    assert values["mem_read_mib_per_s"] == pytest.approx(values["mem_mib_per_s"])
    assert values["mem_read_ops_total"] == values["mem_ops_total"]
    assert report.mem_read_lat_max_ms == pytest.approx(values["mem_lat_max_ms"])


def test_expected_scenarios_accepted():
    text = load("report_base_sample.txt")
    parse_report(text, DEFAULT_SCENARIOS)
    parse_report(new_format(text), DEFAULT_SCENARIOS)


def test_expected_scenario_missing_raises():
    text = with_extra_scenario(load("report_base_sample.txt"))
    expected = DEFAULT_SCENARIOS + (Scenario("other", "cpu"),)
    with pytest.raises(ParseError, match="brak scenariusza other"):
        parse_report(text, expected)


def test_unexpected_scenario_raises():
    text = with_extra_scenario(load("report_base_sample.txt"))
    with pytest.raises(ParseError, match="nieoczekiwany scenariusz mem_read"):
        parse_report(text, DEFAULT_SCENARIOS)


def test_expected_test_type_mismatch_raises():
    text = new_format(load("report_base_sample.txt"))
    expected = (DEFAULT_SCENARIOS[0], Scenario("mem", "cpu"))
    with pytest.raises(ParseError, match="oczekiwano cpu"):
        parse_report(text, expected)


def test_duplicate_scenario_in_report_raises():
    text = new_format(load("report_base_sample.txt")).replace(
        "=== SCENARIO mem memory ===", "=== SCENARIO cpu memory ==="
    )
    with pytest.raises(ParseError, match="powtórzony scenariusz"):
        parse_report(text)


def test_unknown_test_marker_raises():
    text = new_format(load("report_base_sample.txt")).replace(
        "=== SCENARIO mem memory ===", "=== SCENARIO mem disk ==="
    )
    with pytest.raises(ParseError, match="nieznany test"):
        parse_report(text)


def test_report_without_scenarios_raises():
    head = load("report_base_sample.txt").split("=== CPU ===")[0]
    with pytest.raises(ParseError, match="żadnego scenariusza"):
        parse_report(head + "=== END ===\n")


def test_extractors_match_declared_metrics():
    for test, extractors in EXTRACTORS.items():
        assert [metric for metric, _, _ in extractors] == list(TEST_METRICS[test])


def test_report_attribute_access():
    report = parse_report(load("report_base_sample.txt"))
    assert report.cpu_events_per_s == pytest.approx(1049.59)
    assert hasattr(report, "mem_mib_per_s")
    assert not hasattr(report, "nope")
