"""Runtime settings. Everything is environment-driven so the same code runs locally, in Docker and in tests."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Pilot municipalities (IBGE 7-digit codes). Rio de Janeiro, São Paulo, Belo Horizonte by default;
# HP_PILOT=capitals expands to the 27 state capitals; HP_PILOT=3304557,3550308 takes an explicit list.
PILOT_DEFAULT = ["3304557", "3550308", "3106200"]
CAPITALS = [
    "1100205", "1302603", "1200401", "5002704", "1600303", "5300108", "1400100", "5103403", "1721000",
    "3550308", "2211001", "3304557", "1501402", "5208707", "2927408", "4205407", "2111300", "2704302",
    "4314902", "4106902", "3106200", "2304400", "2611606", "2507507", "2800308", "2408102", "3205309",
]


def _pilot() -> list[str]:
    raw = os.environ.get("HP_PILOT", "").strip()
    if not raw:
        return list(PILOT_DEFAULT)
    if raw == "capitals":
        return list(CAPITALS)
    return [c.strip().zfill(7) for c in raw.split(",") if c.strip()]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    artifacts_dir: Path
    db_path: Path
    env: str
    workspace: str
    pilot: list[str] = field(default_factory=list)
    offline: bool = False
    worker_concurrency: int = 2
    job_timeout_s: int = 1800
    http_timeout_s: float = 30.0
    max_download_bytes: int = 512 * 1024 * 1024
    approved_hosts: tuple[str, ...] = (
        "info.dengue.mat.br",
        "archive-api.open-meteo.com",
        "api.open-meteo.com",
        "date.nager.at",
        "apidadosabertos.saude.gov.br",
        "raw.githubusercontent.com",
        "github.com",
        "objects.githubusercontent.com",
        "www.geoboundaries.org",
        "s3.sa-east-1.amazonaws.com",
    )
    secret_key: str = "dev-only-change-me"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def silver_dir(self) -> Path:
        return self.data_dir / "silver"

    @property
    def gold_dir(self) -> Path:
        return self.data_dir / "gold"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = Path(os.environ.get("HP_DATA_DIR", ROOT / "data")).resolve()
    artifacts = Path(os.environ.get("HP_ARTIFACTS_DIR", ROOT / "artifacts")).resolve()
    db_path = Path(os.environ.get("HP_DB", data_dir / "healthpulse.db")).resolve()
    s = Settings(
        data_dir=data_dir,
        artifacts_dir=artifacts,
        db_path=db_path,
        env=os.environ.get("HP_ENV", "local"),
        workspace=os.environ.get("HP_WORKSPACE", "pilot_br"),
        pilot=_pilot(),
        offline=os.environ.get("HP_OFFLINE", "0") == "1",
        worker_concurrency=int(os.environ.get("HP_WORKERS", "2")),
        job_timeout_s=int(os.environ.get("HP_JOB_TIMEOUT", "1800")),
        secret_key=os.environ.get("HP_SECRET_KEY", "dev-only-change-me"),
    )
    for p in (s.data_dir, s.artifacts_dir, s.raw_dir, s.silver_dir, s.gold_dir, s.uploads_dir):
        p.mkdir(parents=True, exist_ok=True)
    return s


def reset_settings_cache() -> None:
    get_settings.cache_clear()
