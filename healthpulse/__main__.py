"""`python -m healthpulse` behaves like `python server.py`."""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parent.parent / "server.py"), run_name="__main__")
