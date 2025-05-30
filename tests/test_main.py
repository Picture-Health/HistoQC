from tests.picture_health.entrypoint import hqc_main

def test_main():
    try:
        hqc_main(
            basepath='/Users/odin/sw/HistoQC/tests/data',
            input_pattern='sample3.tiff',
            outdir='results',
            config='/Users/odin/sw/HistoQC/tests/picture_health/config_ph_v2.1.ini',
        )
        assert True  # If no exception is raised, the test passes
    except Exception as e:
        assert False, e