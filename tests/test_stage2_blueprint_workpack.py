"""当前 Blueprint 只读阅读工作包（审阅已退役）的身份、材料与拒绝路径。

原 ``BlueprintReviewWorkpack``/``BlueprintSeenRequest`` 的审阅 token、已读凭证与
接受语义已整体退役（Git 保留历史反例）；现役入口是
``GET /{sid}/stage2/blueprint-reading-workpack``（``novel.views.stage2.blueprint_reading_workpack``）：
原文与完整剧情节点各一份，机械可用性由同一份 ``--check-only`` 报告披露，
阅读身份摘要只绑定实际读取的输入字节，不产生任何审批待办。
"""
from __future__ import annotations

import pytest

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
)
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints
from novel import prov
from novel.errors import HumanEditProtected, UpstreamMissing
from novel.prov import FreshnessReport
from novel.report.builder import build
from novel.stages.stage2 import characters as characters_module
from novel.stages.stage2 import checks, extract
from novel.store import ArtifactStore
from novel.views import stage2 as view
from tests.test_plot_producer import generate_plot, prepare_book, plot_contract  # noqa: F401


@pytest.fixture
def book(tmp_path, plot_contract):
    path = prepare_book(tmp_path)
    generate_plot(path)
    return path


def _real_report(book):
    from contracts.schema.characters import CharacterRegistry
    from novel.orchestrator.state import readonly_ctx
    ctx = readonly_ctx(book, 'stage2.blueprint', prov.code_version())
    store = ArtifactStore(ctx)
    return build(sid=store.ctx.sid, node='stage2.blueprint',
        freshness=FreshnessReport('fresh', 'match'), outcomes=checks.check(
            global_g=store.read(
                KnowledgeGrounding, extract.GLOBAL_REL).model_dump(mode='json', by_alias=True),
            characters=store.read(CharacterRegistry, characters_module.REL).model_dump(
                mode='json', by_alias=True),
            blueprints=store.read(ChapterBlueprints, extract.BLUEPRINT_REL).model_dump(
                mode='json', by_alias=True),
            main_text=store.read(MainText, extract.MAIN_TEXT).model_dump(mode='json')))


def test_reading_workpack_exposes_whole_book_with_real_check(book):
    pack = view.blueprint_reading_workpack(book.parent, book.name)
    assert pack.read_only and pack.scope == 'cross_chapter'
    assert [row['chapter_ordinal'] for row in pack.material['sources']] == [1, 2, 3]
    assert [row['chapter_ordinal'] for row in pack.material['blueprints']] == [1, 2, 3]
    assert pack.material['sources'][0]['text']
    assert pack.check.rules_scanned > 0 and pack.accounting
    assert pack.progress.total_chapters == 3
    assert pack.progress.artifact_state == 'formal'
    # 审阅已退役：工作包不携带 token/审批语义字段。
    assert 'review_token' not in type(pack).model_fields
    assert 'auto_decisions' not in type(pack).model_fields


def test_manual_scope_selection_returns_that_chapter_only(book):
    chosen = view.blueprint_reading_workpack(book.parent, book.name, scope='chapter:3')
    assert chosen.scope == 'chapter:3'
    assert [row['chapter_ordinal'] for row in chosen.material['sources']] == [3]
    assert [row['chapter_ordinal'] for row in chosen.material['blueprints']] == [3]
    default = view.blueprint_reading_workpack(book.parent, book.name)
    assert default.scope == 'cross_chapter'
    with pytest.raises(UpstreamMissing, match='不在当前原文章集'):
        view.blueprint_reading_workpack(book.parent, book.name, scope='chapter:9')


def test_reading_identity_binds_inputs_not_code_epoch(book):
    from tests.test_stage2_generation import _ctx as _writable_ctx
    baseline = view.blueprint_reading_workpack(book.parent, book.name)
    again = view.blueprint_reading_workpack(book.parent, book.name)
    assert baseline.binding_sha256 == again.binding_sha256
    assert baseline.subject_sha256 == again.subject_sha256
    # 阅读身份不绑代码时代：同一输入在不同 code 下身份稳定。
    other_epoch = view.blueprint_reading_workpack(book.parent, book.name, code='other-epoch')
    assert other_epoch.binding_sha256 == baseline.binding_sha256

    # 可见内容变化 ⇒ 蓝图字节与阅读身份都变化。
    store = ArtifactStore(_writable_ctx(book, 'stage2.blueprint', paid=False))
    current = store.read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    raw = current.model_dump(mode='json', by_alias=True)
    raw['blueprints'][0]['beats'][0]['content'] += '新的可见语义'
    store.write(extract.BLUEPRINT_REL, ChapterBlueprints.model_validate(raw))
    after_content = view.blueprint_reading_workpack(book.parent, book.name)
    assert after_content.subject_sha256 != baseline.subject_sha256
    assert after_content.binding.main_text_artifact_sha256 == baseline.binding.main_text_artifact_sha256


def test_check_report_only_change_rolls_identity_through_binding(book, monkeypatch):
    baseline = view.blueprint_reading_workpack(book.parent, book.name)
    real_report = _real_report(book)
    changed = real_report.model_copy(deep=True)
    changed.rules[0] = changed.rules[0].model_copy(update={'scanned': '新诊断读数'})
    monkeypatch.setattr(extract, 'run_check_only', lambda *_: changed)
    after = view.blueprint_reading_workpack(book.parent, book.name)
    assert after.check.rules_scanned == baseline.check.rules_scanned
    assert after.binding.check_report_sha256 != baseline.binding.check_report_sha256
    assert after.binding_sha256 != baseline.binding_sha256
    # 报告主体一致：材料与蓝图字节没变。
    assert after.material == baseline.material


def test_workpack_refuses_check_report_subject_mismatch(book, monkeypatch):
    real_report = _real_report(book)
    forged = real_report.model_copy(update={
        'provenance': real_report.provenance.model_copy(update={'invalid': True})})
    monkeypatch.setattr(extract, 'run_check_only', lambda *_: forged)
    with pytest.raises(HumanEditProtected, match='阅读校验报告与实际主体不一致'):
        view.blueprint_reading_workpack(book.parent, book.name)


def test_reading_workpack_reads_the_invalid_subject_when_it_is_live(book):
    from tests.test_stage2_generation import _ctx as _writable_ctx
    store = ArtifactStore(_writable_ctx(book, 'stage2.blueprint', paid=False))
    current = store.read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    raw = current.model_dump(mode='json', by_alias=True)
    raw['blueprints'][0]['beats'][0]['content'] += '问题稿'
    store.write_invalid(extract.BLUEPRINT_REL, ChapterBlueprints.model_validate(raw),
                        gates_skipped=[])
    pack = view.blueprint_reading_workpack(book.parent, book.name)
    assert pack.binding.subject_kind == 'invalid'
    assert pack.binding.blueprint_rel != 'stage2/chapter_blueprints.json'
    assert [row['chapter_ordinal'] for row in pack.material['blueprints']] == [1, 2, 3]


def test_live_code_drift_does_not_change_reading_identity(book, monkeypatch):
    """阅读身份只绑输入字节；代码时代变化不撤回已下发的阅读材料。"""
    first = view.blueprint_reading_workpack(book.parent, book.name, code='epoch-a')
    monkeypatch.setattr(view.prov, 'code_version', lambda: 'epoch-b')
    second = view.blueprint_reading_workpack(book.parent, book.name)
    assert first.binding_sha256 == second.binding_sha256
    assert first.subject_sha256 == second.subject_sha256
