"""Connection configuration for the Raven database.

The core never hard-codes the ``raven`` database name (it is not finalized): the
only connection input is a SQLAlchemy URL, supplied directly or via environment
variable. This mirrors the ingest core's ``db`` layer so the two write seams stay
configured the same way.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

from .exceptions import PersistenceError

# Read in this order; first one set wins. RAVEN_* is the shared name across the
# pipeline; HISTOQC_* lets this component point at a different database if needed.
_URL_ENV_VARS = ("RAVEN_DATABASE_URL", "HISTOQC_DATABASE_URL")


@dataclass(frozen=True)
class DatabaseConfig:
    """Settings for connecting to the Raven database.

    Attributes:
        url: A full SQLAlchemy URL (e.g. ``mysql+pymysql://user:pw@host/db``).
            The database name lives in the URL; it is never hard-coded.
        echo: If True, SQLAlchemy logs every statement (debugging only).
        pool_pre_ping: If True, test connections for liveness before use.
    """

    url: str
    echo: bool = False
    pool_pre_ping: bool = True

    @classmethod
    def from_env(
        cls, environ: Optional[Mapping[str, str]] = None
    ) -> "DatabaseConfig":
        """Build a config from environment variables.

        Args:
            environ: Mapping to read from; defaults to :data:`os.environ`.

        Returns:
            A :class:`DatabaseConfig` built from the first URL env var set.

        Raises:
            PersistenceError: If no connection URL env var is set.
        """
        env = os.environ if environ is None else environ
        for name in _URL_ENV_VARS:
            url = env.get(name)
            if url:
                return cls(url=url)
        raise PersistenceError(
            "No database URL configured",
            details={"checked": list(_URL_ENV_VARS)},
        )


__all__ = ["DatabaseConfig"]
