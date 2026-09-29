"""SQLAlchemy models for the two tables this repo writes.

Scope is deliberately narrow. The Raven data model has a dozen ``path_*`` /
``raw_path_*`` tables, but HistoQC only ever produces two kinds of row:

* :class:`PathMask` -- the mask rasters HistoQC emits for a slide: the usable
  ``TISSUE`` region, plus one ``ARTIFACT`` row per detected artifact type.
* :class:`PathQc` -- the QC record for a HistoQC run over one WSI (status plus
  the long tail of HistoQC metrics).

The **input** is ``path_wsi`` -- the standardized image produced upstream by the
ingest core and owned by Raven's migrations. This repo does not own or create it,
so ``wsi_id`` is modelled as a plain indexed column (documented in a comment)
rather than a declared ``ForeignKey``. Same for the other external references
(``organizations``, ``projects``, ``users``). Modelling them as plain columns is
what lets ``metadata.create_all`` build a throwaway SQLite database in tests
without needing the whole Raven schema present; Raven owns the real FKs.

Column definitions -- types, widths, nullability, defaults, indexes and unique
keys -- mirror the finalized ``new_tables`` schema sheet column-for-column and in
the same order, so this file can be audited against it directly.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Double,
    Enum,
    Float,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# --- Raven enumerations (verbatim members from the new_tables sheet) --------

MASK_TYPES = ("ARTIFACT", "TISSUE", "TUMOR")

# The HistoQC artifact vocabulary. Populated only when mask_type == ARTIFACT
# (NULL otherwise, which is why the column is nullable). ``other`` is the escape
# hatch so a HistoQC config change never forces an Alembic migration. The
# HistoQC-key -> member mapping lives in repository.py (e.g. BubbleBounds ->
# bubble_bounds), keeping this enum snake_case-only.
ARTIFACT_TYPES = (
    "blurry",
    "pen_markings",
    "coverslip_edge",
    "flat",
    "bubble_bounds",
    "fatlike",
    "darktissue",
    "other",
)

QC_TASKS = ("histoqc", "manual", "metadata_audit")
QC_STATUSES = ("pass", "warn", "fail")
MPP_SOURCES = ("metadata", "manifest", "absent")


class PathMask(Base):
    """A mask raster HistoQC produced for a WSI (one row per mask/artifact)."""

    __tablename__ = "path_masks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    generated_for_project_id = Column(
        Integer, nullable=True, comment="FK projects.id (owned by Raven)"
    )
    # uuid5(wsi_key, mask_type, artifact_type, model_version): one stable key per
    # mask kind. artifact_type is folded in so the N artifact rows of a WSI --
    # added when ARTIFACT went one-row-per-type -- get distinct keys.
    mask_key = Column(String(36), nullable=False, unique=True)
    wsi_id = Column(
        Integer, nullable=False, comment="FK path_wsi.id (owned by Raven)"
    )
    dataset_id = Column(String(100), nullable=False)
    clinical_id = Column(String(100), nullable=False)
    mask_type = Column(
        Enum(*MASK_TYPES, name="mask_type_enum"), nullable=False
    )
    mask_uri = Column(String(512), nullable=False)
    mask_checksum = Column(String(71), nullable=True)
    width_px = Column(BigInteger, nullable=True)
    height_px = Column(BigInteger, nullable=True)
    positive_px = Column(
        BigInteger,
        nullable=True,
        comment="Foreground pixel count; cannot overflow.",
    )
    total_px = Column(
        BigInteger,
        nullable=True,
        comment="So fractions are computable without reading the raster.",
    )
    n_classes = Column(Integer, nullable=True)
    class_counts = Column(JSON, nullable=True)
    class_labels = Column(JSON, nullable=True)
    # --- P8 artifact convention ---
    container_image = Column(String(512), nullable=True)
    clearml_task_id = Column(String(255), nullable=True)
    model_version = Column(String(128), nullable=True)
    config_hash = Column(String(64), nullable=True)
    mean_confidence = Column(
        Double,
        nullable=True,
        comment="NULL when no model backs the mask (e.g. a threshold mask).",
    )
    qa_code = Column(String(6), nullable=False)
    qa_code_description = Column(String(250), nullable=False)
    qa_error_message = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    is_latest = Column(Boolean, nullable=False, default=True)
    is_canonical = Column(Boolean, nullable=False, default=False)
    organization_id = Column(
        Integer,
        nullable=False,
        comment="FK organizations.id (OrgScopedMixin in Raven).",
    )
    magnification = Column(
        Float,
        nullable=True,
        comment="The magnification the mask was computed at.",
    )
    mask_mpp_x = Column(Float, nullable=True)
    mask_mpp_y = Column(Float, nullable=True)
    # Populated only when mask_type == ARTIFACT; NULL otherwise.
    artifact_type = Column(
        Enum(*ARTIFACT_TYPES, name="artifact_type_enum"), nullable=True
    )

    __table_args__ = (
        Index("ix_path_masks_wsi_id", "wsi_id"),
        Index(
            "ix_path_masks_wsi_type_latest",
            "wsi_id",
            "mask_type",
            "artifact_type",
            "is_latest",
        ),
        Index("ix_path_masks_tenant", "organization_id", "dataset_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug convenience
        return f"PathMask(id={self.id!r}, mask_key={self.mask_key!r})"


class PathQc(Base):
    """The QC record for a HistoQC run over one WSI."""

    __tablename__ = "path_qc"

    id = Column(Integer, primary_key=True, autoincrement=True)
    generated_for_project_id = Column(
        Integer, nullable=True, comment="FK projects.id (owned by Raven)"
    )
    # uuid5(wsi_key, qc_task, task_version).
    qc_key = Column(String(36), nullable=False, unique=True)
    wsi_id = Column(
        Integer, nullable=False, comment="FK path_wsi.id (owned by Raven)"
    )
    dataset_id = Column(String(100), nullable=False)
    clinical_id = Column(String(100), nullable=False)
    qc_task = Column(Enum(*QC_TASKS, name="qc_task_enum"), nullable=False)
    task_version = Column(String(64), nullable=True)
    pipeline_version = Column(String(64), nullable=True)
    status = Column(Enum(*QC_STATUSES, name="qc_status_enum"), nullable=False)
    usable_tissue_frac = Column(Double, nullable=True)
    artifact_frac = Column(Double, nullable=True)
    tumor_area_px = Column(BigInteger, nullable=True)
    tumor_frac = Column(
        Double,
        nullable=True,
        comment="Stored as MEASURED; the threshold stays config (D-16).",
    )
    mpp_source = Column(
        Enum(*MPP_SOURCES, name="mpp_source_enum"),
        nullable=False,
        comment="'absent' is a real, reportable state -- never a fallback.",
    )
    metadata_anomalies = Column(JSON, nullable=True)
    qc_metrics = Column(
        JSON,
        nullable=True,
        comment="The long tail of HistoQC metrics that lack their own column.",
    )
    reviewer_user_id = Column(
        Integer, nullable=True, comment="FK users.id (owned by Raven)"
    )
    reviewed_at = Column(DateTime, nullable=True)
    signature_ref = Column(String(128), nullable=True)
    # --- P8 artifact convention ---
    container_image = Column(String(512), nullable=True)
    clearml_task_id = Column(String(255), nullable=True)
    model_version = Column(String(128), nullable=True)
    config_hash = Column(String(64), nullable=True)
    mean_confidence = Column(Double, nullable=True)
    qa_code = Column(String(6), nullable=False)
    qa_code_description = Column(String(250), nullable=False)
    qa_error_message = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    is_latest = Column(Boolean, nullable=False, default=True)
    is_canonical = Column(Boolean, nullable=False, default=False)
    organization_id = Column(
        Integer,
        nullable=False,
        comment="FK organizations.id (OrgScopedMixin in Raven).",
    )

    __table_args__ = (
        Index("ix_path_qc_wsi_id", "wsi_id"),
        Index("ix_path_qc_tenant", "organization_id", "dataset_id"),
        Index("ix_path_qc_dataset_status", "dataset_id", "status"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug convenience
        return f"PathQc(id={self.id!r}, qc_key={self.qc_key!r})"


__all__ = [
    "Base",
    "PathMask",
    "PathQc",
    "MASK_TYPES",
    "ARTIFACT_TYPES",
    "QC_TASKS",
    "QC_STATUSES",
    "MPP_SOURCES",
]
