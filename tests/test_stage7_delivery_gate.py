#!/usr/bin/env python3
"""Non-corpus guards for Stage7 approval/current-delivery boundaries."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema.report import finding
from contracts.schema.stage7 import ApprovedNameMap
from novel import paths, prov
from novel.errors import (
    ApprovalRequired, BlockingFindingsUnresolved, DeliveryIncomplete, GateBlocked,
    ProvenanceRefused, UpstreamStale, UpstreamUnproven,
)
from novel.orchestrator import state
from novel.stages.stage7 import assemble, rename
from novel.store import ArtifactStore


class _Store:
    def __init__(self, freshness: str = 'fresh', *, invalid: bool = False) -> None:
        self._freshness = freshness
        self._invalid = invalid

    def exists(self, rel: str) -> bool:
        return self._invalid and rel == assemble._INVALID

    def path(self, rel: str) -> Path:
        return Path('/novel') / rel

    def freshness(self, _rel: str):
        return SimpleNamespace(state=self._freshness, reason='synthetic')


def _upstreams():
    return SimpleNamespace(
        hook_art=object(), hook_text='已批准文案', missing=[], recs=[{}],
    )


def _approved_art():
    art = SimpleNamespace(
        surname_map={'沈': '陆'}, characters=[], extra_subs=[],
        approved=None, book=[],
    )
    art.approved = ApprovedNameMap(
        origin='human', actor='delivery-reviewer',
        reason='reviewed the complete current decision surface',
        subject_sha256=rename.approval_subject_sha256(art),
    )
    return art


def test_stage7_currentness_uses_review_identity_not_epoch_workpack_hash(
        monkeypatch):
    art = SimpleNamespace(
        surname_map={'沈': '陆'}, characters=[], extra_subs=[],
        approved=None, book=[],
    )
    subject = rename.approval_subject_sha256(art)
    art.approved = ApprovedNameMap(
        origin='human', actor='reviewer', reason='逐项核对当前人物决策',
        subject_sha256=subject,
        workpack_sha256='f' * 64,
        review_identity_sha256='e' * 64,
    )
    monkeypatch.setattr(
        rename, '_approval_workpack_snapshot',
        lambda *_args: (SimpleNamespace(
            workpack_sha256='0' * 64,
            review_identity_sha256='e' * 64,
            disposition='already_current'), None))

    assert rename.approval_proof_sha256(SimpleNamespace(), art) is not None

    monkeypatch.setattr(
        rename, '_approval_workpack_snapshot',
        lambda *_args: (SimpleNamespace(
            workpack_sha256='0' * 64,
            review_identity_sha256='d' * 64,
            disposition='review_required'), None))
    assert rename.approval_proof_sha256(SimpleNamespace(), art) is None


@pytest.mark.parametrize('entry', [assemble.read_current_book, assemble.run_assemble])
def test_r_2_15_stage2_gate_precedes_every_stage7_delivery_read_or_write(
        monkeypatch, entry):
    """`R-2.16` 同时守组装与下载；旧 txt 的存在不能绕过后来变红的事实闸。"""
    from novel.stages.stage2 import global_review

    seen: list[object] = []

    def closed(ctx):
        seen.append(ctx)
        raise ApprovalRequired('stage2 typed review 未逐项 PASS')

    monkeypatch.setattr(global_review, 'require_approved', closed)
    monkeypatch.setattr(
        assemble, 'ArtifactStore',
        lambda _ctx: (_ for _ in ()).throw(AssertionError('Stage2 闸后才可读 Stage7')))
    ctx = SimpleNamespace()

    with pytest.raises(ApprovalRequired, match='stage2 typed review'):
        entry(ctx)
    assert seen == [ctx]


def test_an_unadjudicated_invalid_blocks_delivery_and_says_where_the_draft_is():
    """⭐ `.INVALID` 挡着 ⇒ `GateBlocked`，**不是** `ApprovalRequired`。

    此刻批不了它：出路是闸门自己的 0 成本提升/编辑路。而这条终态的全部价值在
    `invalid_path`——报告说不出那份稿在哪，人就会以为要重跑（那是一次真金白银）。
    ⚠ 它排在「还没定稿」之前：本用例故意让两个条件同时成立来钉这个顺序。
    ⚠ **下一条命令在 `hint`，不在 message**：断言两个方向都查，否则有人把命令拼回
    正文时这条照样绿——而「这条终态有没有出路」就又变回不可判定的了。
    """
    art = SimpleNamespace(approved=None, book=[])
    with pytest.raises(GateBlocked) as blocked:
        assemble._require_mapping_current(
            SimpleNamespace(), _Store(invalid=True), art, _upstreams())
    assert blocked.value.invalid_path.endswith('name_map.INVALID.json')
    assert '--revalidate' in (blocked.value.hint or '')
    assert '--revalidate' not in str(blocked.value)


def test_assembly_refuses_an_unapproved_map_before_any_other_gate():
    art = SimpleNamespace(approved=None, book=[])
    with pytest.raises(ApprovalRequired, match='还没定稿'):
        assemble._require_mapping_current(
            SimpleNamespace(), _Store(), art, _upstreams())


def test_a_stale_map_and_an_unprovable_one_are_two_different_terminal_states():
    """⭐ `stale` 是一句**可复算的断言**（哪条边变了），`unknown` 只是「证明不了」。

    压成一句 `!= fresh` 的样子：报告替一件算不出来的事编一句「它已经变了」，
    而两者要人做的下一件事本来就不同（指得出重建哪一条 vs 只能整份重建）。
    ⚠ 交付闸两种都拦——`store._refuse_stale_upstream` 对 `unknown` 是放行的，
    这一档更严的严格性必须在类型上看得见，不能只活在措辞里。
    """
    art = _approved_art()
    with pytest.raises(UpstreamStale):
        assemble._require_mapping_current(
            SimpleNamespace(), _Store('stale'), art, _upstreams())
    with pytest.raises(UpstreamUnproven):
        assemble._require_mapping_current(
            SimpleNamespace(), _Store('unknown'), art, _upstreams())
    # 守卫的守卫：接住其中一条的调用方**不许**顺带接住另一条。
    assert not issubclass(UpstreamUnproven, UpstreamStale)
    assert not issubclass(UpstreamStale, UpstreamUnproven)


def test_a_map_that_still_carries_hard_findings_hands_over_the_rule_keys():
    """⭐ 正式稿带着没消的 hard ⇒ `BlockingFindingsUnresolved`，**不是** `GateBlocked`。

    盘上没有 `.INVALID` 可指 ⇒ 那条要么编一个不存在的路径，要么漏掉它的必填参数
    （后者的实测下场是 `TypeError`：退出码 70「崩了」，而这本是一条退出码 2）。
    `rules` 是 typed 的：消费方按 rule key 路由修复，不去 parse 措辞。
    """
    art = _approved_art()
    art.book = [finding('rn_ambiguous', detail='合成的一条阻断级结论', evidence=['阿禾'])]
    with pytest.raises(BlockingFindingsUnresolved) as blocked:
        assemble._require_mapping_current(
            SimpleNamespace(), _Store(), art, _upstreams())
    assert blocked.value.rules == ('rn_ambiguous',)


def test_assembly_refuses_changed_chapter_bindings(monkeypatch):
    art = _approved_art()
    monkeypatch.setattr(assemble, '_judged_drift', lambda _art, _up: (['stage5/deep/ch01.json'], []))
    monkeypatch.setattr(assemble, '_hook_drift', lambda _art, _up: False)
    # 与 freshness 那一条同类：量法不同（逐份成稿的消费指纹），结论与恢复路相同。
    with pytest.raises(UpstreamStale, match='不再绑定当前正文'):
        assemble._require_mapping_current(
            SimpleNamespace(), _Store(), art, _upstreams())


def test_assembly_accepts_an_approval_bound_to_the_current_decision_surface(monkeypatch):
    from novel.stages.stage6 import semantic

    art = _approved_art()
    monkeypatch.setattr(assemble, '_judged_drift', lambda _art, _up: ([], []))
    monkeypatch.setattr(assemble, '_hook_drift', lambda _art, _up: False)
    monkeypatch.setattr(semantic, 'require_approved_clear', lambda *_args: None)
    monkeypatch.setattr(
        assemble, 'approval_proof_sha256', lambda _store, _art: 'f' * 64)

    assemble._require_mapping_current(
        SimpleNamespace(), _Store(), art, _upstreams())


def test_assembly_refuses_an_approval_bound_to_an_older_decision_surface(monkeypatch):
    art = _approved_art()
    art.surname_map = {'沈': '周'}
    monkeypatch.setattr(assemble, '_judged_drift', lambda _art, _up: ([], []))
    monkeypatch.setattr(assemble, '_hook_drift', lambda _art, _up: False)

    with pytest.raises(
            ApprovalRequired, match='批准不再绑定当前决策面') as blocked:
        assemble._require_mapping_current(
            SimpleNamespace(), _Store(), art, _upstreams())
    assert '--review-token' in (blocked.value.hint or '')
    assert '--subject-sha256' not in (blocked.value.hint or '')


def test_an_incomplete_book_reports_every_missing_piece_at_once(monkeypatch):
    """⭐ 缺章与未批准文案**一次报全**，而且各自 typed。

    `R-10.14`：txt 是交付物，里面没有地方写风险抬头横幅——正因为成书里写不下警告，
    这道闸必须在拒绝的那一刻把差的东西一次说清。拆成 first-match 之后，
    人补完缺章才发现文案还没定稿，**一道 0 成本的闸要走两个来回**。
    ⚠ 两项恢复路各不相同（去跑 stage5 / `stage6.approve --pick N`）
    ⇒ 消费方按字段路由，不去 parse 那段人话。
    """
    from novel.stages.stage6 import semantic

    art = _approved_art()
    up = SimpleNamespace(
        hook_art=object(), hook_text='', missing=[7, 8], recs=[{}])
    monkeypatch.setattr(assemble, '_judged_drift', lambda _art, _up: ([], []))
    monkeypatch.setattr(assemble, '_hook_drift', lambda _art, _up: False)
    monkeypatch.setattr(semantic, 'require_approved_clear', lambda *_args: None)

    with pytest.raises(DeliveryIncomplete) as bad:
        assemble._require_mapping_current(SimpleNamespace(), _Store(), art, up)

    assert bad.value.missing == (7, 8)
    assert bad.value.hook_unapproved is True
    # 一次报全：两条都要在措辞里，而不是碰到第一条就返回。
    assert all(s in str(bad.value) for s in ('计划有而成稿无', '开篇文案'))


def test_retired_book_leaves_the_live_download_path_but_remains_recoverable(tmp_path):
    from novel.run import RunContext
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    novel_dir = tmp_path / '书名_123'
    # 成书按书名命名 ⇒ 取生产那一个落点，别在测试里手拼 `stage7/book.txt`。
    live = novel_dir / paths.book_txt_rel(novel_dir)
    live.parent.mkdir(parents=True)
    raw = '旧交付物\n'.encode('utf-8')
    live.write_bytes(raw)
    ctx = RunContext.open(tmp_path, '123', 'stage7.rename', paid=False)
    store = ArtifactStore(ctx)

    rename._retire_book_txt(store)

    archived = novel_dir / 'stage7' / '_rounds' / f'book.{prov.content_sha1(raw)}.txt'
    assert not live.exists()
    assert archived.read_bytes() == raw

    # An interrupted/repeated retirement of identical bytes is idempotent.
    live.write_bytes(raw)
    rename._retire_book_txt(store)
    assert not live.exists() and archived.read_bytes() == raw


class _MutableMap(SimpleNamespace):
    def model_copy(self, *, update):
        return _MutableMap(**(vars(self) | update))


#: 假 store 也必须有 `ctx.novel_dir`：成书的 rel 带书名，`rename.book_txt_rel(store)`
#: 从这里现算。⚠ **别在测试里手写那个文件名**（`docs/LESSONS.md` 第 21 条）。
_FAKE_CTX = SimpleNamespace(novel_dir=Path('FQ_测试书_42'))


class _TransactionalStore:
    def __init__(self, art, *, book='old delivery', fail_map_write=False,
                 book_freshness='fresh'):
        self.ctx = _FAKE_CTX
        self.art = art
        self.book = book
        self.fail_map_write = fail_map_write
        self.book_freshness = book_freshness
        self.events = []

    def exists(self, rel):
        return rel == rename.REL

    def read_own(self, _model, rel):
        assert rel == rename.REL
        return self.art, b'old-map'

    @contextmanager
    def history_transaction(self, *_rels):
        before = self.art, self.book
        try:
            yield
        except BaseException:
            self.art, self.book = before
            self.events.append('rollback')
            raise

    def write_text(self, rel, text):
        assert rel == rename.book_txt_rel(self)
        self.events.append('book')
        self.book = text
        return Path(rel)

    def write(self, rel, art):
        assert rel == rename.REL
        self.events.append('map')
        self.art = art
        if self.fail_map_write:
            raise ProvenanceRefused('injected map postcondition failure')

    def freshness(self, rel):
        assert rel == rename.book_txt_rel(self)
        return SimpleNamespace(state=self.book_freshness, reason='synthetic')


def _atomic_map():
    art = _MutableMap(
        surname_map={'沈': '陆'}, characters=[], extra_subs=[],
        approved=None, book=[], book_txt='old-account', subs={}, kept=[],
    )
    art.approved = ApprovedNameMap(
        origin='human', actor='prior-reviewer', reason='prior decision',
        subject_sha256=rename.approval_subject_sha256(art),
    )
    return art


def test_stage7_approve_restores_map_and_delivery_when_map_postcondition_fails(
        monkeypatch, tmp_path):
    art = _atomic_map()
    store = _TransactionalStore(art, fail_map_write=True)
    monkeypatch.setattr(rename, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(rename, '_Upstreams', lambda *_args: object())
    workpack_sha256 = 'f' * 64
    workpack = SimpleNamespace(
        workpack_sha256=workpack_sha256,
        review_token='s7r3_' + workpack_sha256,
        review_identity_sha256='e' * 64,
        decision_subject_sha256=rename.approval_subject_sha256(art),
        disposition='review_required', current_approval=None)
    monkeypatch.setattr(
        rename, '_approval_workpack_snapshot',
        lambda *_args: (workpack, SimpleNamespace()))

    def retire(_store):
        store.events.append('retire')
        store.book = None

    monkeypatch.setattr(rename, '_retire_book_txt', retire)

    with pytest.raises(ProvenanceRefused, match='postcondition'):
        rename.run_approve(
            SimpleNamespace(novel_dir=tmp_path),
            actor='reviewer', reason='reviewed current map',
            review_token=workpack.review_token,
        )

    assert store.art is art and store.book == 'old delivery'
    assert store.events == ['retire', 'map', 'rollback']


@pytest.mark.parametrize('fail_map_write,book_freshness', [
    (True, 'fresh'),
    (False, 'stale'),
])
def test_stage7_assemble_restores_map_and_delivery_on_any_half_commit(
            monkeypatch, tmp_path, fail_map_write, book_freshness):
    from novel.stages.stage2 import global_review
    monkeypatch.setattr(global_review, 'require_approved', lambda _ctx: None)
    # 本用例验证的是组装半提交回滚；Stage5 闭合闸自身由 test_stage5_closure
    # 覆盖，这里替换为恒通过，隔离两个关注点（assemble 在调用点做延迟 import，
    # 因此补丁打在 closure 模块属性上）。
    import novel.stages.stage5.closure as _closure
    monkeypatch.setattr(_closure, 'require_stage5_closed', lambda _store: None)
    art = _atomic_map()
    store = _TransactionalStore(
        art, fail_map_write=fail_map_write, book_freshness=book_freshness)
    upstreams = SimpleNamespace(hook_text='hook', recs=[{}])
    monkeypatch.setattr(assemble, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(assemble, '_Upstreams', lambda *_args: upstreams)
    monkeypatch.setattr(assemble, '_require_mapping_current', lambda *_args: None)
    monkeypatch.setattr(assemble, 'apply_subs', lambda *_args: ('hook', {}, set()))
    monkeypatch.setattr(assemble, 'assemble_txt', lambda *_args: 'new delivery\n')

    with pytest.raises(ProvenanceRefused):
        assemble.run_assemble(SimpleNamespace(novel_dir=tmp_path))

    assert store.art is art and store.book == 'old delivery'
    assert store.events == ['book', 'map', 'rollback']
