"""Importing this module registers every job handler with the queue."""
from .sources import ingest  # noqa: F401
from .features import jobs as _feature_jobs  # noqa: F401
from .ml import jobs as _ml_jobs  # noqa: F401
from .ml import nowcast_jobs as _nowcast_jobs  # noqa: F401
from .simulation import jobs as _sim_jobs  # noqa: F401
