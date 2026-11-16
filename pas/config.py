import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DATA = ROOT / "data"
ARTIFACTS = ROOT / "artifacts"
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://pas:pas_local_demo@localhost:55432/rossmann_pas"
)
AS_OF = os.getenv("AS_OF", "2015-07-31")
HORIZON = int(os.getenv("HORIZON", "42"))
SEED = int(os.getenv("SEED", "42"))
