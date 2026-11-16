"""Regenerate the real_chain_v1 stage-2 golden (HistoQC, PH config v2.1).

Stage 2 of the TRD-3501 golden chain: HistoQC on the standardized CCF 105
slide (the stage-1 golden) with the PH production config. Downstream stages
(tumor seg, HistoBlur, tiler) use ``<slide>/<slide>_mask_use.png``.

Usage (from the repo root, in the HistoQC image or a venv with
requirements-image.lock)::

    python tests/golden/real_chain_v1/regenerate.py --out /tmp/hqc
    python tests/golden/real_chain_v1/compare.py --run /tmp/hqc/golden

``--input DIR`` skips the S3 download and reads the chain's input from DIR.
``--chain NAME`` picks one of the five chains in ``tests/golden/chains.py``
(default ``real_chain_v1``).
The S3 download uses the AWS CLI (``aws s3 cp``) and needs read access to the
``picturehealth-data`` bucket.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chains import CHAINS, s3_prefix  # noqa: E402

DEFAULT_CHAIN = "real_chain_v1"
CONFIG = (
    Path(__file__).resolve().parents[2] / "picture_health" / "config_ph_v2.1.ini"
)


def run_histoqc(slide: Path, outdir: Path, config: Path = CONFIG) -> None:
    """The exact stage-2 command the goldens were made with.

    ``python -m histoqc -c config_ph_v2.1.ini -o OUT -n 1 -s 1 -f SLIDE``.
    HistoQC unpacks its package data into a temp dir under the cwd, so run it
    from the (writable) output's parent.
    """
    if outdir.exists():
        shutil.rmtree(outdir)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "histoqc",
            "-c",
            str(config),
            "-o",
            str(outdir),
            "-n",
            "1",
            "-s",
            "1",
            "-f",
            str(slide),
        ],
        cwd=outdir.parent,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="Output directory.")
    parser.add_argument(
        "--input", help="Directory holding the input (default: S3)."
    )
    parser.add_argument(
        "--chain", default=DEFAULT_CHAIN, choices=sorted(CHAINS)
    )
    parser.add_argument(
        "--config", default=str(CONFIG), help="HistoQC config (default: PH v2.1)."
    )
    args = parser.parse_args()
    slide_name = CHAINS[args.chain]

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.input:
        input_dir = Path(args.input).resolve()
    else:
        input_dir = out / "input"
        subprocess.run(
            [
                "aws",
                "s3",
                "cp",
                "--only-show-errors",
                f"{s3_prefix(args.chain)}/input/{slide_name}",
                str(input_dir / slide_name),
            ],
            check=True,
        )
    run_histoqc(input_dir / slide_name, out / "golden", Path(args.config))
    print(f"wrote {out / 'golden'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
