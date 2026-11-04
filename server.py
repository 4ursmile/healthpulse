#!/usr/bin/env python3
"""HealthPulse one-command start:  python server.py

Works from a fresh checkout with any Python >= 3.11:
  1. creates .venv and installs the package if it is missing (uses `uv` when available, else venv + pip);
  2. loads .env (if present) and initialises the database;
  3. on first run, downloads the real public data and runs the pipeline (`hp demo`), unless --no-data;
  4. starts the API + worker and opens the dashboard in your browser.

Options:
  --port 8080          port to serve on (default 8080, or $PORT)
  --host 127.0.0.1     bind address
  --no-data            skip the first-run download (the UI shows empty states)
  --fetch-missing      also ingest every available source that has never been ingested (SRAG, WorldPop, Zenodo…)
  --full               first run downloads everything, including the large optional datasets (~1 GB)
  --no-browser         do not open a browser tab
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VPY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQUIRED = ("polars", "fastapi", "uvicorn", "sklearn", "scipy", "tifffile", "imagecodecs", "multipart")


def say(msg: str) -> None:
    print(f"\033[1;36m›\033[0m {msg}", flush=True)


def _deps_ok(py: Path) -> bool:
    code = "import importlib.util as u,sys; sys.exit(0 if all(u.find_spec(m) for m in %r) else 1)" % (REQUIRED,)
    return subprocess.run([str(py), "-c", code], cwd=ROOT).returncode == 0


def ensure_env() -> None:
    """Re-exec inside .venv with dependencies installed."""
    if Path(sys.prefix).resolve() == VENV.resolve() and _deps_ok(Path(sys.executable)):
        return
    if sys.version_info < (3, 11) and not shutil.which("uv"):
        sys.exit("HealthPulse needs Python 3.11+ (or `uv` installed, which fetches one).")
    uv = shutil.which("uv")
    if not VPY.exists():
        say("creating virtual environment .venv")
        if uv:
            subprocess.check_call([uv, "venv", "-p", "3.12", str(VENV)], cwd=ROOT)
        else:
            subprocess.check_call([sys.executable, "-m", "venv", str(VENV)], cwd=ROOT)
    if not _deps_ok(VPY):
        say("installing dependencies (first run only)")
        if uv:
            subprocess.check_call([uv, "pip", "install", "--python", str(VPY), "-e", ".[dev]", "python-multipart"], cwd=ROOT)
        else:
            subprocess.check_call([str(VPY), "-m", "pip", "install", "-q", "--upgrade", "pip"], cwd=ROOT)
            subprocess.check_call([str(VPY), "-m", "pip", "install", "-q", "-e", ".[dev]", "python-multipart"], cwd=ROOT)
    os.execv(str(VPY), [str(VPY), str(Path(__file__).resolve()), *sys.argv[1:]])


def load_dotenv() -> None:
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def has_data() -> bool:
    from healthpulse.core import db
    r = db.q1("SELECT COUNT(*) n FROM source_snapshot WHERE published=1 AND silver_table='disease_weekly'")
    return bool(r and r["n"])


def missing_sources() -> list[str]:
    from healthpulse.core import db
    from healthpulse.sources.connectors import CONNECTORS
    rows = db.q("SELECT s.source_id FROM source_registry s WHERE s.enabled=1 AND s.status IN ('available','healthy','stale') "
                "AND NOT EXISTS (SELECT 1 FROM source_snapshot p WHERE p.source_id=s.source_id AND p.published=1)")
    skip = {"synthetic_fixture", "geoboundaries", "meta_mobility"}
    return [r["source_id"] for r in rows if r["source_id"] in CONNECTORS and r["source_id"] not in skip]


def wait_and_open(url: str) -> None:
    import urllib.request
    for _ in range(120):
        try:
            urllib.request.urlopen(url + "/api/v1/health", timeout=1)
            webbrowser.open(url)
            return
        except Exception:
            time.sleep(0.5)


def main() -> None:
    ap = argparse.ArgumentParser(description="Start HealthPulse (API + worker + dashboard).")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-data", action="store_true")
    ap.add_argument("--fetch-missing", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    ensure_env()
    os.chdir(ROOT)
    load_dotenv()
    sys.path.insert(0, str(ROOT))
    from healthpulse import cli

    cli._init()
    if not a.no_data and not has_data():
        say("first run: downloading real public data and running the pipeline (≈5 min, needs internet)")
        cli.main(["demo"] + (["--full"] if a.full else []))
    from healthpulse.vietnam import data as vn
    if not a.no_data and not vn.is_built():
        say("first run for Viet Nam: downloading provinces, dengue history, weather (≈2 min)")
        try:
            cli.main(["vn-fetch"])
        except Exception as e:  # the Brazil app still starts; the Vietnam screens show how to fetch
            say(f"Viet Nam fetch failed: {e}")
    if a.fetch_missing:
        todo = missing_sources()
        say(f"fetching never-ingested sources: {', '.join(todo) or 'none'}")
        if todo:
            cli.main(["ingest", *todo])

    url = f"http://{a.host}:{a.port}"
    if not a.no_browser:
        threading.Thread(target=wait_and_open, args=(url,), daemon=True).start()
    say(f"HealthPulse → {url}   (sign in at {url}/login · Ctrl-C to stop)")
    cli.main(["serve", "--host", a.host, "--port", str(a.port)])


if __name__ == "__main__":
    main()
