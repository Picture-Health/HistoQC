"""The golden test's fixture handling (no slides needed, runs by default).

Guards the post-merge review of PR #1: a configured fixture source that is
broken must fail the golden test, never skip it; only "no source configured"
may skip.
"""

from __future__ import annotations

import sys
import types

import pytest

import test_real_chains as golden

CHAIN = "real_chain_v1"


def _outcome(fn, *args):
    try:
        fn(*args)
    except pytest.skip.Exception:
        return "skip"
    except pytest.fail.Exception:
        return "fail"
    except Exception:  # noqa: BLE001 - an error also fails the test
        return "error"
    return "ok"


# RQ: When the golden test (`pytest -m integration tests/golden`) fetches a
#     chain's fixtures, given neither GOLDEN_FIXTURES_DIR nor
#     GOLDEN_FIXTURES_S3 is set, then the system shall skip the golden test.
def test_no_source_skips(monkeypatch, tmp_path):
    # S1: Clear both fixture-source variables.
    monkeypatch.delenv("GOLDEN_FIXTURES_DIR", raising=False)
    monkeypatch.delenv("GOLDEN_FIXTURES_S3", raising=False)
    # S2: Fetch the chain's fixtures.
    # V1: Verify that the golden test was skipped.
    assert _outcome(golden._fixtures, CHAIN, tmp_path) == "skip"


# RQ: When the golden test (`pytest -m integration tests/golden`) fetches a
#     chain's fixtures, given GOLDEN_FIXTURES_DIR names a directory without
#     the chain's input slide and golden results.tsv, then the system shall
#     fail the golden test.
def test_missing_local_file_fails(monkeypatch, tmp_path):
    # S1: Point GOLDEN_FIXTURES_DIR at an empty directory.
    monkeypatch.setenv("GOLDEN_FIXTURES_DIR", str(tmp_path))
    # S2: Fetch the chain's fixtures.
    # V1: Verify that the golden test failed.
    assert _outcome(golden._fixtures, CHAIN, tmp_path) == "fail"


def _fake_boto3(monkeypatch, client):
    monkeypatch.setitem(
        sys.modules, "boto3", types.SimpleNamespace(client=lambda _name: client)
    )
    monkeypatch.delenv("GOLDEN_FIXTURES_DIR", raising=False)
    monkeypatch.setenv("GOLDEN_FIXTURES_S3", "1")


# RQ: When the golden test (`pytest -m integration tests/golden`) fetches a
#     chain's fixtures, given GOLDEN_FIXTURES_S3=1 and the S3 listing raises
#     an error (e.g. access denied), then the system shall fail the golden
#     test, not skip it.
def test_s3_error_fails_not_skips(monkeypatch, tmp_path):
    # S1: Set GOLDEN_FIXTURES_S3=1 with an S3 client that denies access.
    class Denied:
        def get_paginator(self, _op):
            raise RuntimeError("AccessDenied")

    _fake_boto3(monkeypatch, Denied())
    # S2: Fetch the chain's fixtures.
    # V1: Verify that the golden test failed (or errored) and did not skip.
    assert _outcome(golden._fixtures, CHAIN, tmp_path) in ("fail", "error")


# RQ: When the golden test (`pytest -m integration tests/golden`) fetches a
#     chain's fixtures, given GOLDEN_FIXTURES_S3=1 and the golden prefix
#     lists no objects, then the system shall fail the golden test without
#     downloading anything.
def test_empty_s3_listing_fails(monkeypatch, tmp_path):
    # S1: Set GOLDEN_FIXTURES_S3=1 with an S3 client whose listing is empty
    # and whose download raises if called.
    class Moved:
        def get_paginator(self, _op):
            return types.SimpleNamespace(paginate=lambda **_kw: [{}])

        def download_file(self, *_args):
            raise AssertionError("must not download after an empty listing")

    _fake_boto3(monkeypatch, Moved())
    # S2: Fetch the chain's fixtures.
    # V1: Verify that the golden test failed and nothing was downloaded.
    assert _outcome(golden._fixtures, CHAIN, tmp_path) == "fail"


# RQ: When the golden test (`pytest -m integration tests/golden`) fetches a
#     chain's fixtures, given GOLDEN_FIXTURES_S3=1 and boto3 is not
#     installed, then the system shall fail the golden test.
def test_s3_without_boto3_fails(monkeypatch, tmp_path):
    # S1: Set GOLDEN_FIXTURES_S3=1 with boto3 unimportable.
    monkeypatch.setitem(sys.modules, "boto3", None)  # import raises ImportError
    monkeypatch.delenv("GOLDEN_FIXTURES_DIR", raising=False)
    monkeypatch.setenv("GOLDEN_FIXTURES_S3", "1")
    # S2: Fetch the chain's fixtures.
    # V1: Verify that the golden test failed.
    assert _outcome(golden._fixtures, CHAIN, tmp_path) == "fail"
