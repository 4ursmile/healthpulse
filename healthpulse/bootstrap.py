"""Idempotent initialisation: migrations, demo accounts, source catalog, built-in feature definitions."""
from __future__ import annotations


def init() -> None:
    from .core import db
    from .core.security import seed_users
    from .sources.catalog import seed_sources

    db.migrate()
    seed_users()
    seed_sources()
    try:
        from .features.registry import seed_features
        seed_features()
    except ImportError:
        pass
