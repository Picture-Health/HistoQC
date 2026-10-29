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

## Known gap: the Docker image doesn't build (left to the production image owner)

`Dockerfile` and `.github/workflows/docker-image.yml` are upstream's and are not
how the production HistoQC image is built. Whoever owns that build needs to
handle these:

- **Python version.** The image is `python:3.8`, but `requirements.txt` has
  needed Python ≥ 3.10 since `1c21f66` (2025-05: `scikit-image>=0.25.2`;
  `numpy>=1.26.4` needs ≥ 3.9), so `pip install -r requirements.txt` fails.
- **Private dependency.** `pathology-slide-reader` installs from the private
  repo `Picture-Health/pathology-slide-reader` (a git URL), so the build needs
  GitHub read access. Don't put a token in a build arg or layer. Use a BuildKit
  secret instead, for example:
  `RUN --mount=type=secret,id=gh_token git config --global url."https://x-access-token:$(cat /run/secrets/gh_token)@github.com/".insteadOf "https://github.com/" && pip install ...`
  with `docker build --secret id=gh_token,env=GH_TOKEN .`. Alternatives: an
  internal package index, or a vendored wheel. The same applies to the
  HistoBlur image (TRD-3228).
- **Publish workflow.** `docker-image.yml` pushes to upstream's public Docker
  Hub repo (`histotools/HistoQC`) on every push to `master`. Disable it or
  point it at our registry (passing the secret above) before relying on it.
