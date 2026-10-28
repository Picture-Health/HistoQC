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
