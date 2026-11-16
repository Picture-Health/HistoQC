"""Engine and session management.

A thin wrapper around SQLAlchemy's engine/session so callers get a transactional
``with`` block without repeating commit/rollback/close boilerplate. Importing
this module requires SQLAlchemy (the optional ``db`` extra); the rest of the
package stays importable without it.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, Optional, Union

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import DatabaseConfig
from .exceptions import PersistenceError
from .models import Base

logger = logging.getLogger(__name__)


def create_engine_from_config(config: DatabaseConfig) -> Engine:
    """Build a SQLAlchemy :class:`Engine` from a :class:`DatabaseConfig`.

    ``hide_parameters`` keeps bound values (clinical IDs, slide filenames, the
    QC metrics JSON with the slide's ImageDescription) out of SQLAlchemy's
    error messages, so a failed write can't leak them into logs.
    """
    try:
        return create_engine(
            config.url,
            echo=config.echo,
            pool_pre_ping=config.pool_pre_ping,
            hide_parameters=True,
            future=True,
        )
    except Exception as exc:  # e.g. an unparseable/unsupported URL
        # Only the type: the message can echo the URL and its credentials.
        raise PersistenceError(
            "Failed to create database engine",
            details={"error_type": type(exc).__name__},
        ) from exc


class Database:
    """Owns an engine and a session factory for the ``path_masks``/``path_qc``
    tables.

    Construct it from a :class:`DatabaseConfig`, a URL string, or an existing
    :class:`~sqlalchemy.engine.Engine`. Use :meth:`session` for a transactional
    scope. :meth:`create_all` creates only the two tables this repo owns and is
    intended for tests and local development -- production schema is owned by
    Raven's migrations, so never call it against the real database.
    """

    def __init__(self, source: Union[DatabaseConfig, Engine, str]) -> None:
        if isinstance(source, Engine):
            self.engine = source
        elif isinstance(source, DatabaseConfig):
            self.engine = create_engine_from_config(source)
        elif isinstance(source, str):
            self.engine = create_engine_from_config(DatabaseConfig(url=source))
        else:  # pragma: no cover - guarded by type contract
            raise PersistenceError(
                "Database expects a DatabaseConfig, Engine or URL string",
                details={"got": type(source).__name__},
            )
        self._sessionmaker = sessionmaker(
            bind=self.engine, expire_on_commit=False, future=True
        )

    @classmethod
    def from_env(cls) -> "Database":
        """Build a :class:`Database` from environment variables."""
        return cls(DatabaseConfig.from_env())

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Yield a session, committing on success and rolling back on error."""
        session = self._sessionmaker()
        try:
            yield session
            session.commit()
        except Exception as exc:
            session.rollback()
            if isinstance(exc, PersistenceError):
                raise
            # Only the type: the message can carry row values (e.g. from the
            # database driver), which may identify a patient.
            raise PersistenceError(
                "Database transaction failed and was rolled back",
                details={"error_type": type(exc).__name__},
            ) from exc
        finally:
            session.close()

    def create_all(self) -> None:
        """Create the two owned tables. Tests/dev only -- not for production."""
        logger.warning(
            "create_all(): creating path_masks/path_qc directly. "
            "Production schema is owned by Raven migrations."
        )
        Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        """Dispose of the engine's connection pool."""
        self.engine.dispose()


@contextmanager
def session_scope(
    factory: Union[Database, sessionmaker], _session: Optional[Session] = None
) -> Iterator[Session]:
    """Transactional scope helper usable with a :class:`Database` or factory.

    If ``_session`` is provided it is yielded as-is (the caller owns its
    lifecycle); otherwise a new session is opened, committed on success and
    rolled back on error. This lets the repository accept a caller-managed
    session or manage one itself.
    """
    if _session is not None:
        yield _session
        return
    if isinstance(factory, Database):
        with factory.session() as session:
            yield session
        return
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = ["Database", "create_engine_from_config", "session_scope"]
