"""The five real-slide golden chains (TRD-3501, TRD-3518).

Each chain is one de-identified CCF immunotherapy Philips scan run through all
seven pathology stages. HistoQC is stage 2: its input is the stage-1
(pathology-slide-ingest) golden. This module names that slide for each chain,
so ``regenerate.py``, ``compare.py`` and ``test_real_chains.py`` agree.
"""

from __future__ import annotations

S3_ROOT = (
    "s3://picturehealth-data/users/haojia/pathomics/model_integration_test/"
    "histoqc"
)

#: chain name -> slide file in input/ (golden/ holds results.tsv and a
#: folder of PNGs named after it)
CHAINS = {
    "real_chain_v1": "ccf_105_std.tiff",
    "real_chain_ccf_010": "ccf_010_std.tiff",
    "real_chain_ccf_031": "ccf_031_std.tiff",
    "real_chain_ccf_054": "ccf_054_std.tiff",
    "real_chain_ccf_008": "ccf_008_std.tiff",
}


def s3_prefix(chain: str) -> str:
    return f"{S3_ROOT}/{chain}"
