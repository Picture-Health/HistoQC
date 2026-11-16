"""Persist a HistoQC run as ``path_masks`` + ``path_qc`` rows.

This is the write seam between HistoQC and the Raven database. A HistoQC run over
one standardized WSI (``path_wsi``, produced upstream and read-only here) yields:

* mask rasters -- at minimum the usable ``TISSUE`` region, plus one ``ARTIFACT``
  row per detected artifact type -- which become :class:`~histoqc.db.models.PathMask`
  rows, and
* a QC record (status plus the long tail of HistoQC metrics) which becomes a
  single :class:`~histoqc.db.models.PathQc` row.

Both point at their input WSI by ``wsi_id`` (``path_wsi.id``). Identity the tool
cannot know -- which WSI, tenant or project -- is supplied by the caller via
:class:`WsiIdentity`. Facts that are cheaply derivable (mask checksum, dimensions,
foreground pixel count) are computed here when ``probe_masks`` is set; anything
genuinely unknown is left NULL rather than fabricated.

Writes are idempotent on the natural keys: re-running with the same inputs updates
the existing rows instead of inserting duplicates.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Union,
    runtime_checkable,
)

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from .exceptions import PersistenceError
from .models import PathMask, PathQc
from .session import Database, session_scope

logger = logging.getLogger(__name__)

# Fixed namespace so the uuid5 keys are stable across runs and machines. Shared
# verbatim with the ingest core so both write seams key into the same space.
_KEY_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://picturehealth.io/raven/path_wsi"
)

# Placeholder QA values. Raven's real QA vocabulary is owned upstream; callers
# should pass their own codes. These defaults satisfy the NOT NULL columns.
_DEFAULT_QA_CODE = "000000"
_DEFAULT_QA_DESCRIPTION = "qc_complete"

# Default filename of the usable-tissue mask HistoQC's SaveModule writes.
_TISSUE_MASK_SUFFIX = "_mask_use.png"

# HistoQC metric key holding the usable-tissue fraction (0..1), added by
# BasicModule.finalComputations. Absent on older configs -> usable_tissue_frac
# is left NULL rather than guessed.
_USABLE_FRAC_KEYS = ("percent_pixels_to_use",)

# Map HistoQC module/config keys onto the Raven ``artifact_type`` enum. The enum
# is snake_case-only by design, so the CamelCase / mixed HistoQC keys are
# normalized here (e.g. BubbleBounds -> bubble_bounds) rather than in the schema.
# Unknown keys fall back to ``other`` so a HistoQC config change never needs a
# schema migration.
_ARTIFACT_TYPE_BY_KEY = {
    "pen_markings": "pen_markings",
    "pen": "pen_markings",
    "coverslip_edge": "coverslip_edge",
    "coverslip": "coverslip_edge",
    "darktissue": "darktissue",
    "dark": "darktissue",
    "fatlike": "fatlike",
    "fat": "fatlike",
    "bubble": "bubble_bounds",
    "bubbles": "bubble_bounds",
    "bubblebounds": "bubble_bounds",
    "bubble_bounds": "bubble_bounds",
    "bubbleregionbyregion": "bubble_bounds",
    "blurry": "blurry",
    "blur": "blurry",
    "flat": "flat",
}


def artifact_type_for(histoqc_key: str) -> str:
    """Normalize a HistoQC module/config key to an ``artifact_type`` member.

    Unrecognized keys map to ``"other"`` (the schema's escape hatch), so a new
    HistoQC detector never forces an Alembic migration.
    """
    normalized = str(histoqc_key).strip().lower().replace(" ", "_")
    return _ARTIFACT_TYPE_BY_KEY.get(normalized, "other")


@dataclass
class WsiIdentity:
    """Caller-supplied identity for the input WSI a QC run belongs to.

    Attributes:
        wsi_id: ``path_wsi.id`` of the standardized image QC was run on.
        wsi_key: ``path_wsi.wsi_key`` (uuid5 seed for the mask/qc keys).
        dataset_id: Tenant-scoped dataset identifier.
        clinical_id: Clinical identifier.
        organization_id: ``organizations.id`` owning the rows.
        generated_for_project_id: Provenance project, if any.
    """

    wsi_id: int
    wsi_key: str
    dataset_id: str
    clinical_id: str
    organization_id: int
    generated_for_project_id: Optional[int] = None


@dataclass
class Provenance:
    """P8 artifact-convention provenance, stamped on every row a run writes."""

    container_image: Optional[str] = None
    clearml_task_id: Optional[str] = None
    model_version: Optional[str] = None
    config_hash: Optional[str] = None


@dataclass
class MaskArtifact:
    """One mask raster to persist as a ``path_masks`` row.

    ``mask_type`` is one of ``TISSUE``/``ARTIFACT``/``TUMOR``. For ``ARTIFACT``
    rows, ``artifact_type`` should be a HistoQC key (it is normalized to the enum
    via :func:`artifact_type_for`) or an enum member directly.
    """

    mask_type: str
    mask_uri: str
    artifact_type: Optional[str] = None
    positive_px: Optional[int] = None
    total_px: Optional[int] = None
    width_px: Optional[int] = None
    height_px: Optional[int] = None
    n_classes: Optional[int] = None
    class_counts: Optional[dict] = None
    class_labels: Optional[list] = None
    magnification: Optional[float] = None
    mask_mpp_x: Optional[float] = None
    mask_mpp_y: Optional[float] = None
    mean_confidence: Optional[float] = None

    @classmethod
    def tissue(cls, mask_uri: str, **kwargs: Any) -> "MaskArtifact":
        """Build the usable-tissue mask row."""
        return cls(mask_type="TISSUE", mask_uri=mask_uri, **kwargs)

    @classmethod
    def artifact(
        cls, mask_uri: str, histoqc_key: str, **kwargs: Any
    ) -> "MaskArtifact":
        """Build an artifact mask row, normalizing ``histoqc_key`` to the enum."""
        return cls(
            mask_type="ARTIFACT",
            mask_uri=mask_uri,
            artifact_type=artifact_type_for(histoqc_key),
            **kwargs,
        )


@dataclass
class HistoQCResult:
    """A parsed HistoQC run for a single WSI.

    Attributes:
        slide_filename: Basename of the analyzed slide.
        output_dir: The per-slide HistoQC output directory.
        masks: Mask rasters to persist (at least the TISSUE mask).
        metrics: The full HistoQC metric row, stored verbatim in ``qc_metrics``.
        warnings: HistoQC warnings for the slide.
        usable_tissue_frac: Fraction of the image that is usable tissue (0..1).
        artifact_frac: Fraction removed as artifact, if known.
        metadata_anomalies: Structured metadata-mismatch findings, if any.
        status: ``pass``/``warn``/``fail``. Derived from warnings if unset.
        mpp_source: Where the MPP came from (``metadata``/``manifest``/``absent``).
    """

    slide_filename: str
    output_dir: str
    masks: List[MaskArtifact] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    usable_tissue_frac: Optional[float] = None
    artifact_frac: Optional[float] = None
    metadata_anomalies: Optional[dict] = None
    status: Optional[str] = None
    mpp_source: str = "metadata"

    @classmethod
    def from_histoqc_state(
        cls,
        state: Mapping[str, Any],
        *,
        output_dir: Optional[str] = None,
    ) -> "HistoQCResult":
        """Build a result from a HistoQC ``BaseImage`` state mapping.

        Reads only mapping keys and the filesystem (no ``BaseImage`` import), so
        it stays decoupled from HistoQC's core. Discovers the usable-tissue mask
        (``<filename>_mask_use.png``) in the output directory; artifact masks are
        not written by the default pipeline, so callers that save them should
        append :class:`MaskArtifact` entries via :meth:`MaskArtifact.artifact`.

        Args:
            state: The per-slide HistoQC state (a ``dict`` subclass). Expected
                keys: ``filename``, ``output`` (metric names), ``warnings`` and
                ``outdir``.
            output_dir: Override for the output directory (else ``state['outdir']``).
        """
        filename = str(state.get("filename", ""))
        out_dir = output_dir or str(state.get("outdir", ""))

        # The metric row: the ordered print list HistoQC writes to results.tsv.
        output_keys = state.get("output") or []
        metrics = {
            key: _jsonable(state[key])
            for key in output_keys
            if key in state
        }

        # Drop the leading empty-string sentinel HistoQC seeds warnings with.
        warnings = [w for w in (state.get("warnings") or []) if w]

        masks: List[MaskArtifact] = []
        if filename and out_dir:
            tissue_uri = os.path.join(
                out_dir, f"{filename}{_TISSUE_MASK_SUFFIX}"
            )
            masks.append(MaskArtifact.tissue(tissue_uri))

        usable = None
        for key in _USABLE_FRAC_KEYS:
            val = _as_float(metrics.get(key))
            if val is not None:
                usable = val
                break

        return cls(
            slide_filename=filename,
            output_dir=out_dir,
            masks=masks,
            metrics=metrics,
            warnings=warnings,
            usable_tissue_frac=usable,
            status="warn" if warnings else "pass",
        )


@dataclass
class RecordedMask:
    """Identifiers of a persisted ``path_masks`` row."""

    mask_id: int
    mask_key: str
    mask_type: str
    artifact_type: Optional[str]
    created: bool


@dataclass
class RecordedQc:
    """Identifiers of the rows written by :meth:`record_qc`."""

    qc_id: int
    qc_key: str
    created_qc: bool
    masks: List[RecordedMask] = field(default_factory=list)


@runtime_checkable
class HistoQCResultStore(Protocol):
    """Contract for persisting a HistoQC run."""

    def record_qc(
        self,
        result: HistoQCResult,
        identity: WsiIdentity,
        **kwargs: object,
    ) -> RecordedQc:
        """Write the QC + mask rows and return their identifiers."""
        ...


def _uuid5(*parts: object) -> str:
    """Deterministic uuid5 over ``parts`` in the Raven key namespace."""
    return str(uuid.uuid5(_KEY_NAMESPACE, "|".join(str(p) for p in parts)))


def _sha256(path: Path) -> Optional[str]:
    """Return ``sha256:<hex>`` for ``path``, or None if it cannot be read.

    The ``sha256:`` prefix plus 64 hex chars is 71 characters, matching the
    ``*_checksum`` columns' ``String(71)`` width.
    """
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"
    except OSError:
        return None


def _jsonable(value: Any) -> Any:
    """Coerce a HistoQC metric value into something JSON-serializable."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    # numpy scalars expose .item(); fall through to str() for tuples/arrays/etc.
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (ValueError, TypeError):  # pragma: no cover - defensive
            pass
    return str(value)


def _as_float(value: Any) -> Optional[float]:
    """Best-effort float, or None if not numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _probe_mask(path: Path):
    """Return ``(width, height, positive_px, total_px)`` for a mask, best-effort.

    Loads the raster with Pillow (a HistoQC runtime dependency); foreground is
    any non-zero pixel. Returns all-None if the file cannot be read, so a missing
    mask leaves NULLs rather than raising.
    """
    try:
        import numpy as np
        from PIL import Image

        with Image.open(path) as handle:
            arr = np.asarray(handle)
        if arr.ndim == 3:
            arr = arr.any(axis=2)
        height, width = int(arr.shape[0]), int(arr.shape[1])
        total = int(arr.shape[0] * arr.shape[1])
        positive = int((arr != 0).sum())
        return width, height, positive, total
    except Exception:
        return None, None, None, None


class SqlAlchemyHistoQCStore:
    """Persists HistoQC runs into ``path_masks`` / ``path_qc``.

    Construct it with a :class:`~histoqc.db.session.Database`, a session factory,
    or an open :class:`~sqlalchemy.orm.Session`. A ``Database`` or factory manages
    the transaction per call; a passed-in ``Session`` is used as-is and its
    lifecycle (commit/rollback) is left to the caller.
    """

    def __init__(
        self,
        target: Union[Database, sessionmaker, Session],
    ) -> None:
        self._session: Optional[Session] = None
        self._factory: Optional[Union[Database, sessionmaker]] = None
        if isinstance(target, Session):
            self._session = target
        elif isinstance(target, (Database, sessionmaker)):
            self._factory = target
        else:  # pragma: no cover - guarded by type contract
            raise PersistenceError(
                "SqlAlchemyHistoQCStore expects a Database, sessionmaker or "
                "Session",
                details={"got": type(target).__name__},
            )

    def record_qc(
        self,
        result: HistoQCResult,
        identity: WsiIdentity,
        *,
        qc_task: str = "histoqc",
        task_version: Optional[str] = None,
        pipeline_version: Optional[str] = None,
        status: Optional[str] = None,
        mpp_source: Optional[str] = None,
        provenance: Optional[Provenance] = None,
        mean_confidence: Optional[float] = None,
        qa_code: str = _DEFAULT_QA_CODE,
        qa_code_description: str = _DEFAULT_QA_DESCRIPTION,
        qa_error_message: Optional[str] = None,
        make_canonical: bool = False,
        probe_masks: bool = True,
    ) -> RecordedQc:
        """Upsert the QC record and its mask rows.

        Args:
            result: The parsed :class:`HistoQCResult`.
            identity: Caller-supplied WSI/tenant identity.
            qc_task: ``path_qc.qc_task`` (default ``histoqc``).
            task_version: QC task version; folded into ``qc_key``.
            pipeline_version: HistoQC pipeline/config version.
            status: ``pass``/``warn``/``fail``. Overrides ``result.status``.
            mpp_source: Overrides ``result.mpp_source``.
            provenance: P8 provenance stamped on every row.
            mean_confidence: Overall run confidence, if any (else NULL).
            qa_code: ``qa_code`` (NOT NULL). Placeholder default; pass your own.
            qa_code_description: ``qa_code_description`` (NOT NULL).
            qa_error_message: Optional QA error message.
            make_canonical: If True, mark this the canonical QC/mask rows.
            probe_masks: If True, read each mask's dimensions/pixel counts.

        Returns:
            A :class:`RecordedQc` with the QC row's id/key and each mask's.

        Raises:
            PersistenceError: If the write fails.
        """
        provenance = provenance or Provenance()
        effective_status = status or result.status or "pass"
        effective_mpp_source = mpp_source or result.mpp_source

        with session_scope(self._factory, self._session) as session:
            recorded_masks = [
                self._upsert_mask(
                    session,
                    mask,
                    identity,
                    provenance=provenance,
                    probe_masks=probe_masks,
                    qa_code=qa_code,
                    qa_code_description=qa_code_description,
                    qa_error_message=qa_error_message,
                    make_canonical=make_canonical,
                )
                for mask in result.masks
            ]
            qc, created_qc = self._upsert_qc(
                session,
                result,
                identity,
                qc_task=qc_task,
                task_version=task_version,
                pipeline_version=pipeline_version,
                status=effective_status,
                mpp_source=effective_mpp_source,
                provenance=provenance,
                mean_confidence=mean_confidence,
                qa_code=qa_code,
                qa_code_description=qa_code_description,
                qa_error_message=qa_error_message,
                make_canonical=make_canonical,
            )
            # Flush so autoincrement ids are populated before we read them.
            session.flush()
            return RecordedQc(
                qc_id=qc.id,
                qc_key=qc.qc_key,
                created_qc=created_qc,
                masks=[
                    RecordedMask(
                        mask_id=row.id,
                        mask_key=row.mask_key,
                        mask_type=row.mask_type,
                        artifact_type=row.artifact_type,
                        created=created,
                    )
                    for row, created in recorded_masks
                ],
            )

    # -- internal helpers ---------------------------------------------------

    def _upsert_mask(
        self,
        session: Session,
        mask: MaskArtifact,
        identity: WsiIdentity,
        *,
        provenance: Provenance,
        probe_masks: bool,
        qa_code: str,
        qa_code_description: str,
        qa_error_message: Optional[str],
        make_canonical: bool,
    ) -> "tuple[PathMask, bool]":
        artifact_type = mask.artifact_type
        if mask.mask_type == "ARTIFACT" and artifact_type is not None:
            artifact_type = artifact_type_for(artifact_type)
        elif mask.mask_type != "ARTIFACT":
            # artifact_type is only meaningful for ARTIFACT rows.
            artifact_type = None

        # artifact_type folded into the seed so the N artifact rows of one WSI
        # get distinct keys (the sheet's uuid5(wsi_key, mask_type, model_version)
        # predates the one-row-per-artifact-type change and would collide).
        mask_key = _uuid5(
            identity.wsi_key,
            mask.mask_type,
            artifact_type,
            provenance.model_version,
        )

        width, height = mask.width_px, mask.height_px
        positive, total = mask.positive_px, mask.total_px
        checksum = None
        mask_path = Path(mask.mask_uri)
        if probe_masks:
            checksum = _sha256(mask_path)
            pw, ph, pp, pt = _probe_mask(mask_path)
            width = width if width is not None else pw
            height = height if height is not None else ph
            positive = positive if positive is not None else pp
            total = total if total is not None else pt

        values = {
            "wsi_id": identity.wsi_id,
            "dataset_id": identity.dataset_id,
            "clinical_id": identity.clinical_id,
            "generated_for_project_id": identity.generated_for_project_id,
            "organization_id": identity.organization_id,
            "mask_type": mask.mask_type,
            "artifact_type": artifact_type,
            "mask_uri": mask.mask_uri,
            "mask_checksum": checksum,
            "width_px": width,
            "height_px": height,
            "positive_px": positive,
            "total_px": total,
            "n_classes": mask.n_classes,
            "class_counts": mask.class_counts,
            "class_labels": mask.class_labels,
            "magnification": mask.magnification,
            "mask_mpp_x": mask.mask_mpp_x,
            "mask_mpp_y": mask.mask_mpp_y,
            "container_image": provenance.container_image,
            "clearml_task_id": provenance.clearml_task_id,
            "model_version": provenance.model_version,
            "config_hash": provenance.config_hash,
            "mean_confidence": mask.mean_confidence,
            "qa_code": qa_code,
            "qa_code_description": qa_code_description,
            "qa_error_message": qa_error_message,
        }

        existing = session.scalar(
            select(PathMask).where(PathMask.mask_key == mask_key)
        )
        if existing is not None:
            for key, value in values.items():
                setattr(existing, key, value)
            if make_canonical:
                self._set_mask_canonical(session, identity.wsi_id, existing)
            logger.info("Updated path_masks %s", mask_key)
            return existing, False

        self._demote_mask_latest(
            session, identity.wsi_id, mask.mask_type, artifact_type
        )
        row = PathMask(
            mask_key=mask_key,
            is_latest=True,
            is_canonical=False,
            **values,
        )
        session.add(row)
        if make_canonical:
            session.flush()
            self._set_mask_canonical(session, identity.wsi_id, row)
        logger.info("Inserted path_masks %s", mask_key)
        return row, True

    def _upsert_qc(
        self,
        session: Session,
        result: HistoQCResult,
        identity: WsiIdentity,
        *,
        qc_task: str,
        task_version: Optional[str],
        pipeline_version: Optional[str],
        status: str,
        mpp_source: str,
        provenance: Provenance,
        mean_confidence: Optional[float],
        qa_code: str,
        qa_code_description: str,
        qa_error_message: Optional[str],
        make_canonical: bool,
    ) -> "tuple[PathQc, bool]":
        qc_key = _uuid5(identity.wsi_key, qc_task, task_version)

        metrics = dict(result.metrics) if result.metrics else None
        if result.warnings:
            # Preserve warnings inside the metrics blob so nothing is lost.
            metrics = metrics or {}
            metrics.setdefault("warnings", list(result.warnings))

        values = {
            "wsi_id": identity.wsi_id,
            "dataset_id": identity.dataset_id,
            "clinical_id": identity.clinical_id,
            "generated_for_project_id": identity.generated_for_project_id,
            "organization_id": identity.organization_id,
            "qc_task": qc_task,
            "task_version": task_version,
            "pipeline_version": pipeline_version,
            "status": status,
            "usable_tissue_frac": result.usable_tissue_frac,
            "artifact_frac": result.artifact_frac,
            "tumor_area_px": None,  # HistoQC does not segment tumor.
            "tumor_frac": None,
            "mpp_source": mpp_source,
            "metadata_anomalies": result.metadata_anomalies,
            "qc_metrics": metrics,
            "container_image": provenance.container_image,
            "clearml_task_id": provenance.clearml_task_id,
            "model_version": provenance.model_version,
            "config_hash": provenance.config_hash,
            "mean_confidence": mean_confidence,
            "qa_code": qa_code,
            "qa_code_description": qa_code_description,
            "qa_error_message": qa_error_message,
        }

        existing = session.scalar(
            select(PathQc).where(PathQc.qc_key == qc_key)
        )
        if existing is not None:
            for key, value in values.items():
                setattr(existing, key, value)
            if make_canonical:
                self._set_qc_canonical(session, identity.wsi_id, existing)
            logger.info("Updated path_qc %s", qc_key)
            return existing, False

        self._demote_qc_latest(session, identity.wsi_id, qc_task)
        row = PathQc(
            qc_key=qc_key,
            is_latest=True,
            is_canonical=False,
            **values,
        )
        session.add(row)
        if make_canonical:
            session.flush()
            self._set_qc_canonical(session, identity.wsi_id, row)
        logger.info("Inserted path_qc %s", qc_key)
        return row, True

    @staticmethod
    def _demote_mask_latest(
        session: Session,
        wsi_id: int,
        mask_type: str,
        artifact_type: Optional[str],
    ) -> None:
        """Clear ``is_latest`` on prior masks of the same kind for this WSI."""
        session.execute(
            update(PathMask)
            .where(
                PathMask.wsi_id == wsi_id,
                PathMask.mask_type == mask_type,
                PathMask.artifact_type.is_(artifact_type)
                if artifact_type is None
                else PathMask.artifact_type == artifact_type,
                PathMask.is_latest.is_(True),
            )
            .values(is_latest=False)
        )

    @staticmethod
    def _set_mask_canonical(
        session: Session, wsi_id: int, row: PathMask
    ) -> None:
        """Make ``row`` the single canonical mask of its kind for the WSI."""
        session.execute(
            update(PathMask)
            .where(
                PathMask.wsi_id == wsi_id,
                PathMask.mask_type == row.mask_type,
                PathMask.artifact_type.is_(row.artifact_type)
                if row.artifact_type is None
                else PathMask.artifact_type == row.artifact_type,
                PathMask.id != row.id,
                PathMask.is_canonical.is_(True),
            )
            .values(is_canonical=False)
        )
        row.is_canonical = True

    @staticmethod
    def _demote_qc_latest(
        session: Session, wsi_id: int, qc_task: str
    ) -> None:
        """Clear ``is_latest`` on prior QC rows for this WSI + task."""
        session.execute(
            update(PathQc)
            .where(
                PathQc.wsi_id == wsi_id,
                PathQc.qc_task == qc_task,
                PathQc.is_latest.is_(True),
            )
            .values(is_latest=False)
        )

    @staticmethod
    def _set_qc_canonical(session: Session, wsi_id: int, row: PathQc) -> None:
        """Make ``row`` the single canonical QC record for the WSI."""
        session.execute(
            update(PathQc)
            .where(
                PathQc.wsi_id == wsi_id,
                PathQc.id != row.id,
                PathQc.is_canonical.is_(True),
            )
            .values(is_canonical=False)
        )
        row.is_canonical = True


__all__ = [
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
