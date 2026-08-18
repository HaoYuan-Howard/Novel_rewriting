#!/usr/bin/env python3
"""Non-corpus guards for Stage7 approval/current-delivery boundaries."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema.report import finding
from contracts.schema.stage7 import ApprovedNameMap
from novel import prov
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
        hook_art=object(), hook_text='已批准文案', dup=[], missing=[], recs=[{}],
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

    assemble._require_mapping_current(
        SimpleNamespace(), _Store(), art, _upstreams())


def test_assembly_refuses_an_approval_bound_to_an_older_decision_surface(monkeypatch):
    art = _approved_art()
    art.surname_map = {'沈': '周'}
    monkeypatch.setattr(assemble, '_judged_drift', lambda _art, _up: ([], []))
    monkeypatch.setattr(assemble, '_hook_drift', lambda _art, _up: False)

    with pytest.raises(ApprovalRequired, match='批准不再绑定当前决策面'):
        assemble._require_mapping_current(
            SimpleNamespace(), _Store(), art, _upstreams())


def test_an_incomplete_book_reports_every_missing_piece_at_once(monkeypatch):
    """⭐ 三项**一次报全**，而且各自 typed。

    `R-10.14`：txt 是交付物，里面没有地方写风险抬头横幅——正因为成书里写不下警告，
    这道闸必须在拒绝的那一刻把差的东西一次说清。拆成 first-match 之后，
    人补完缺章才发现文案还没定稿，**一道 0 成本的闸要走三个来回**。
    ⚠ 三项恢复路各不相同（人删掉多的那一份 / 去跑 stage5 / `stage6.approve --pick N`）
    ⇒ 消费方按字段路由，不去 parse 那段人话。
    """
    from novel.stages.stage6 import semantic

    art = _approved_art()
    up = SimpleNamespace(
        hook_art=object(), hook_text='', dup=[3], missing=[7, 8], recs=[{}])
    monkeypatch.setattr(assemble, '_judged_drift', lambda _art, _up: ([], []))
    monkeypatch.setattr(assemble, '_hook_drift', lambda _art, _up: False)
    monkeypatch.setattr(semantic, 'require_approved_clear', lambda *_args: None)

    with pytest.raises(DeliveryIncomplete) as bad:
        assemble._require_mapping_current(SimpleNamespace(), _Store(), art, up)

    assert bad.value.duplicated == (3,)
    assert bad.value.missing == (7, 8)
    assert bad.value.hook_unapproved is True
    # 一次报全：三条都要在措辞里，而不是碰到第一条就返回。
    assert all(s in str(bad.value) for s in ('同章双份', '计划有而成稿无', '开篇文案'))


def test_retired_book_leaves_the_live_download_path_but_remains_recoverable(tmp_path):
    novel_dir = tmp_path / '书名_123'
    live = novel_dir / 'stage7' / 'book.txt'
    live.parent.mkdir(parents=True)
    raw = '旧交付物\n'.encode('utf-8')
    live.write_bytes(raw)
    ctx = state.readonly_ctx(novel_dir, 'stage7.rename', prov.code_version())
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


class _TransactionalStore:
    def __init__(self, art, *, book='old delivery', fail_map_write=False,
                 book_freshness='fresh'):
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
        assert rel == rename.BOOK_TXT
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
        assert rel == rename.BOOK_TXT
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
        monkeypatch):
    art = _atomic_map()
    store = _TransactionalStore(art, fail_map_write=True)
    monkeypatch.setattr(rename, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(rename, '_Upstreams', lambda *_args: object())

    def retire(_store):
        store.events.append('retire')
        store.book = None

    monkeypatch.setattr(rename, '_retire_book_txt', retire)

    with pytest.raises(ProvenanceRefused, match='postcondition'):
        rename.run_approve(
            SimpleNamespace(), actor='reviewer', reason='reviewed current map',
            subject_sha256=rename.approval_subject_sha256(art),
        )

    assert store.art is art and store.book == 'old delivery'
    assert store.events == ['retire', 'map', 'rollback']


@pytest.mark.parametrize('fail_map_write,book_freshness', [
    (True, 'fresh'),
    (False, 'stale'),
])
def test_stage7_assemble_restores_map_and_delivery_on_any_half_commit(
        monkeypatch, fail_map_write, book_freshness):
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
        assemble.run_assemble(SimpleNamespace())

    assert store.art is art and store.book == 'old delivery'
    assert store.events == ['book', 'map', 'rollback']
