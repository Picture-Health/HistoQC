"""Optional persistence of HistoQC runs into the Raven database.

This subpackage is the write seam between HistoQC and Raven. A run over one
standardized WSI (``path_wsi``, the read-only input) is recorded as:

* ``path_masks`` rows -- the ``TISSUE`` usable region and one ``ARTIFACT`` row per
  detected artifact type, and
* a ``path_qc`` row -- the run's status plus the long tail of HistoQC metrics.

SQLAlchemy is an **optional** dependency (the ``db`` extra), imported lazily like
HistoQC's other heavy deps. Importing :data:`DatabaseConfig` and the exceptions is
free; touching the models/session/repository requires SQLAlchemy installed.

Example::

    from histoqc.db import Database, SqlAlchemyHistoQCStore, WsiIdentity
    from histoqc.db import HistoQCResult

    db = Database.from_env()               # reads RAVEN_DATABASE_URL
    store = SqlAlchemyHistoQCStore(db)
    result = HistoQCResult.from_histoqc_state(state)   # state = BaseImage dict
    store.record_qc(result, WsiIdentity(
        wsi_id=42, wsi_key="...", dataset_id="ds1",
        clinical_id="c1", organization_id=3,
    ))
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .config import DatabaseConfig
from .exceptions import HistoQCDbError, PersistenceError

if TYPE_CHECKING:  # import for type-checkers only; keeps runtime import lazy
    from .models import (  # noqa: F401
        ARTIFACT_TYPES,
        MASK_TYPES,
        MPP_SOURCES,
        PathMask,
        PathQc,
        QC_STATUSES,
        QC_TASKS,
    )
    from .repository import (  # noqa: F401
        HistoQCResult,
        HistoQCResultStore,
        MaskArtifact,
        Provenance,
        RecordedMask,
        RecordedQc,
        SqlAlchemyHistoQCStore,
        WsiIdentity,
        artifact_type_for,
    )
    from .session import (  # noqa: F401
        Database,
        create_engine_from_config,
        session_scope,
    )

# Where each lazily-exported symbol lives, resolved on first access.
_LAZY = {
    "Database": "session",
    "create_engine_from_config": "session",
    "session_scope": "session",
    "Base": "models",
    "PathMask": "models",
    "PathQc": "models",
    "MASK_TYPES": "models",
    "ARTIFACT_TYPES": "models",
    "QC_TASKS": "models",
    "QC_STATUSES": "models",
    "MPP_SOURCES": "models",
    "WsiIdentity": "repository",
    "Provenance": "repository",
    "MaskArtifact": "repository",
    "HistoQCResult": "repository",
    "RecordedMask": "repository",
    "RecordedQc": "repository",
    "HistoQCResultStore": "repository",
    "SqlAlchemyHistoQCStore": "repository",
    "artifact_type_for": "repository",
}


def __getattr__(name: str) -> Any:
    """Lazily import DB symbols so ``import histoqc.db`` needs no SQLAlchemy."""
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        from importlib import import_module

        mod = import_module(f".{module}", __name__)
    except ImportError as exc:  # pragma: no cover - depends on env
        raise ImportError(
            f"{name} requires the optional 'db' extra "
            "(pip install histoqc[db])."
        ) from exc
    return getattr(mod, name)


__all__ = [
    "DatabaseConfig",
    "HistoQCDbError",
    "PersistenceError",
    "Database",
    "create_engine_from_config",
    "session_scope",
    "Base",
    "PathMask",
    "PathQc",
    "MASK_TYPES",
    "ARTIFACT_TYPES",
    "QC_TASKS",
    "QC_STATUSES",
    "MPP_SOURCES",
    "WsiIdentity",
    "Provenance",
    "MaskArtifact",
    "HistoQCResult",
    "RecordedMask",
    "RecordedQc",
    "HistoQCResultStore",
    "SqlAlchemyHistoQCStore",
    "artifact_type_for",
]
