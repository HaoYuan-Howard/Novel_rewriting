#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 Global / Analyze 测试共用 RunContext 夹具（C3：无 Agent CLI）。"""
from __future__ import annotations

import copy
from pathlib import Path

from contracts.schema.orchestration import LegacyExecutorBackend, LegacyLlmProfile
from contracts.schema.stage1 import MainText
from novel import prov
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, ledger_path
from novel.run import RunContext
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_generation import _response_global


def _profile() -> LegacyLlmProfile:
    return LegacyLlmProfile(
        provider='deepseek',
        model='deepseek-v4-pro',
        reasoning_effort='high',
        thinking_mode='on',
    )


def _backend() -> LegacyExecutorBackend:
    return LegacyExecutorBackend(profile=_profile())


def _ctx(
        base: Path, *, with_characters: bool = True, agent: bool = False,
) -> RunContext:
    from novel.work_units import initialize_workflow

    del agent  # C3：不再构造 agent profile
    initialize_workflow(base)
    book = base / 'global直连_2042'
    book.mkdir(exist_ok=True)
    clean = book / 'stage0/clean_text.txt'
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_text('synthetic stage0 root\n', encoding='utf-8', newline='')
    ctx = RunContext(
        novel_dir=book,
        node='stage2.global',
        paid=True,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(base)),
        executor_backend=_backend(),
    )
    ArtifactStore(ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    if with_characters:
        from tests.stage2_fixtures import seed_character_registry

        seed_character_registry(ctx)
    return ctx


def _copy_ctx(
        ctx: RunContext, *, same_run: bool, paid: bool,
        agent: bool = False,
) -> RunContext:
    del agent
    assert ctx.ledger.path is not None
    kwargs = {'run_id': ctx.run_id} if same_run else {}
    return RunContext(
        novel_dir=ctx.novel_dir,
        node=ctx.node,
        paid=paid,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=ctx.ledger.path, **kwargs),
        executor_backend=_backend(),
        **kwargs,
    )


def _resume_ctx(ctx: RunContext) -> RunContext:
    return _copy_ctx(ctx, same_run=True, paid=True)


def _new_run_ctx(ctx: RunContext) -> RunContext:
    return _copy_ctx(ctx, same_run=False, paid=True)


def _direct_ctx(ctx: RunContext, *, same_run: bool = False) -> RunContext:
    return _copy_ctx(ctx, same_run=same_run, paid=True)


def _free_ctx(ctx: RunContext) -> RunContext:
    return _copy_ctx(ctx, same_run=False, paid=False)


def _bad_global() -> dict:
    bad = copy.deepcopy(_response_global())
    bad['arc_secrets'][0]['revealed_from'] = 99
    return bad
