"""Compare a regenerated stage-2 (HistoQC) output against its golden.

The rule recorded in every chain's manifest.json (``tolerances``):

* every ``<slide>/*.png`` (incl. ``mask_use``) byte-identical to the golden,
  and the same set of PNGs;
* ``results.tsv``: the data row exact, column by column, EXCEPT the
  ``warnings`` column (it holds the random ``.histoqc_pkg_data_tmp*`` dir
  name); the column header row and ``#pipeline`` exact; the other ``#`` header
  lines (start_time, outdir, config_file, command_line_args) skipped, since
  they hold the run's time and paths.

Usage::

    python tests/golden/real_chain_v1/compare.py --run OUT/golden \\
        [--golden DIR] [--chain NAME]

Without ``--golden`` the golden is downloaded from S3 (AWS CLI).
Stdlib only, so it runs on any host. Exit code 0 = pass, 1 = fail.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from regenerate import CHAINS, DEFAULT_CHAIN, s3_prefix  # noqa: E402

#: ``#key:`` header lines that hold run-specific times and paths.
RUN_SPECIFIC_HEADERS = ("start_time", "outdir", "config_file", "command_line_args")
SKIPPED_COLUMNS = ("warnings",)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_tsv(path: Path):
    """Return (kept ``#`` header lines, column names, data rows)."""
    headers, columns, rows = [], None, []
    for line in path.read_text().splitlines():
        if line.startswith("#dataset:"):
            columns = line[len("#dataset:") :].split("\t")
        elif line.startswith("#"):
            key = line[1:].split(":", 1)[0]
            if key not in RUN_SPECIFIC_HEADERS:
                headers.append(line)
        elif line:
            rows.append(line.split("\t"))
    return headers, columns, rows


def compare_tsv(run_file: Path, golden_file: Path) -> list:
    got_h, got_cols, got_rows = _read_tsv(run_file)
    exp_h, exp_cols, exp_rows = _read_tsv(golden_file)
    failures = []
    if got_h != exp_h:
        failures.append("results.tsv: # header lines differ (e.g. #pipeline)")
    if got_cols != exp_cols:
        return failures + ["results.tsv: column names differ"]
    if len(got_rows) != len(exp_rows):
        return failures + [
            f"results.tsv: {len(got_rows)} data rows != golden {len(exp_rows)}"
        ]
    for got, exp in zip(got_rows, exp_rows):
        for name, g, e in zip(exp_cols, got, exp):
            if name not in SKIPPED_COLUMNS and g != e:
                failures.append(
                    f"results.tsv: {name} {g!r} != golden {e!r}{_ulps(g, e)}"
                )
    return failures


def _ulps(got: str, exp: str) -> str:
    """Float distance in ULPs, to tell CPU/SIMD rounding from real changes."""
    try:
        a, b = float(got), float(exp)
    except ValueError:
        return ""
    if a == b or math.isnan(a) or math.isnan(b) or (a < 0) != (b < 0):
        return ""
    ia, ib = (struct.unpack("<q", struct.pack("<d", abs(x)))[0] for x in (a, b))
    return f" ({abs(ia - ib)} ULP)"


def compare(run_dir: Path, golden_dir: Path, slide: str) -> list:
    """Return a list of failure messages (empty = pass)."""
    failures = compare_tsv(run_dir / "results.tsv", golden_dir / "results.tsv")
    got = {p.name: p for p in (run_dir / slide).glob("*.png")}
    exp = {p.name: p for p in (golden_dir / slide).glob("*.png")}
    for name in sorted(exp.keys() - got.keys()):
        failures.append(f"{name}: missing from the run")
    for name in sorted(got.keys() - exp.keys()):
        failures.append(f"{name}: not in the golden")
    same = 0
    for name in sorted(exp.keys() & got.keys()):
        if _sha256(got[name]) == _sha256(exp[name]):
            same += 1
        else:
            failures.append(f"{name}: bytes differ from the golden")
    print(f"{slide}: {same}/{len(exp)} PNGs byte-identical")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True, help="Regenerated golden/.")
    parser.add_argument("--golden", help="Golden dir (default: S3).")
    parser.add_argument(
        "--chain", default=DEFAULT_CHAIN, choices=sorted(CHAINS)
    )
    args = parser.parse_args()
    slide = CHAINS[args.chain]

    with tempfile.TemporaryDirectory() as tmp:
        golden_dir = Path(args.golden) if args.golden else Path(tmp)
        if not args.golden:
            subprocess.run(
                [
                    "aws",
                    "s3",
                    "cp",
                    "--only-show-errors",
                    "--recursive",
                    "--exclude",
                    "*.log",
                    f"{s3_prefix(args.chain)}/golden/",
                    str(golden_dir),
                ],
                check=True,
            )
        failures = compare(Path(args.run), golden_dir, slide)
    for failure in failures:
        print(f"FAIL: {failure}")
    if not failures:
        print("PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
