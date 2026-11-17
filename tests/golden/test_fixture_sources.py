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


def test_no_source_skips(monkeypatch, tmp_path):
    monkeypatch.delenv("GOLDEN_FIXTURES_DIR", raising=False)
    monkeypatch.delenv("GOLDEN_FIXTURES_S3", raising=False)
    assert _outcome(golden._fixtures, CHAIN, tmp_path) == "skip"


def test_missing_local_file_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("GOLDEN_FIXTURES_DIR", str(tmp_path))
    assert _outcome(golden._fixtures, CHAIN, tmp_path) == "fail"


def _fake_boto3(monkeypatch, client):
    monkeypatch.setitem(
        sys.modules, "boto3", types.SimpleNamespace(client=lambda _name: client)
    )
    monkeypatch.delenv("GOLDEN_FIXTURES_DIR", raising=False)
    monkeypatch.setenv("GOLDEN_FIXTURES_S3", "1")


def test_s3_error_fails_not_skips(monkeypatch, tmp_path):
    class Denied:
        def get_paginator(self, _op):
            raise RuntimeError("AccessDenied")

    _fake_boto3(monkeypatch, Denied())
    assert _outcome(golden._fixtures, CHAIN, tmp_path) in ("fail", "error")


def test_empty_s3_listing_fails(monkeypatch, tmp_path):
    class Moved:
        def get_paginator(self, _op):
            return types.SimpleNamespace(paginate=lambda **_kw: [{}])

        def download_file(self, *_args):
            raise AssertionError("must not download after an empty listing")

    _fake_boto3(monkeypatch, Moved())
    assert _outcome(golden._fixtures, CHAIN, tmp_path) == "fail"


def test_s3_without_boto3_fails(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "boto3", None)  # import raises ImportError
    monkeypatch.delenv("GOLDEN_FIXTURES_DIR", raising=False)
    monkeypatch.setenv("GOLDEN_FIXTURES_S3", "1")
    assert _outcome(golden._fixtures, CHAIN, tmp_path) == "fail"
