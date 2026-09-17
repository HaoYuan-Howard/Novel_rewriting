#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C2b：Stage2 Global 只保留直连生产；旧 agent 状态仅作为拒绝边界。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from contracts.schema.orchestration import AgentProfile
from contracts.schema.stage1 import MainText
from novel import paths, prov
from novel.agents import AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage
from novel.errors import GateBlocked, ProvenanceRefused
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger, ledger_path
from novel.run import RunContext
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_generation import _client, _ok, _response_global


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex',
        model='gpt-5.6-sol',
        reasoning_effort='high',
        execution_mode='standard',
    )


def _ctx(
        base: Path, *, with_characters: bool = True, agent: bool = False,
) -> RunContext:
    from novel.work_units import initialize_workflow

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
        executor_profile=_profile() if agent else None,
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
    assert ctx.ledger.path is not None
    kwargs = {'run_id': ctx.run_id} if same_run else {}
    return RunContext(
        novel_dir=ctx.novel_dir,
        node=ctx.node,
        paid=paid,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=ctx.ledger.path, **kwargs),
        executor_profile=_profile() if agent else None,
        **kwargs,
    )


def _resume_ctx(ctx: RunContext) -> RunContext:
    """兼容旧测试夹具名；现役恢复 context 不再携带 agent profile。"""
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


def _full(candidate: dict) -> str:
    """保留给冻结旧测试读取的历史 agent wire 包装器。"""
    return json.dumps({
        'payload': {'kind': 'full_candidate', 'candidate': copy.deepcopy(candidate)},
    }, ensure_ascii=False)


class _Driver(AgentDriver):
    """冻结旧 agent 测试的最小 driver；现役 producer 不会调用它。"""

    driver = 'codex'
    provider = 'openai'

    def __init__(
        self, responses: list[str], book: Path, *,
        session_id: str = 'retired-stage2-global-session',
    ) -> None:
        self.responses = list(responses)
        self.book = book
        self.session_id = session_id
        self.calls: list[tuple[str, AgentRequest]] = []

    def probe(self) -> AgentProbe:
        return AgentProbe(
            driver=self.driver,
            provider=self.provider,
            executable='retired-fake-codex',
            available=True,
            version='0.test',
        )

    def _turn(self) -> AgentTurn:
        return AgentTurn(
            session_id=self.session_id,
            content=self.responses.pop(0),
            model=None,
            actual_reasoning_effort='high',
            usage=AgentUsage(
                prompt_tokens=100,
                completion_tokens=20,
                reasoning_tokens=10,
                total_tokens=120,
            ),
            turn_count=1,
        )

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        self.calls.append(('start', request))
        return self._turn()

    def resume(
        self, profile: AgentProfile, session_id: str, request: AgentRequest,
    ) -> AgentTurn:
        self.calls.append(('resume', request))
        return self._turn()

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        return None


def _rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding='utf-8').splitlines()
    ]


@pytest.mark.parametrize('collection', ['world_rules', 'relations', 'arc_secrets'])
@pytest.mark.parametrize('shape', ['empty', 'missing', 'null'])
def test_direct_global_producer_distinguishes_explicit_empty_from_missing_or_null(
        tmp_path, collection, shape,
) -> None:
    from novel.errors import ResponseRepairRequired

    ctx = _ctx(tmp_path)
    ctx.repair_rejected_responses = True
    candidate = {
        'setting': {
            'era': None,
            'social_context': None,
            'era_evidence_refs': [],
            'social_context_evidence_refs': [],
        },
        'world_rules': [],
        'relations': [],
        'arc_secrets': [],
    }
    if shape == 'missing':
        candidate.pop(collection)
    elif shape == 'null':
        candidate[collection] = None
    client, transport = _client(ctx, lambda _req, _index: _ok(candidate))

    if shape == 'empty':
        artifact = extract.run_global(ctx, client)
        assert artifact.world_rules == artifact.relations == artifact.arc_secrets == []
    else:
        with pytest.raises(ResponseRepairRequired):
            extract.run_global(ctx, client)
        assert not ArtifactStore(ctx).exists(extract.GLOBAL_REL)
    assert len(transport.calls) == 1


def test_global_production_rejects_non_llm_client_before_preparation(
        tmp_path, monkeypatch,
) -> None:
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        extract,
        'prepare_global_candidate',
        lambda *_args, **_kwargs: pytest.fail('非直连客户端必须先拒绝'),
    )
    with pytest.raises(ProvenanceRefused, match='只接受 LlmClient'):
        extract.run_global(ctx, object())  # type: ignore[arg-type]


def test_global_production_rejects_agent_profile_before_preparation(
        tmp_path, monkeypatch,
) -> None:
    ctx = _ctx(tmp_path, agent=True)
    monkeypatch.setattr(
        extract,
        'prepare_global_candidate',
        lambda *_args, **_kwargs: pytest.fail('agent profile 必须先拒绝'),
    )
    with pytest.raises(ProvenanceRefused, match='不能接管 agent profile'):
        extract.run_global(ctx, object.__new__(LlmClient))


@pytest.mark.parametrize('legacy', [False, True])
def test_global_production_rejects_existing_agent_run_directory_before_wire(
        tmp_path, legacy,
) -> None:
    ctx = _ctx(tmp_path)
    root = (
        paths.legacy_stage2_global_run_path(ctx.novel_dir, ctx.run_id)
        if legacy else paths.stage2_global_run_path(ctx.novel_dir, ctx.run_id)
    )
    root.mkdir(parents=True)
    client, transport = _client(
        ctx, lambda _req, _index: pytest.fail('agent run 不得触发 wire'))

    with pytest.raises(ProvenanceRefused, match='agent 状态'):
        extract.run_global(ctx, client)
    assert transport.calls == []


def test_global_success_uses_only_direct_finalize_and_no_agent_journal(tmp_path) -> None:
    ctx = _ctx(tmp_path)
    client, transport = _client(
        ctx, lambda _req, _index: _ok(_response_global()))

    landed = extract.run_global(ctx, client)

    assert landed == ArtifactStore(ctx).read(
        type(landed), extract.GLOBAL_REL)
    assert len(transport.calls) == 1
    assert not paths.stage2_global_run_path(
        ctx.novel_dir, ctx.run_id).exists()
    assert not hasattr(extract, '_finalize_agent_global_formal')


def test_global_direct_hard_candidate_lands_once_without_agent_checkpoint(
        tmp_path,
) -> None:
    ctx = _ctx(tmp_path)
    client, transport = _client(
        ctx, lambda _req, _index: _ok(_bad_global()))

    with pytest.raises(GateBlocked, match='canon_chapter_refs_dangling'):
        extract.run_global(ctx, client)

    store = ArtifactStore(ctx)
    assert len(transport.calls) == 1
    assert store.exists(extract._GLOBAL_INVALID)
    assert not store.exists(extract.GLOBAL_REL)
    assert not paths.stage2_global_run_path(
        ctx.novel_dir, ctx.run_id).exists()
