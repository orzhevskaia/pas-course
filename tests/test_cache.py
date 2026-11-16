import json
from pas.ingest import cached_weather


def test_larger_cache_supports_incremental_replay_without_inventing_provenance(tmp_path):
    params = {
        "latitude": 1.0,
        "longitude": 2.0,
        "daily": "temperature_2m_mean,precipitation_sum",
        "timezone": "Europe/Berlin",
        "start_date": "2015-01-01",
        "end_date": "2015-01-03",
    }
    response = {
        "daily": {
            "time": ["2015-01-01", "2015-01-02", "2015-01-03"],
            "temperature_2m_mean": [1, 2, 3],
            "precipitation_sum": [0, 0, 1],
        }
    }
    path = tmp_path / "Demo_2015-01-01_2015-01-03.json"
    path.write_text(json.dumps({"request": params, "response": response}))
    before = path.read_bytes()
    sliced, source = cached_weather(tmp_path, "Demo", {**params, "start_date": "2015-01-02"})
    assert sliced["daily"]["time"] == ["2015-01-02", "2015-01-03"]
    assert sliced["daily"]["temperature_2m_mean"] == [2, 3]
    assert path.read_bytes() == before
    assert source == path.name
