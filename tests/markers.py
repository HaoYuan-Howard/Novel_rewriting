#!/usr/bin/env python3
"""Shared marker helpers for tests that optionally consume the frozen corpus."""
from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

import pytest


T = TypeVar('T')


def corpus_params(values: Iterable[T], *, reason: str) -> list:
    """Mark only parameter rows that actually depend on frozen artifacts.

    Module-level ``corpus`` markers also hide unrelated synthetic/schema tests.
    An explicit empty row keeps a visible skip under ``--require-corpus`` instead
    of silently collecting zero cases when no current corpus is configured.
    """
    rows = list(values)
    if rows:
        return [pytest.param(value, marks=pytest.mark.corpus) for value in rows]
    return [pytest.param(
        None,
        marks=(pytest.mark.corpus, pytest.mark.skip(reason=reason)),
        id='no-corpus',
    )]
