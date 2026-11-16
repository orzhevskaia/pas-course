"""Smoke all real pages with the connected PostgreSQL (no data mocks)."""

from pathlib import Path
import pytest
from pas.db import connect


def test_all_dashboard_pages():
    try:
        conn = connect()
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('meta.assets')")
            if cur.fetchone()[0] is None:
                pytest.skip("Initialize database first")
        conn.close()
    except Exception:
        pytest.skip("Database unavailable")
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "dashboard/app.py"), default_timeout=90
    )
    app.run()
    for label in ["Прогноз продаж", "Контроль данных", "Происхождение данных", "О проекте"]:
        app.sidebar.radio[0].set_value(label).run()
        assert not app.exception, [(x.message) for x in app.exception]
        assert not app.error, [(x.value) for x in app.error]
