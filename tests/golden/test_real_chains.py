"""Stage-2 (HistoQC) golden test over the five real-slide chains (TRD-3518).

For each chain in ``chains.py``: run HistoQC on the chain's standardized slide
with the PH config, exactly as the golden was made, and compare the output with
the golden (``real_chain_v1/compare.py``: PNGs byte-identical, results.tsv
exact apart from the warnings column and the run-specific ``#`` lines).

Fixtures come from ``GOLDEN_FIXTURES_DIR`` when set (a local copy laid out as
``<dir>/<chain>/{input,golden}/``), else from S3. The test skips when neither is
reachable, so a plain ``pytest`` run stays green without AWS credentials.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "real_chain_v1"))
from chains import CHAINS, s3_prefix  # noqa: E402
from compare import compare  # noqa: E402
from regenerate import run_histoqc  # noqa: E402

pytestmark = pytest.mark.integration


def _fixtures(chain: str, tmp: Path) -> Path:
    """Return a dir holding ``input/<slide>`` and ``golden/``."""
    slide = CHAINS[chain]
    local = os.environ.get("GOLDEN_FIXTURES_DIR")
    if local:
        root = Path(local) / chain
        for needed in (root / "input" / slide, root / "golden" / "results.tsv"):
            if not needed.exists():
                pytest.skip(f"{needed} not in GOLDEN_FIXTURES_DIR")
        return root
    boto3 = pytest.importorskip("boto3")
    bucket, _, prefix = s3_prefix(chain)[len("s3://") :].partition("/")
    root = tmp / "fixtures"
    try:
        s3 = boto3.client("s3")
        keys = [
            obj["Key"]
            for page in s3.get_paginator("list_objects_v2").paginate(
                Bucket=bucket, Prefix=f"{prefix}/golden/"
            )
            for obj in page.get("Contents", [])
            if not obj["Key"].endswith(".log")
        ] + [f"{prefix}/input/{slide}"]
        for key in keys:
            path = root / key[len(prefix) + 1 :]
            path.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, key, str(path))
    except Exception as exc:  # noqa: BLE001 - unreachable -> skip, not error
        pytest.skip(f"golden fixture unavailable: {exc}")
    return root


@pytest.mark.parametrize("chain", sorted(CHAINS))
def test_stage2_reproduces_the_golden(chain, tmp_path):
    slide = CHAINS[chain]
    fixtures = _fixtures(chain, tmp_path)
    run_dir = tmp_path / "run"
    run_histoqc(fixtures / "input" / slide, run_dir)
    assert compare(run_dir, fixtures / "golden", slide) == []
