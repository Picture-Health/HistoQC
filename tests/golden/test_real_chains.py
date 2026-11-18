"""Stage-2 (HistoQC) golden test over the five real-slide chains (TRD-3518).

For each chain in ``chains.py``: run HistoQC on the chain's standardized slide
with the PH config, exactly as the golden was made, and compare the output with
the golden (``real_chain_v1/compare.py``: PNGs byte-identical, results.tsv
exact apart from the warnings column and the run-specific ``#`` lines).

The test is opt-in: ``pyproject.toml`` deselects the ``integration`` marker,
so a plain ``pytest`` never runs it (and never downloads five whole slides).
Run it with ``pytest -m integration tests/golden`` and one fixture source:

- ``GOLDEN_FIXTURES_DIR``: a local copy laid out as
  ``<dir>/<chain>/{input,golden}/``;
- ``GOLDEN_FIXTURES_S3=1``: download from ``chains.S3_ROOT`` (needs read
  access to the bucket).

With neither set the test skips. Once a source is configured, any fixture
problem (a missing file, an S3 error, an empty golden listing, boto3 not
installed) FAILS the test, so a green run always means the slides were
compared.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "real_chain_v1"))
from chains import CHAINS, s3_prefix  # noqa: E402
from compare import compare  # noqa: E402
from regenerate import run_histoqc  # noqa: E402

pytestmark = pytest.mark.integration


def _fixtures(chain: str, tmp: Path) -> Path:
    """Return a dir holding ``input/<slide>`` and ``golden/results.tsv``."""
    slide = CHAINS[chain]
    local = os.environ.get("GOLDEN_FIXTURES_DIR")
    if local:
        root = Path(local) / chain
    elif os.environ.get("GOLDEN_FIXTURES_S3") == "1":
        root = tmp / "fixtures"
        _download(chain, root)
    else:
        pytest.skip(
            "no fixture source: set GOLDEN_FIXTURES_DIR or GOLDEN_FIXTURES_S3=1"
        )
    for needed in (root / "input" / slide, root / "golden" / "results.tsv"):
        if not needed.is_file():
            pytest.fail(f"golden fixture missing: {needed}")
    return root


def _download(chain: str, root: Path) -> None:
    """Copy the chain's input slide and golden (minus logs) from S3 to root.

    Errors propagate (no skip): access denied or a moved prefix must fail.
    """
    try:
        import boto3
    except ImportError:
        pytest.fail("GOLDEN_FIXTURES_S3=1 but boto3 is not installed")
    bucket, _, prefix = s3_prefix(chain)[len("s3://") :].partition("/")
    s3 = boto3.client("s3")
    golden = [
        obj["Key"]
        for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=f"{prefix}/golden/"
        )
        for obj in page.get("Contents", [])
        if not obj["Key"].endswith(".log")
    ]
    if not golden:
        pytest.fail(f"no golden objects under s3://{bucket}/{prefix}/golden/")
    for key in golden + [f"{prefix}/input/{CHAINS[chain]}"]:
        path = root / key[len(prefix) + 1 :]
        path.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, key, str(path))


# RQ: When the `python -m histoqc -c config_ph_v2.1.ini -n 1 -s 1` command
#     is run on a chain's standardized slide, given the chain's input and
#     golden fixtures, then the system shall write PNGs byte-identical to the
#     golden's and a results.tsv matching it exactly (apart from the warnings
#     column and the run-specific # lines).
@pytest.mark.parametrize("chain", sorted(CHAINS))
def test_stage2_reproduces_the_golden(chain):
    slide = CHAINS[chain]
    # Not pytest's tmp_path: pytest keeps the last three runs' directories,
    # and an S3 run downloads whole slides. This folder is deleted after
    # each chain, pass or fail.
    tmp = Path(tempfile.mkdtemp(prefix=f"histoqc-golden-{chain}-"))
    try:
        # S1: Fetch the chain's input slide and golden (fixture errors fail).
        fixtures = _fixtures(chain, tmp)
        # S2: Run HistoQC on the slide exactly as the golden was made.
        run_dir = tmp / "run"
        run_histoqc(fixtures / "input" / slide, run_dir)
        # V1: Verify that the output matched the golden under compare.py's
        # rule (no differences reported).
        assert compare(run_dir, fixtures / "golden", slide) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
