"""Optional real-browser smoke check. Requires playwright and an installed Chrome."""

import json
import platform
import time
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts"
for attempt in range(30):
    try:
        with urllib.request.urlopen("http://localhost:8501/_stcore/health", timeout=3) as response:
            if response.status == 200:
                break
    except OSError:
        pass
    time.sleep(1)
else:
    raise RuntimeError("Streamlit health endpoint is unavailable")
with sync_playwright() as p:
    executable = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if platform.system() == "Darwin"
        else None
    )
    browser = p.chromium.launch(headless=True, executable_path=executable)
    page = browser.new_page(viewport={"width": 1600, "height": 1100}, device_scale_factor=1)
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto("http://localhost:8501", wait_until="domcontentloaded", timeout=60000)
    page.get_by_role("heading", name="Выручка под контролем").wait_for(timeout=90000)
    page.locator('[data-testid="stPlotlyChart"]').first.wait_for(timeout=90000)
    page.get_by_text("Скачать выбранный прогноз CSV", exact=True).wait_for(timeout=120000)
    page.screenshot(path=str(OUT / "dashboard_forecast.png"), full_page=True)
    page.screenshot(path=str(OUT / "dashboard_forecast_slide.png"))
    outcomes = []
    for label, heading, name in [
        ("Контроль данных", "Можно ли доверять данным?", "dashboard_operations.png"),
        ("Происхождение данных", "От показателя до источника", "dashboard_lineage.png"),
    ]:
        page.get_by_text(label, exact=True).click()
        page.get_by_role("heading", name=heading).wait_for(timeout=90000)
        # Wait for the final page element, not just a heading while old content is stale.
        marker = (
            "Реальные ответы Open-Meteo для фиксированных"
            if label == "Контроль данных"
            else "Каталог и история обновлены."
        )
        page.get_by_text(marker, exact=False).wait_for(timeout=120000)
        page.wait_for_timeout(500)
        assert page.get_by_text("Данные пока недоступны.", exact=False).count() == 0
        page.screenshot(path=str(OUT / name), full_page=True)
        if label == "Контроль данных":
            page.screenshot(path=str(OUT / "dashboard_operations_slide.png"))
        outcomes.append({"page": label, "passed": True})
    assert not errors, errors
    (OUT / "browser_evidence.json").write_text(
        json.dumps(
            {
                "passed": True,
                "pages": [{"page": "Прогноз продаж", "passed": True}] + outcomes,
                "javascript_errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    browser.close()
print("Browser smoke passed; screenshots saved in artifacts/")
