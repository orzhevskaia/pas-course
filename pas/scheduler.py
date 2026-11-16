"""Optional scheduler: fixed replay date, no artificial invention of future real sales."""

import logging
import os
import time
from .cli import pipeline
from .config import AS_OF

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
while True:
    try:
        pipeline(AS_OF)
    except Exception:
        logging.exception("Scheduled pipeline failed; next scheduled attempt will retry")
    time.sleep(max(60, int(os.getenv("INTERVAL_SECONDS", "86400"))))
