"""Point-in-time correctness (spec §19.3, acceptance §24.3 scenarios 2, 3, 4)."""
from datetime import date, datetime, timedelta, timezone

import polars as pl
import pytest

from healthpulse.core.util import epi_week, epi_week_start, week_from_code
from healthpulse.features import compute, pit
from healthpulse.features import registry as freg
from healthpulse.sources import fixtures, silver

from .conftest import run

UTC = timezone.utc


def test_epi_weeks_start_on_sunday():
    assert epi_week_start(date(2025, 1, 1)) == date(2024, 12, 29)
    assert epi_week(date(2025, 1, 1)) == (2025, 1)
    assert epi_week(date(2021, 1, 2)) == (2020, 53)  # Jan 1 2021 is a Friday: belongs to 2020's last week
    assert week_from_code(202503) == date(2025, 1, 12)


def test_lookup_never_reads_after_issue(fixture_data):
    df = silver.all_vintages("disease_weekly")
    c = pit.candidates(df, ["municipality_id"], "week_start", ["observed_cases"], "publication_lag")
    sp = compute.build_spine(["3304557"], fixture_data["start"] + timedelta(weeks=10), fixture_data["end"]).with_columns(
        (pl.col("issue_week") - pl.duration(weeks=1)).alias("_ev"))
    out = pit.lookup(sp, c, keys=["municipality_id"], spine_event="_ev", event_col="week_start", value_cols=["observed_cases"])
    assert out.height == sp.height
    assert out.filter(pl.col("known_at") > pl.col("issue_at")).height == 0
    # a zero-lag read of reanalysis-like data must come back null rather than leak
    sp0 = sp.with_columns(pl.col("issue_week").alias("_ev"))
    out0 = pit.lookup(sp0, c, keys=["municipality_id"], spine_event="_ev", event_col="week_start", value_cols=["observed_cases"])
    assert out0["observed_cases"].null_count() == out0.height


def test_revised_vintage_is_not_used_for_earlier_issue(fixture_data):
    """Scenario 2: a later InfoDengue-style correction must not appear in an earlier issue's row."""
    wk = fixture_data["end"] - timedelta(weeks=30)
    orig = silver.latest("disease_weekly").filter((pl.col("municipality_id") == "3304557") & (pl.col("week_start") == wk))["observed_cases"][0]
    later = datetime.now(UTC) + timedelta(days=30)
    fixtures.publish_revision(wk, "3304557", orig + 999, later)
    issue = datetime.now(UTC) + timedelta(days=1)
    asof = silver.as_of("disease_weekly", issue, "vintage")
    v = asof.filter((pl.col("municipality_id") == "3304557") & (pl.col("week_start") == wk))["observed_cases"][0]
    assert v == orig
    asof2 = silver.as_of("disease_weekly", later + timedelta(seconds=1), "vintage")
    assert asof2.filter((pl.col("municipality_id") == "3304557") & (pl.col("week_start") == wk))["observed_cases"][0] == orig + 999


def test_leaky_definition_is_blocked(fixture_data):
    """Scenario 4: a feature whose inputs are released after the issue instant is rejected by the validator."""
    d = freg.get_def("rain_sum_1w_lag1")
    res = freg.validate_def(d)
    assert res["status"] == "leak_blocked"
    pit_check = next(c for c in res["checks"] if c["check"] == "pit_available_at_le_issue_at")
    assert not pit_check["passed"] and pit_check["violations"] > 0
    safe = freg.validate_def(freg.get_def("rain_sum_4w"))
    assert safe["status"] == "pass"


def test_dataset_builder_rejects_unsafe_feature_set(fixture_data):
    fs = freg.create_feature_set("leaky_set", "dengue", ["cases_lag_1w", "rain_sum_1w_lag1"], "contains a leaky draft", "u_scientist")
    r = run("dataset_build", {"feature_set_id": fs["feature_set_id"], "target": "dengue_reported_cases"})
    assert r["status"] == "failed" and "not PIT-validated" in r["error"]


def test_fanout_join_hard_fails():
    """Scenario 3: facility-grain rows joined to municipality-week without aggregation must not multiply counts."""
    left = pl.DataFrame({"municipality_id": ["1", "2"], "cases": [10, 20]})
    right = pl.DataFrame({"municipality_id": ["1", "1", "2"], "beds": [5, 7, 3]})
    with pytest.raises(pit.CardinalityError):
        pit.safe_join(left, right, on=["municipality_id"])


def test_missing_source_blocks_mobility_feature(fixture_data):
    """Scenario 1: no authorised OD data -> mobility feature blocked, nothing fabricated."""
    res = freg.validate_def(freg.get_def("commuter_pressure_lag_1w"))
    assert res["status"] == "source_missing"
    fs = freg.get_feature_set("br_dengue")
    assert "commuter_pressure_lag_1w" in freg.readiness(fs)["blocked"]


def test_dataset_is_pit_audited(dataset):
    from healthpulse.features.jobs import dataset_frame, get_dataset
    d = get_dataset(dataset)
    df = dataset_frame(dataset)
    assert d["pit_status"] == "PIT_UNVERIFIED"  # publication-lag approximation is declared, never hidden
    assert df.filter(pl.col("max_input_available_at") > pl.col("issue_at")).height == 0
    assert df.filter(pl.col("target_week") <= pl.col("anchor_week")).height == 0
    assert df.select(["municipality_id", "issue_week", "horizon"]).unique().height == df.height
    assert "commuter_pressure_lag_1w" not in df.columns
