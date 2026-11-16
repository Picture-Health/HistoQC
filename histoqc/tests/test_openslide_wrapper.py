"""The OpenSlide names HistoQC uses resolve to pathology-slide-reader."""
import slide_reader

from histoqc.import_wrapper.openslide import openslide


def test_openslide_names_map_to_the_reader():
    assert openslide.OpenSlide is slide_reader.SlideReader
    assert openslide.OpenSlideError is slide_reader.SlideReaderError
    assert (
        openslide.OpenSlideUnsupportedFormatError
        is slide_reader.UnsupportedFormatError
    )
    assert openslide.lowlevel.OpenSlideError is slide_reader.SlideReaderError
    assert (
        openslide.lowlevel.OpenSlideUnsupportedFormatError
        is slide_reader.UnsupportedFormatError
    )


def test_property_names_are_openslides():
    # HistoQC looks these keys up in ``properties``; the values must stay
    # OpenSlide's strings.
    assert openslide.PROPERTY_NAME_BACKGROUND_COLOR == (
        "openslide.background-color"
    )
    assert openslide.PROPERTY_NAME_BOUNDS_X == "openslide.bounds-x"
    assert openslide.PROPERTY_NAME_BOUNDS_Y == "openslide.bounds-y"
    assert openslide.PROPERTY_NAME_BOUNDS_WIDTH == "openslide.bounds-width"
    assert openslide.PROPERTY_NAME_BOUNDS_HEIGHT == "openslide.bounds-height"
    assert openslide.PROPERTY_NAME_OBJECTIVE_POWER == (
        "openslide.objective-power"
    )


def test_reads_an_aperio_svs(svs_small):
    with openslide.OpenSlide(str(svs_small)) as slide:
        assert slide.properties["openslide.vendor"] == "aperio"
        assert slide.dimensions == (2220, 2967)
        thumb = slide.get_thumbnail((256, 256))
        assert max(thumb.size) == 256
