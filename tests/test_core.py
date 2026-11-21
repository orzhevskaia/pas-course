import numpy as np
import pandas as pd
import pytest
import requests
from pas.quality import validate_sales, digest
from pas.ml import build_features, seasonal_naive, metrics
from pas.ingest import request_weather


def sample(**overrides):
    return dict(
        Store=1,
        Date="2015-07-31",
        DayOfWeek=5,
        Sales=100,
        Customers=10,
        Open=1,
        Promo=0,
        StateHoliday="0",
        SchoolHoliday=0,
        **overrides,
    )


def test_bad_type_is_quarantined_before_cast():
    row = sample()
    row["Sales"] = "invalid"
    valid, rejected, checks = validate_sales(pd.DataFrame([row]), {1})
    assert valid.empty and len(rejected) == 1
    assert "type_Sales" in rejected.iloc[0]._reason


def test_open_zero_sales_is_preserved():
    row = sample()
    row["Sales"] = 0
    valid, rejected, checks = validate_sales(pd.DataFrame([row]), {1})
    assert len(valid) == 1 and rejected.empty
    assert any(c["severity"] == "warning" and c["failed"] == 1 for c in checks)


def test_duplicate_resolution_is_last_source_row():
    a = sample()
    b = {**a, "Sales": 200}
    valid, rejected, _ = validate_sales(pd.DataFrame([a, b]), {1})
    assert valid.iloc[0].Sales == 200 and len(rejected) == 1


@pytest.mark.parametrize(
    "field,value",
    [("Store", 999), ("Customers", -1), ("Open", 2), ("DayOfWeek", 8), ("Date", "bad")],
)
def test_domains_references_and_dates(field, value):
    row = sample()
    row[field] = value
    valid, rejected, _ = validate_sales(pd.DataFrame([row]), {1})
    assert valid.empty and len(rejected) == 1


def history():
    dates = pd.date_range("2013-01-01", periods=600)
    f = pd.DataFrame(
        {
            "store_id": 1,
            "date": dates,
            "sales": np.arange(len(dates), dtype=float),
            "is_open": True,
            "promo": False,
            "state_holiday": "0",
            "school_holiday": False,
        }
    )
    return f, pd.DataFrame({"store_id": [1]})


def test_features_cannot_see_any_future_targets_in_42_day_window():
    frame, stores = history()
    origin = frame.date.iloc[-43]
    before = build_features(frame, stores)
    modified = frame.copy()
    modified.loc[modified.date > origin, "sales"] = 1e9
    after = build_features(modified, stores)
    pd.testing.assert_frame_equal(before.loc[frame.date > origin], after.loc[frame.date > origin])
    assert "customers" not in before and "temperature" not in before


def test_calendar_lags_do_not_skip_missing_dates():
    frame, stores = history()
    missing = frame.date.iloc[50]
    frame = frame[frame.date != missing].reset_index(drop=True)
    x = build_features(frame, stores)
    target = frame.index[frame.date == missing + pd.Timedelta(days=42)][0]
    assert pd.isna(x.loc[target, "lag_42"])


def test_baseline_does_not_read_holdout_and_closes_stores():
    frame, _ = history()
    origin = frame.date.iloc[-43]
    targets = frame[frame.date > origin].copy()
    a = seasonal_naive(frame, targets, origin)
    frame.loc[frame.date > origin, "sales"] = 1e12
    np.testing.assert_array_equal(a, seasonal_naive(frame, targets, origin))
    targets["is_open"] = False
    assert seasonal_naive(frame, targets, origin).sum() == 0


def test_zero_safe_metrics():
    m = metrics([0, 100], [10, 110])
    assert m["mae"] == 10 and m["wape"] == 0.2 and m["rmspe"] == 0.1
    assert metrics([0], [1])["rmspe"] is None


def test_hash_ignores_dictionary_key_order():
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})


def test_rate_limit_has_bounded_retries():
    class Response:
        status_code = 429
        headers = {"Retry-After": "0"}

    class Session:
        calls = 0

        def get(self, *a, **k):
            self.calls += 1
            return Response()

    s = Session()
    with pytest.raises(RuntimeError, match="4 attempts"):
        request_weather(s, {}, sleep=lambda _: None)
    assert s.calls == 4


def test_network_timeout_is_retried():
    class Session:
        calls = 0

        def get(self, *a, **k):
            self.calls += 1
            raise requests.Timeout("test")

    s = Session()
    with pytest.raises(RuntimeError):
        request_weather(s, {}, sleep=lambda _: None)
    assert s.calls == 4
