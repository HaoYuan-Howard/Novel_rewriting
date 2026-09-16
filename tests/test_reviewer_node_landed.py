"""正式 producer 已批准但工作单元未认领：只读精确补尾，不能认领相邻决定。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema.stage1 import HookCopy, MainText
from contracts.schema.stage6 import Hook, Mech
from contracts.schema.stage7 import NameMap, NameMapFreeProvenance
from novel import prov
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.orchestrator.reviewer_node_landed import approval_action_landed
from novel.run import RunContext
from novel.stages.stage0 import removal, revisions
from novel.stages.stage1.partition import partition_text
from novel.stages.stage6 import hook
from novel.stages.stage7 import rename
from novel.store import ArtifactStore
from tests.factories import TEXT_A, TEXT_B, TEXT_C
from tests.factories import stage6_hook as _hook


SID = '986'
ACTOR = '恢复审阅者'
REASON = '已逐项核对这次完整工作包'


def _tree(base: Path) -> dict[str, str]:
    return {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in base.rglob('*') if p.is_file()}


def _ctx(base: Path, node: str) -> RunContext:
    return RunContext.open(base, SID, node, paid=False, write_ledger=False)


def _base(tmp_path: Path) -> Path:
    source = tmp_path / f'FQ_审批恢复_{SID}.txt'
    source.write_text(
        '开篇悬念。\n\n1\n沈月来到门前。\n\n2\n她推开屋门。\n', encoding='utf-8')
    base = tmp_path / 'output'
    removal.run_source(source, base)
    return base


def _landed(base: Path, node: str, flags: tuple[str, ...]) -> bool:
    before = _tree(base)
    try:
        return approval_action_landed(base, SID, node=node, flags=flags, code=prov.code_version())
    finally:
        assert _tree(base) == before, '已落查询不得改审批、产物、工作单元或费用/会话账'


# ⚠ `test_stage0_real_approval_requires_exact_revision_actor_reason` 已随
#   stage0.approve 节点退役删除（2026-09-15 B1）：历史 stage0 审批账的 landed
#   解码仍受 support，但不再有现役 producer 可真实构造该事件。
def _seed_final(base: Path, monkeypatch) -> None:
    """给定合成前置；真实 ArtifactStore、批准 producer、workpack/identity 均不替换。"""
    ctx1 = _ctx(base, 'stage1.partition')
    text = ArtifactStore(ctx1).path('stage0/clean_text.txt').read_text(encoding='utf-8')
    partitioned = partition_text(text)
    store1 = ArtifactStore(ctx1)
    store1.write('stage1/main_text.json', partitioned.main_text)
    store1.write('stage1/hook_copy.json', partitioned.hook_copy)

    def hook_upstreams(_ctx, store):
        store.read(HookCopy, 'stage1/hook_copy.json')
        return SimpleNamespace(judged={}, upto=1)

    def rename_upstreams(_ctx, store):
        store.read(MainText, 'stage1/main_text.json')
        return SimpleNamespace(judged={}, upto=1)

    monkeypatch.setattr(hook, '_Upstreams', hook_upstreams)
    from tests.test_final_approval_workpacks import _stage6_materials
    monkeypatch.setattr(hook, '_approval_materials', lambda *_args: _stage6_materials())
    monkeypatch.setattr(hook, '_judge_artifact', lambda *_args: [])
    monkeypatch.setattr(hook, '_normalize_and_judge', lambda text, _up: (
        text, Mech(punct_fixed=0, quotes_fixed=0, paras_fixed=0), []))
    monkeypatch.setattr(rename, '_Upstreams', rename_upstreams)
    monkeypatch.setattr(rename, '_judge', lambda *_args: [])

    ctx6 = _ctx(base, 'stage6.hook')
    store6 = ArtifactStore(ctx6)
    store6.read(HookCopy, 'stage1/hook_copy.json')
    candidate = _hook([TEXT_A, TEXT_B, TEXT_C])
    candidate = candidate.model_copy(update={'provenance': candidate.provenance.model_copy(update={
        'code': ctx6.code, 'root_clean_sha1': ctx6.root_clean_sha1, 'inputs': ctx6.edges,
        'prompts': {'RULES': prov.fingerprint(prov.rules_fingerprint('stage6'))},
    })})
    store6.write(hook.REL, candidate)

    ctx7 = _ctx(base, 'stage7.rename')
    store7 = ArtifactStore(ctx7)
    store7.read(MainText, 'stage1/main_text.json')
    art = NameMap(
        surname_map={'沈': '陆'}, characters=[{
            'char_id': 'char_001', 'old_canonical': '沈月', 'new_canonical': '陆月',
            'renamed': True, 'not_renamed_reason': None, 'aliases': [],
            'reason': '合成输入明确决定改姓',
        }], extra_subs=[], subs={'沈月': '陆月'}, kept=[],
        applied={'chapters': {}, 'hook': {'counts': {}}}, residual=[], baselines={},
        approved=None, book_txt=None, judged={}, hook_sha1=None,
        hook_consumed={'present': False, 'candidates_sha1': prov.fingerprint('')},
        sources={'writer_context': {'state': 'fresh', 'detail': [], 'code': ctx7.code}},
        book=[], identity=None,
        _provenance=ctx7.free_provenance(
            NameMapFreeProvenance, origin='human', params={'max_tokens': 1024},
            prompts={'RULES': prov.rules_fingerprint('stage7')}))
    store7.write(rename.REL, art)


@pytest.mark.parametrize('node', ['stage6.approve'])
def test_final_producer_approval_requires_original_token_and_exact_statement(
    tmp_path, monkeypatch, node,
):
    base = _base(tmp_path)
    _seed_final(base, monkeypatch)
    ctx = _ctx(base, node)
    store = ArtifactStore(ctx)
    module = hook
    model = Hook
    pack = module.approval_workpack(store)
    flags = (('--pick', '1') if node == 'stage6.approve' else ()) + (
        '--actor', ACTOR, '--reason', REASON, '--review-token', pack.review_token)
    assert not _landed(base, node, flags)
    kwargs = {'actor': ACTOR, 'reason': REASON, 'review_token': pack.review_token}
    if node == 'stage6.approve':
        kwargs['pick'] = 1
    module.run_approve(ctx, **kwargs)
    assert _landed(base, node, flags)
    for key, value in (('--actor', '另一位'), ('--reason', '另一决定'),
                       ('--review-token', pack.review_token[:-1] + ('0' if pack.review_token[-1] != '0' else '1'))):
        wrong = list(flags)
        wrong[wrong.index(key) + 1] = value
        assert not _landed(base, node, tuple(wrong))
    assert not _landed(base, node, ('--pick', '2', *flags[2:]))
    assert pack.review_token.startswith('s6r4_')
    old = list(flags)
    old[old.index('--review-token') + 1] = pack.review_token.replace('s6r4_', 's6r3_', 1)
    assert not _landed(base, node, tuple(old)), '相同 workpack 摘要不能认领旧契约令牌'

    art, _ = store.read_own(model, module.REL)
    approval = art.approved.resolution
    for field, value in (('workpack_sha256', None), ('review_identity_sha256', None),
                         ('subject_sha256', 'f' * 64)):
        changed = approval.model_copy(update={field: value})
        approved = art.approved.model_copy(update={'resolution': changed})
        store.write(module.REL, art.model_copy(update={'approved': approved}))
        assert not _landed(base, node, flags)
    store.write(module.REL, art)
    assert _landed(base, node, flags)
    store.path(module.REL).write_text('{"v":', encoding='utf-8')
    with pytest.raises(ArtifactUnreadable):
        _landed(base, node, flags)


def test_unsupported_missing_or_incomplete_node_action_is_read_only(tmp_path):
    assert not _landed(tmp_path, 'stage5.review', ())
    # 批次 B4：stage7.approve 已退役；landed 探测对未知/退役节点恒 False。
    assert not _landed(tmp_path, 'stage7.approve', ())
    flags = ('--actor', ACTOR, '--reason', REASON, '--review-token', 's7r3_' + 'a' * 64)
    assert not _landed(tmp_path, 'stage7.approve', flags)
    assert not _landed(tmp_path, 'stage7.approve', (*flags, '--actor', ACTOR))
