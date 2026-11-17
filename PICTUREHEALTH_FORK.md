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

- **Python 3.10.20** (`python:3.10.20-slim`, pinned by digest), the version
  the TRD-3518 goldens were made with. Upstream's `python:3.8` stopped working
  at `1c21f66` (2025-05: `scikit-image>=0.25.2`, `numpy>=1.26.4`).
- **Locked dependencies.** `requirements.txt` has version ranges only, so the
  image installs `requirements-image.lock` instead (`--require-hashes`, wheels
  only), generated from `requirements-image.in`. The versions the golden
  manifests record are pinned exactly; the rest are as resolved on the golden
  run date. `requirements.txt` and `setup.cfg` keep upstream's ranges for pip
  installs.
- **Private dependency.** `pathology-slide-reader` comes from the private repo
  `Picture-Health/pathology-slide-reader` at the commit pinned in
  `requirements.txt`, installed `--no-deps --no-build-isolation`, so pip
  builds it with the hash-checked `poetry-core` from the lock (removed after
  the build) and fetches nothing unpinned while the token is present. The
  build takes a GitHub token as a BuildKit secret
  (`--secret id=github_token,env=GITHUB_TOKEN`, see the README); git reads it from `GIT_CONFIG_*` variables inside that one `RUN`, so
  it is never written to a layer. Never pass it as a build arg. The same
  applies to the HistoBlur image (TRD-3228).
- **No publish workflow.** Upstream's `.github/workflows/docker-image.yml`
  pushed to upstream's public Docker Hub repo (`histotools/HistoQC`) on every
  push to `master` and failed on every run (the fork has no Docker Hub
  secrets: "Username and password required"; it also had no token for the
  private reader). It is deleted; don't restore it on upstream syncs. Publishing to our
  registry is a separate change.
- **`.dockerignore`** keeps the build context to the files the `Dockerfile`
  copies.
- **The golden test is opt-in** (`pytest -m integration`, see
  `tests/golden/real_chain_v1/README.md`); `pyproject.toml` deselects it by
  default.

## No direct database writes

HistoQC does not connect to the Raven database. The `path_masks` / `path_qc`
tables are owned by raven-api, and all platform data goes through raven-api
via the `raven` SDK, so its access rules, organization checks and audit log
apply. An earlier direct writer (`histoqc/db/`, the `db` extra and
`tests/test_db.py`, from TRD-3227) bypassed all three and was removed unused
in the PR #1 / #2 post-merge review. QC results will be recorded through
raven-api once it has an endpoint for pathology QC records (TRD-2841; the SDK
has none today). On upstream syncs, don't add a database client here.

HistoBlur's `histoblur/db/` was written the same way (TRD-3228) and should go
the same way.
