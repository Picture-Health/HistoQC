"""OpenSlide's API for HistoQC, backed by ``pathology-slide-reader``.

HistoQC no longer uses OpenSlide (LGPL) at runtime (TRD-3227): slides are read
with ``slide_reader.SlideReader``, a tifffile-based reader that matches
OpenSlide on the pipeline's standardized slides (``pathology-slide-ingest``
output) and on Aperio SVS. Modules keep importing ``openslide`` from here and
using OpenSlide's names:

- ``openslide.OpenSlide(path)``;
- ``openslide.PROPERTY_NAME_*`` (same keys, so ``properties`` lookups are
  unchanged);
- ``openslide.OpenSlideError`` / ``openslide.OpenSlideUnsupportedFormatError``,
  also as ``openslide.lowlevel.*``.
"""
from types import SimpleNamespace

import slide_reader

_errors = {
    "OpenSlideError": slide_reader.SlideReaderError,
    "OpenSlideUnsupportedFormatError": slide_reader.UnsupportedFormatError,
}

openslide = SimpleNamespace(
    OpenSlide=slide_reader.SlideReader,
    lowlevel=SimpleNamespace(**_errors),
    **_errors,
    **{
        name: getattr(slide_reader, name)
        for name in slide_reader.__all__
        if name.startswith("PROPERTY_NAME_")
    },
)
