"""Exceptions for the optional DB persistence layer.

HistoQC's core does not ship a typed-exception hierarchy, so the persistence
layer defines its own small one rather than leaking SQLAlchemy errors to callers.
Everything the ``histoqc.db`` package raises on its own derives from
:class:`HistoQCDbError`.
"""

from __future__ import annotations

from typing import Optional


class HistoQCDbError(Exception):
    """Base class for errors raised by the ``histoqc.db`` package."""

    def __init__(
        self, message: str, *, details: Optional[dict] = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class PersistenceError(HistoQCDbError):
    """A database write/connection operation failed."""


__all__ = ["HistoQCDbError", "PersistenceError"]
