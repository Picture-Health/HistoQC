# Picture Health fork: changes to carry on upstream syncs

This fork (`Picture-Health/HistoQC`) diverges from `choosehappy/HistoQC` in the
ways below. Re-apply or re-check each one when merging upstream.

## Slides are read with pathology-slide-reader, not OpenSlide (TRD-3227)

OpenSlide is LGPL 2.1 and is being removed from the pathology pipeline
(TRD-3096). HistoQC only reads standardized slides (`path_wsi`, written by
`pathology-slide-ingest`), which `pathology-slide-reader` (tifffile-based,
OpenSlide-compatible API) reads the same way.

- `histoqc/import_wrapper/openslide.py` exposes the reader under OpenSlide's
  names: `openslide.OpenSlide`, the `PROPERTY_NAME_*` constants, and
  `OpenSlideError` / `OpenSlideUnsupportedFormatError` (also under
  `openslide.lowlevel`). Every module imports `openslide` from here.
- `TileExtractionModule.py` imported `openslide` directly; it now uses the
  wrapper.
- `openslide-python` is dropped from `requirements.txt`, `setup.cfg` and
  `environment.devenv.yml`; `libopenslide0` and upstream's Windows DLLs
  (`bin/`) are kept out of the Docker image.

On upstream syncs: any new `import openslide` in upstream code must go through
the wrapper, and any OpenSlide API it starts using must exist in
`pathology-slide-reader`. `grep -rn "import openslide" histoqc/` should only
show the wrapper.

## The Docker image (TRD-3554)

`Dockerfile` is ours now: it builds the image the goldens are checked against
(`tests/golden/real_chain_v1/README.md`). On upstream syncs, keep ours.

- **Python 3.10.20** (`python:3.10.20-slim`), the version the TRD-3518 goldens
  were made with. Upstream's `python:3.8` stopped working at `1c21f66`
  (2025-05: `scikit-image>=0.25.2`, `numpy>=1.26.4`).
- **Locked dependencies.** `requirements.txt` has version ranges only, so the
  image installs `requirements-image.lock` instead (`--require-hashes`, wheels
  only), generated from `requirements-image.in`. The versions the golden
  manifests record are pinned exactly; the rest are as resolved on the golden
  run date. `requirements.txt` and `setup.cfg` keep upstream's ranges for pip
  installs.
- **Private dependency.** `pathology-slide-reader` comes from the private repo
  `Picture-Health/pathology-slide-reader` at the commit pinned in
  `requirements.txt`, installed `--no-deps`. The build takes a GitHub token as
  a BuildKit secret (`--secret id=github_token,env=GITHUB_TOKEN`, see the
  README); git reads it from `GIT_CONFIG_*` variables inside that one `RUN`, so
  it is never written to a layer. Never pass it as a build arg. The same
  applies to the HistoBlur image (TRD-3228).
- **Publish workflow (still open).** `.github/workflows/docker-image.yml` is
  upstream's: it pushes to upstream's public Docker Hub repo
  (`histotools/HistoQC`) on every push to `master` and passes no secret, so it
  can't build this image. Disable it or point it at our registry (with the
  secret) before relying on it.

## Known gap: DB persistence layer (`histoqc/db/`), deferred until the Raven data-record API is final

Found in pre-push review (2026-10-29). Nothing in the HistoQC pipeline calls
`record_qc` yet, so none of this affects production runs today. Revisit when
the Raven database API for `path_masks` / `path_qc` records is finalized.

- **Reruns overwrite records in place.** `qc_key = uuid5(wsi_key, qc_task,
  task_version)` and `mask_key = uuid5(wsi_key, mask_type, artifact_type,
  model_version)` (from the `new_tables` schema sheet) leave out
  `config_hash` and `pipeline_version`, and `task_version` defaults to `None`.
  So a rerun with a different config replaces `status`, `qc_metrics`,
  `config_hash`, `pipeline_version`, `container_image` and `clearml_task_id`
  on the existing row, with no history kept.
  - A row with `reviewer_user_id` / `signature_ref` set keeps its signature
    while the data under it changes, so the record no longer matches what was
    signed (design-control traceability).
  - The update path never sets `is_latest` back to True: after
    v1 → v2 → rerun v1, the v1 row has fresh data but `is_latest=False`, and
    v2 stays "latest".
  - To decide with the schema owner: one row per run (keys include the
    config/pipeline version, or insert-and-demote) vs. idempotent overwrite.
    `tests/test_db.py::test_keys_are_deterministic_and_idempotent` asserts
    the current overwrite behaviour.
- **No tenant check on update.** `_upsert_mask` / `_upsert_qc` find the
  existing row by key alone and then overwrite `organization_id`, `wsi_id`,
  `dataset_id` and `clinical_id` with the caller's identity. A wrong or
  reused `wsi_key` would silently move another organization's row. Fix
  regardless of the key decision: refuse the update when `organization_id` or
  `wsi_id` differs, and leave the identity columns out of the update set.
- **Open design questions:** `_set_qc_canonical` clears `is_canonical`
  across all `qc_task` values for a WSI, while `is_latest` is per task;
  `mask_uri` stores a local filesystem path.

HistoBlur's `histoblur/db/` was written the same way (TRD-3228); check it
for the same issues.
