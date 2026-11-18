# HistoQC golden kit (stage 2 of the real-slide chains)

Stage 2 of the TRD-3501 / TRD-3518 golden chains: HistoQC with the PH production
config on the stage-1 (pathology-slide-ingest) golden of each slide. Downstream
stages (tumor seg, HistoBlur, tiler) read `<slide>/<slide>_mask_use.png`. This
directory lets anyone regenerate the stage-2 goldens from git and check them.
It mirrors the pathology-slide-ingest kit (TRD-3553).

## The fixtures

S3 prefix (read access to `picturehealth-data` needed), one per chain:
`s3://picturehealth-data/users/haojia/pathomics/model_integration_test/histoqc/<chain>/`

| Path | What it is |
|---|---|
| `input/ccf_<NNN>_std.tiff` | Byte copy of the stage-1 golden (standardized slide). Internal data, do not share outside Picture Health. |
| `golden/results.tsv` | HistoQC's results table (one data row). |
| `golden/ccf_<NNN>_std.tiff/*.png` | The 19 per-slide PNGs, incl. `_mask_use.png`. |
| `input/SHA256SUMS`, `golden/SHA256SUMS` | sha256 of every file. |
| `manifest.json` | Repo, branch, commit (`cd66a60` on `feat/TRD-3227-replace-openslide-with-slide-reader`), package versions, hardware, exact command, tolerances, notes. |
| `scripts/` | The generator used to make the fixture (local paths; this directory is the portable version). |

`tests/golden/chains.py` lists the five chains: `real_chain_v1` (CCF 105) and
`real_chain_ccf_010`, `_031`, `_054`, `_008`.

## The command

```bash
python -m histoqc -c tests/picture_health/config_ph_v2.1.ini -o OUT -n 1 -s 1 -f ccf_<NNN>_std.tiff
```

`config_ph_v2.1.ini` as of `cd66a60` (`BasicModule.finalProcessingArea`
`area_threshold` 4000). `regenerate.py` runs exactly this; `--chain NAME` picks
the chain (default `real_chain_v1`), `--input DIR` reads the slide from DIR
instead of S3.

## Reproduce

In the image (see the repo README for the build command, which needs a GitHub
token as a BuildKit secret):

```bash
mkdir -p /tmp/hqc && chmod 777 /tmp/hqc
aws s3 cp --recursive --exclude '*.log' \
    s3://picturehealth-data/users/haojia/pathomics/model_integration_test/histoqc/real_chain_ccf_010/ /tmp/fx/
docker run --rm --platform linux/amd64 -v "$PWD":/repo:ro -v /tmp/fx:/fx:ro -v /tmp/hqc:/out \
    --entrypoint python histoqc /repo/tests/golden/real_chain_v1/regenerate.py \
    --chain real_chain_ccf_010 --input /fx/input --out /out
python3 tests/golden/real_chain_v1/compare.py --chain real_chain_ccf_010 \
    --run /tmp/hqc/golden --golden /tmp/fx/golden   # host: stdlib only
```

In a venv: Python 3.10, `pip install --require-hashes -r requirements-image.lock`,
then pathology-slide-reader `--no-deps` at the commit in `requirements.txt`; run
from the repo root.

All five with pytest. The test is opt-in: `pyproject.toml` deselects the
`integration` marker, so a plain `pytest` never runs it or downloads slides.
Opt in with `-m integration` and one fixture source:

```bash
# a local copy laid out as <dir>/<chain>/{input,golden}/
GOLDEN_FIXTURES_DIR=~/fixtures/histoqc pytest -m integration tests/golden
# or download from S3 (chains.S3_ROOT; needs read access to the bucket)
GOLDEN_FIXTURES_S3=1 pytest -m integration tests/golden
```

With no source set the test skips. With a source set, any fixture problem (a
missing file, an S3 error such as access denied, an empty golden listing,
boto3 not installed) fails the test, so a green run always compared all five
slides. `tests/golden/test_fixture_sources.py` (runs by default, no slides)
checks that rule.

Where to run it: the inputs are real (de-identified) clinical slides, and
slide data stays in AWS. Run it on an AWS-hosted linux/amd64 machine (such as
an EC2 dev box), in the image or a venv built from the lock, not on a laptop.
Each chain's downloads and HistoQC output go into a temp folder that is
deleted after the chain, pass or fail (not pytest's `tmp_path`, which keeps
the last three runs).

## Compare rule

From each manifest's `tolerances`:

- every PNG (incl. `mask_use`) byte-identical, and the same set of PNGs;
- `results.tsv`: the data row exact, column by column, except `warnings` (it
  holds a random `.histoqc_pkg_data_tmp*` dir name). The column header row and
  `#pipeline` must match; `#start_time`, `#outdir`, `#config_file` and
  `#command_line_args` are skipped (run time and paths).

`compare.py` exits 0 on pass. Failing float columns print their distance in
ULPs, so CPU rounding (1 or 2 ULP) is easy to tell from a real change.

## Verified

### Native x86-64: 5/5 pass (2026-11-11, TRD-3554)

The strict rule passes on hardware matching the goldens' CPU type: `pytest -m
integration tests/golden` in the image built from this Dockerfile at `7bf07f1`
(the head of PR #1, `chore/TRD-3554-histoqc-image-golden`), on an EC2
g4dn.8xlarge (Intel Xeon Platinum 8259CL, AVX-512; Tesla T4, unused by
HistoQC), fixtures from a local copy: `5 passed in 124.58s`. Every PNG
byte-identical (19/19 per slide) and every results.tsv column exact, for all
five chains. The comparison stays exact; no float tolerance is allowed.

### Other x86 CPUs: 1 to 2 ULP float differences (2026-11-09)

Other x86 CPUs, including emulated ones, are expected to fail the strict rule
by 1 or 2 ULP in a few float columns (and, on some slides, one grey level in
the diagnostic `_equalized_thumb.png`): numpy and OpenBLAS pick SIMD kernels by
CPU, and those round differently. `compare.py` prints the ULP distance of each
failing float column, so such a run can be told from a real change. Recorded
in the image built from this Dockerfile, run as `linux/amd64` under emulation
on an Apple Silicon Mac. The emulated CPU has SSE4.2 only (no AVX, AVX2, AVX-512 or FMA; OpenBLAS
picks its generic kernel):

| Chain | PNGs byte-identical | `mask_use.png` | results.tsv |
|---|---|---|---|
| real_chain_v1 | 19/19 | identical | 6 float columns off by 1 or 2 ULP |
| real_chain_ccf_010 | 19/19 | identical | 1 float column off by 1 ULP |
| real_chain_ccf_031 | 19/19 | identical | 3 float columns off by 1 ULP |
| real_chain_ccf_054 | 18/19 | identical | 2 float columns off by 1 ULP |
| real_chain_ccf_008 | 18/19 | identical | 3 float columns off by 1 ULP |

The two differing PNGs are `_equalized_thumb.png` (a diagnostic image nothing
downstream reads): 6 of 698,368 pixels (CCF 54) and 13 of 1,127,168 (CCF 8)
are one grey level off. Every integer and string column, and every count
(`pixels_to_use` etc.), matches exactly.

## Environment

The goldens were made in a venv on EC2 (Python 3.10.20, numpy 2.2.6,
scikit-image 0.25.2, scipy 1.15.3, opencv-python-headless 5.0.0.93, Pillow
12.3.0, tifffile 2025.5.10, imagecodecs 2025.3.30, pathology-slide-reader
0.1.0). The manifest does not record matplotlib (read from the golden
`_hist.png`: 3.10.9) or scikit-learn and the smaller dependencies; those are
pinned in `requirements-image.lock` as resolved on the golden run date. That
venv also had openslide-python 1.3.1 installed, but HistoQC no longer imports
it, so the image leaves it out.

## Known issues (open, not fixed here)

Changing detection would change the goldens, so these are recorded, not fixed.
Both are HistoQC's behaviour on the PH production config.

- `real_chain_ccf_010`: `pen_markings` flags a dense eosinophilic (bright pink)
  tissue region as pen, so that tissue is excluded from `mask_use`
  (TRD-3518 issue 5, false positive).
- `real_chain_ccf_054` and `real_chain_ccf_008`: pen-marker circles over the
  tissue. The dark ink cores are caught by `pen_markings` and most of the light
  ink halo by `coverslip_edge`, but thin light ink crossing the tissue stays
  inside `mask_use` (TRD-3518 issue 6, false negative).

## Raven records

The golden covers the file output only. HistoQC does not write to the Raven
database: all platform data goes through raven-api via the `raven` SDK, which
has no pathology QC endpoint yet (TRD-2841). When it does, add a golden of the
records sent through it.
