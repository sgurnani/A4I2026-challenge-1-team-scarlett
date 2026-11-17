import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

for p in (ROOT / "agent", ROOT / "scripts", ROOT / "toolbox", ROOT / "tests", ROOT / "web" / "backend", ROOT / "api"):
    sys.path.insert(0, str(p))
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-project")
os.environ.setdefault("TOOLBOX_URL", "http://127.0.0.1:5000")
sys.path.insert(0, str(ROOT / "client"))
os.environ.setdefault("WEB_STORE", "memory")
