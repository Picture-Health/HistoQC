"""Tests for the optional DB persistence layer (``histoqc.db``).

Uses a throwaway SQLite database (no MySQL/Raven required) and small synthetic
mask PNGs, so checksum/dimension/pixel probing runs without any real WSI.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

sqlalchemy = pytest.importorskip("sqlalchemy")

from histoqc.db import (  # noqa: E402
    Database,
    DatabaseConfig,
    HistoQCResult,
    MaskArtifact,
    PathMask,
    PathQc,
    PersistenceError,
    Provenance,
    SqlAlchemyHistoQCStore,
    WsiIdentity,
    artifact_type_for,
)


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'qc.db'}")
    database.create_all()
    yield database
    database.dispose()


@pytest.fixture
def make_mask(tmp_path):
    """Write a small binary mask PNG and return its path."""

    def _make(name="slide.svs_mask_use.png", *, fg_pixels=40, size=10):
        arr = np.zeros((size, size), dtype=np.uint8)
        flat = arr.reshape(-1)
        flat[:fg_pixels] = 255
        path = tmp_path / name
        Image.fromarray(arr).save(path)
        return str(path)

    return _make


def _identity(**overrides):
    base = {
        "wsi_id": 42,
        "wsi_key": "wsi-key-1",
        "dataset_id": "ds1",
        "clinical_id": "c1",
        "organization_id": 3,
    }
    base.update(overrides)
    return WsiIdentity(**base)


def _result(mask_uri, output_dir="/out/slide.svs", **overrides):
    base = {
        "slide_filename": "slide.svs",
        "output_dir": output_dir,
        "masks": [MaskArtifact.tissue(mask_uri)],
        "metrics": {"base_mag": 40.0, "percent_pixels_to_use": 0.8},
        "warnings": [],
        "usable_tissue_frac": 0.8,
        "status": "pass",
    }
    base.update(overrides)
    return HistoQCResult(**base)


# --- record_qc: masks + qc row ---------------------------------------------


def test_record_inserts_qc_and_tissue_mask(db, make_mask):
    mask_uri = make_mask(fg_pixels=40, size=10)
    store = SqlAlchemyHistoQCStore(db)

    rec = store.record_qc(_result(mask_uri), _identity())

    assert rec.created_qc
    assert len(rec.masks) == 1 and rec.masks[0].created
    assert rec.masks[0].mask_type == "TISSUE"

    with db.session() as s:
        qc = s.get(PathQc, rec.qc_id)
        mask = s.get(PathMask, rec.masks[0].mask_id)

        assert qc.wsi_id == 42
        assert qc.qc_task == "histoqc"
        assert qc.status == "pass"
        assert qc.usable_tissue_frac == 0.8
        assert qc.mpp_source == "metadata"
        assert qc.qc_metrics["base_mag"] == 40.0
        assert qc.is_latest is True and qc.is_canonical is False
        assert qc.qa_code and qc.qa_code_description

        # TISSUE mask row maps the raster, probed for size/pixels/checksum.
        assert mask.wsi_id == 42
        assert mask.mask_type == "TISSUE"
        assert mask.artifact_type is None
        assert mask.mask_uri == mask_uri
        assert mask.mask_checksum.startswith("sha256:")
        assert mask.width_px == 10 and mask.height_px == 10
        assert mask.positive_px == 40 and mask.total_px == 100
        assert mask.is_latest is True


def test_keys_are_deterministic_and_idempotent(db, make_mask):
    mask_uri = make_mask()
    store = SqlAlchemyHistoQCStore(db)

    rec1 = store.record_qc(_result(mask_uri), _identity())
    rec2 = store.record_qc(_result(mask_uri), _identity())

    assert rec2.created_qc is False
    assert rec2.masks[0].created is False
    assert rec1.qc_key == rec2.qc_key
    assert rec1.masks[0].mask_key == rec2.masks[0].mask_key
    with db.session() as s:
        assert s.query(PathQc).count() == 1
        assert s.query(PathMask).count() == 1


def test_artifact_masks_get_distinct_keys_and_normalized_types(db, make_mask):
    tissue = make_mask(name="slide.svs_mask_use.png")
    pen = make_mask(name="slide.svs_pen.png", fg_pixels=5)
    bubble = make_mask(name="slide.svs_bubble.png", fg_pixels=7)
    store = SqlAlchemyHistoQCStore(db)

    result = _result(
        tissue,
        masks=[
            MaskArtifact.tissue(tissue),
            MaskArtifact.artifact(pen, "pen_markings"),
            # HistoQC's mixed-case key must normalize to the snake_case member.
            MaskArtifact.artifact(bubble, "BubbleBounds"),
        ],
    )
    rec = store.record_qc(result, _identity())

    assert len(rec.masks) == 3
    keys = {m.mask_key for m in rec.masks}
    assert len(keys) == 3  # tissue + 2 artifacts all distinct
    with db.session() as s:
        types = {
            m.artifact_type
            for m in s.query(PathMask).filter(PathMask.mask_type == "ARTIFACT")
        }
        assert types == {"pen_markings", "bubble_bounds"}


def test_new_task_version_demotes_latest_qc(db, make_mask):
    mask_uri = make_mask()
    store = SqlAlchemyHistoQCStore(db)

    store.record_qc(_result(mask_uri), _identity(), task_version="v1")
    store.record_qc(_result(mask_uri), _identity(), task_version="v2")

    with db.session() as s:
        rows = s.query(PathQc).all()
        assert len(rows) == 2
        latest = [r for r in rows if r.is_latest]
        assert len(latest) == 1 and latest[0].task_version == "v2"


def test_make_canonical_is_single_per_wsi(db, make_mask):
    mask_uri = make_mask()
    store = SqlAlchemyHistoQCStore(db)

    store.record_qc(
        _result(mask_uri), _identity(), task_version="v1", make_canonical=True
    )
    rec2 = store.record_qc(
        _result(mask_uri), _identity(), task_version="v2", make_canonical=True
    )

    with db.session() as s:
        canonical = [r for r in s.query(PathQc).all() if r.is_canonical]
        assert len(canonical) == 1 and canonical[0].qc_key == rec2.qc_key


def test_provenance_is_stamped_on_rows(db, make_mask):
    mask_uri = make_mask()
    store = SqlAlchemyHistoQCStore(db)
    prov = Provenance(
        container_image="ecr/histoqc:1.2",
        clearml_task_id="task-abc",
        model_version="hqc-v2.1",
        config_hash="deadbeef",
    )

    rec = store.record_qc(_result(mask_uri), _identity(), provenance=prov)
    with db.session() as s:
        qc = s.get(PathQc, rec.qc_id)
        mask = s.get(PathMask, rec.masks[0].mask_id)
        assert qc.container_image == "ecr/histoqc:1.2"
        assert qc.clearml_task_id == "task-abc"
        assert mask.model_version == "hqc-v2.1"
        assert mask.config_hash == "deadbeef"


# --- HistoQCResult.from_histoqc_state ---------------------------------------


def test_from_histoqc_state_parses_metrics_and_tissue_mask(tmp_path):
    # A BaseImage-like state mapping (plain dict is enough).
    state = {
        "filename": "slide.svs",
        "outdir": str(tmp_path),
        "output": ["filename", "base_mag", "percent_pixels_to_use"],
        "base_mag": 40.0,
        "percent_pixels_to_use": 0.73,
        "warnings": ["", "Bounding Box requested but could not read"],
    }

    result = HistoQCResult.from_histoqc_state(state)

    assert result.slide_filename == "slide.svs"
    assert result.usable_tissue_frac == 0.73
    assert result.status == "warn"  # a real warning present
    assert result.warnings == ["Bounding Box requested but could not read"]
    assert result.metrics["base_mag"] == 40.0
    assert len(result.masks) == 1
    assert result.masks[0].mask_type == "TISSUE"
    assert result.masks[0].mask_uri.endswith("slide.svs_mask_use.png")


def test_from_histoqc_state_coerces_non_json_metrics(tmp_path):
    state = {
        "filename": "s.svs",
        "outdir": str(tmp_path),
        "output": ["arr", "scalar", "tup"],
        "arr": np.uint32(7),  # numpy scalar -> python int via .item()
        "scalar": 1.5,
        "tup": (500, 500),  # tuple -> str
    }
    result = HistoQCResult.from_histoqc_state(state)
    assert result.metrics["arr"] == 7
    assert result.metrics["scalar"] == 1.5
    assert isinstance(result.metrics["tup"], str)


def test_warnings_folded_into_qc_metrics(db, make_mask):
    mask_uri = make_mask()
    store = SqlAlchemyHistoQCStore(db)
    result = _result(mask_uri, warnings=["low tissue"], status="warn")

    rec = store.record_qc(result, _identity())
    with db.session() as s:
        qc = s.get(PathQc, rec.qc_id)
        assert qc.status == "warn"
        assert qc.qc_metrics["warnings"] == ["low tissue"]


# --- artifact_type_for mapping ----------------------------------------------


@pytest.mark.parametrize(
    "key,expected",
    [
        ("pen_markings", "pen_markings"),
        ("coverslip_edge", "coverslip_edge"),
        ("BubbleBounds", "bubble_bounds"),
        ("dark", "darktissue"),
        ("blur", "blurry"),
        ("flat", "flat"),
        ("tissue_fold", "other"),  # not in the current HistoQC vocabulary
        ("something_new", "other"),
    ],
)
def test_artifact_type_for(key, expected):
    assert artifact_type_for(key) == expected


# --- config / engine / error handling ---------------------------------------


def test_config_from_env_reads_url():
    cfg = DatabaseConfig.from_env({"RAVEN_DATABASE_URL": "sqlite://"})
    assert cfg.url == "sqlite://"


def test_config_from_env_prefers_raven_over_histoqc():
    cfg = DatabaseConfig.from_env(
        {
            "RAVEN_DATABASE_URL": "sqlite:///raven",
            "HISTOQC_DATABASE_URL": "sqlite:///hqc",
        }
    )
    assert cfg.url == "sqlite:///raven"


def test_config_from_env_missing_url_raises():
    with pytest.raises(PersistenceError):
        DatabaseConfig.from_env({})


def test_create_engine_bad_url_raises():
    with pytest.raises(PersistenceError):
        Database("not-a-valid-url://x")


def test_database_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAVEN_DATABASE_URL", f"sqlite:///{tmp_path / 'e.db'}")
    database = Database.from_env()
    database.create_all()
    database.dispose()


def test_store_accepts_sessionmaker(db, make_mask):
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=db.engine, future=True, expire_on_commit=False)
    store = SqlAlchemyHistoQCStore(factory)
    rec = store.record_qc(_result(make_mask()), _identity())
    assert rec.qc_id
    with db.session() as s:
        assert s.query(PathQc).count() == 1


def test_record_with_caller_managed_session(db, make_mask):
    mask_uri = make_mask()
    with db.session() as session:
        store = SqlAlchemyHistoQCStore(session)
        rec = store.record_qc(_result(mask_uri), _identity())
        assert rec.qc_id and rec.masks[0].mask_id
    with db.session() as s:
        assert s.query(PathQc).count() == 1


def test_transaction_error_is_wrapped_and_rolled_back(db, make_mask):
    mask_uri = make_mask()
    store = SqlAlchemyHistoQCStore(db)

    # qa_code is NOT NULL; passing None trips an IntegrityError on flush, which
    # the session scope rolls back and re-raises as a PersistenceError.
    with pytest.raises(PersistenceError):
        store.record_qc(_result(mask_uri), _identity(), qa_code=None)
    with db.session() as s:
        assert s.query(PathQc).count() == 0
        assert s.query(PathMask).count() == 0


def test_probe_missing_mask_leaves_nulls(db):
    store = SqlAlchemyHistoQCStore(db)
    result = _result("/no/such/mask_use.png")

    rec = store.record_qc(result, _identity())
    with db.session() as s:
        mask = s.get(PathMask, rec.masks[0].mask_id)
        assert mask.mask_checksum is None
        assert mask.width_px is None and mask.positive_px is None


def test_probe_masks_false_skips_raster_read(db, make_mask):
    mask_uri = make_mask()
    store = SqlAlchemyHistoQCStore(db)

    rec = store.record_qc(_result(mask_uri), _identity(), probe_masks=False)
    with db.session() as s:
        mask = s.get(PathMask, rec.masks[0].mask_id)
        assert mask.mask_checksum is None
        assert mask.width_px is None
