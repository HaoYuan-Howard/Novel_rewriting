#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 定向改写不再接受 session pool 作为生产客户端。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel.errors import ProvenanceRefused
from novel.stages.stage5 import write


def test_reviewer_rewrite_refuses_retired_session_source_before_loading_request(
        monkeypatch):
    ctx = SimpleNamespace(
        paid=True,
        executor_profile=None,
        novel_dir='E:/synthetic-stage5-book',
        run_id='direct-reviewer-run',
    )
    source = SimpleNamespace()
    monkeypatch.setattr(
        write.reviewer_rewrite,
        'load_reviewer_rewrite_batch',
        lambda *_args, **_kwargs: pytest.fail(
            '非 LlmClient 必须在读取定向改写 request 前拒绝'),
    )

    with pytest.raises(ProvenanceRefused, match='只接受 LlmClient'):
        write.run_reviewer_rewrite(ctx, source)
