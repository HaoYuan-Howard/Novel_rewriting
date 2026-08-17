#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_secret_edit.py —— `stage2.global --apply-edit`：Stage2 typed facts 的 0 成本人工订正。

【它守的是什么】细粒度 patch 先补上已有 secret 字段的修订入口；完整 replace surface
继续接住 patch 无法表达的集合级决定：拆分复合 secret、增删 world rule、重建 address 列表。
两条路都必须有严格 schema、来源账、同一归一/判据、轮次归档和 global-review 刷新。

【四条各对应一次会静默发生的损失】
  · **越界章号**  —— 越界的揭示章让逐章剧透闸门**永不触发**（`R-6.3` 按它开闸），
                    而产物里那一格看着一切正常 ⇒ 落盘之前显式终态；
  · **未知字段**  —— 人手写的 JSON 里一个拼错的键会被静默忽略，
                    于是「我改过了」与「什么都没改」在盘上同形 ⇒ 载体 `extra='forbid'`；
  · **同一判定**  —— 人工稿绕过闸门就是给自己开一个免检写口 ⇒ HARD 走 `.INVALID`，
                    **正式全书底座不被覆盖**；
  · **下游 stale** —— 底座变了而下游还按旧的算，是全链最贵的一类错
                    ⇒ 由字节指纹与字段投影两条边同时保证。

⚠ 本文件**全程合成语料**，不依赖 `NOVEL_OUTPUT`：底座三章来自 `tests/test_stage2.py`。
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schema import encode_artifact
from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalEdit, GlobalGrounding
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState
from novel.orchestrator.state import readonly_ctx
from novel.quality.global_review import decide
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.test_stage2_generation import (
    _client, _ctx, _ok, _response_blueprints, _response_global,
)
from tests.test_stage2 import main_text

BLUEPRINT_EDGE = 'stage2/global_grounding.json'


def _seed(book: Path, *, with_blueprints: bool = False) -> None:
    """把这本书跑到「有正式全书底座」（可选：连逐章蓝图一起）。**一次真调用都不发。**"""
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
    client, _transport = _client(global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, client)
    if not with_blueprints:
        return
    by_ordinal = _response_blueprints()

    def respond(req, _index):
        ordinals = re.findall(r'阅读序第(\d+)章', req.user)
        return _ok({'blueprints': [by_ordinal[int(ordinals[-1])]]})

    blueprint_ctx = _ctx(book, 'stage2.blueprint')
    bp_client, _bp_transport = _client(blueprint_ctx, respond)
    extract.run_blueprint(blueprint_ctx, bp_client)


def _write_edit(book: Path, payload: dict) -> None:
    """把人写的当前版本决策集落到书目录里。"""
    (book / 'stage2').mkdir(parents=True, exist_ok=True)
    (book / extract.GLOBAL_EDIT).write_text(
        json.dumps(encode_artifact(extract.GLOBAL_EDIT, payload),
                   ensure_ascii=False, indent=2),
        encoding='utf-8', newline='\n')


def _apply(book: Path):
    return extract.run_apply_edit(_ctx(book, 'stage2.global', paid=False))


def _sandbox(name: str) -> tuple[Path, Path]:
    root = Path(tempfile.mkdtemp())
    book = root / name
    book.mkdir()
    return root, book


# ─────────────────────────────────────────────────────────────────────────────
# 一、⭐ 主样本：错误揭示章 → 人工修正 → 重判 → 下游 stale
# ─────────────────────────────────────────────────────────────────────────────

def test_a_wrong_reveal_chapter_is_corrected_and_the_downstream_turns_stale():
    """⭐⭐ TODO-05 的验收样本，四段一次跑完。

    合成底座里 `secret_01` 的读者揭示章是第 2 章。假设那是抽错的（真相其实在第 3 章
    才点破）⇒ 人写一份决策集把它改到 3，跑 `--apply-edit`：

      ① **改真的落进产物**（不是只改了报告）；
      ② **重走归一**：埋点章 `[1]` 仍严格早于新揭示章 ⇒ 留着；
      ③ **重判**：闸门跑过、报告出得来；
      ④ **下游 stale**：逐章蓝图记的那条 `stage2/global_grounding.json` 边
         同时在**字节指纹**与**字段投影**两侧失配。
         ⚠ 第四段是本条最值钱的一段：底座改了而下游还按旧的算，
         是全链最贵的一类错，而它**不会报错**。
    """
    root, book = _sandbox('揭示章订正_001')
    try:
        _seed(book, with_blueprints=True)
        bp_ctx = _ctx(book, 'stage2.blueprint', paid=False)
        assert ArtifactStore(bp_ctx).freshness(extract.BLUEPRINT_REL).state == 'fresh', \
            '底座还没动，下游就已经 stale？那这条测试后面什么也证明不了'

        _write_edit(book, {'secrets': [
            {'secret_id': 'secret_01', 'reader_reveal_events': [{'chapter': 3}]}]})
        artifact, report = _apply(book)

        # ① 改真的落进产物
        secret = artifact.canon.secrets[0]
        assert [event.chapter for event in secret.reader_reveal_events] == [3]
        reloaded = ArtifactStore(_ctx(book, 'stage2.global', paid=False)).read(
            GlobalGrounding, extract.GLOBAL_REL)
        assert [e.chapter for e in reloaded.canon.secrets[0].reader_reveal_events] == [3]

        # ② 重走归一：埋点章 1 < 新揭示章 3 ⇒ 不该被裁掉
        assert secret.setup_chapters == [1]
        assert reloaded.provenance.setup_chapters_pruned == []

        # ③ 重判真的跑过（不是把旧报告抄一遍）
        assert report.node == 'stage2.global' and report.rules
        assert {r.rule for r in report.rules if r.status == 'skipped'} == set(), \
            '有判据没排上——那一行会绿着而什么都没查'

        # ④ 下游 stale：两条边各说一句话
        fresh = ArtifactStore(bp_ctx).freshness(extract.BLUEPRINT_REL)
        assert fresh.state == 'stale', '底座换了而下游仍报 fresh——最贵的那类错'
        edge = next(e for e in fresh.edges if e.key == BLUEPRINT_EDGE)
        assert edge.state == 'stale'
        assert edge.recorded_file != edge.now_file, '字节指纹没动？那底座根本没被写回去'
        assert edge.recorded_consumed != edge.now_consumed, \
            '改的是 secrets，而逐章剧透闸门正是消费它的那一个——字段投影必须跟着变'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_correcting_the_reveal_chapter_backwards_reprunes_and_then_hits_the_same_gate():
    """⭐ 人把揭示章往**前**改时，走的是与生成路**一模一样**的那三步。

    埋点章 `[1]` 在揭示章改到第 1 章之后不再严格早于它（`R-2.9`）⇒
      ① `_prune_setups` 把它裁掉，并把裁剪账记进 `_provenance.setup_chapters_pruned`
         （「冷揭示」与「被裁空」在字段值上完全同形，只能靠那个计数分开）；
      ② `secret_setup_order`（HARD）**断言的正是那个计数**⇒ 命中 ⇒ 问题稿进 `.INVALID`，
         正式底座不动。⚠ **这不是人工路的特例**：`run_global` 在同一份数据上
         也是这个下场（见 `test_stage2_global_renormalize_promotes_deterministically_pruned_invalid`）
         ——「裁过一刀」这件事必须让人看见，不许静默应用；
      ③ 0 成本出路是 `--renormalize`：再归一时已经没什么可裁，计数归零、提升为正式稿。

    ⚠ 本条是「人工稿要重走 normalize、当前判据和落盘闸门」这句话的完整形态：
    只测到 ① 会让人以为人工路可以绕过 ②。
    """
    root, book = _sandbox('揭示章前移_002')
    try:
        _seed(book)
        before = (book / extract.GLOBAL_REL).read_bytes()
        _write_edit(book, {'secrets': [
            {'secret_id': 'secret_01', 'reader_reveal_events': [{'chapter': 1}]}]})

        with pytest.raises(GateBlocked, match='secret_setup_order'):
            _apply(book)
        assert (book / extract.GLOBAL_REL).read_bytes() == before, '正式底座被覆盖了'

        invalid = json.loads((book / extract._GLOBAL_INVALID).read_text(encoding='utf-8'))
        assert invalid['canon']['secrets'][0]['setup_chapters'] == []
        assert invalid['_provenance']['setup_chapters_pruned'] == [['secret_01', [1], []]]

        report = extract.run_renormalize(_ctx(book, 'stage2.global', paid=False))
        assert report.passed
        stored = ArtifactStore(_ctx(book, 'stage2.global', paid=False)).read(
            GlobalGrounding, extract.GLOBAL_REL)
        assert [e.chapter for e in stored.canon.secrets[0].reader_reveal_events] == [1]
        assert stored.canon.secrets[0].setup_chapters == []
        assert stored.provenance.setup_chapters_pruned == []
        assert not (book / extract._GLOBAL_INVALID).exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_other_two_editable_fields_land_too():
    """知情人与 secret 事实——TODO-05 点名的另外两处抽错，同样改得动。"""
    root, book = _sandbox('知情人订正_003')
    try:
        _seed(book)
        _write_edit(book, {'secrets': [{
            'secret_id': 'secret_01',
            'fact': '父亲早已回城，只是被债主扣在城南客栈里',
            'character_known_by': ['char_001', 'char_002'],
        }]})
        artifact, _report = _apply(book)
        secret = artifact.canon.secrets[0]
        assert secret.fact.endswith('客栈里')
        assert secret.character_known_by == ['char_001', 'char_002']
        # 没给的字段一个都不许动
        assert [e.chapter for e in secret.reader_reveal_events] == [2]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_complete_typed_surfaces_reach_disk_and_rebuild_the_human_review():
    """A compound secret can be split without bypassing the real consumer.

    ``Stage4`` exposes a secret's whole fact after the earliest reveal event;
    adding a second event to one compound fact therefore cannot express two
    independently revealed facts.  The complete-set edit splits that row,
    also replaces an omitted world-rule set and a character's complete address
    surface, then ``run_apply_edit`` rebuilds ``global_review`` from the landed
    artifact.  Old decisions bind to the old hashes and must not follow it.
    """
    root, book = _sandbox('集合订正_003b')
    try:
        _seed(book)
        ctx = _ctx(book, 'stage2.global', paid=False)
        store = ArtifactStore(ctx)
        old_review = store.read(GlobalReview, extract.GLOBAL_REVIEW_REL)
        old_secret_rows = [
            item for item in old_review.items
            if item.kind in {'secret_set_complete', 'secret', 'known_by', 'reveal'}]
        assert old_secret_rows
        for item in old_secret_rows:
            old_review = decide(
                old_review,
                item_id=item.item_id,
                expected_subject_sha256=item.subject_sha256,
                state='PASS',
                actor='test-reviewer',
                reason='synthetic source evidence checked before replacement',
                now='2026-08-17T00:00:00+00:00',
            )
        store.write(extract.GLOBAL_REVIEW_REL, old_review)
        old_item_ids = {item.item_id for item in old_secret_rows}

        _write_edit(book, {
            'secrets_replace': [{
                'secret_id': 'secret_01',
                'fact': '父亲已经回城',
                'character_known_by': ['char_002'],
                'setup_chapters': [1],
                'reader_reveal_events': [{'chapter': 3}],
            }, {
                'secret_id': 'secret_02',
                'fact': '母亲早就知道那封信的事',
                'character_known_by': ['char_001'],
                'setup_chapters': [],
                'reader_reveal_events': [{'chapter': 2}],
            }],
            'world_rules_replace': [{
                'rule_id': 'wr_02',
                'kind': 'legal_rule',
                'versions': [{
                    'effective_from_chapter': 1,
                    'desc': '城中夜间梆子响过以后实行宵禁，行人须留在住所直到天明',
                }],
            }],
            'addresses_replace': [{
                'char_id': 'char_002',
                'address': [{
                    'surface': '母亲', 'by': 'public', 'context': 'address',
                    'from_chapter': 2,
                }, {
                    'surface': '母亲', 'by': 'char_001', 'context': 'reference',
                    'from_chapter': 2,
                }],
            }],
        })
        artifact, report = _apply(book)

        assert report.node == 'stage2.global'
        assert [row.secret_id for row in artifact.canon.secrets] == [
            'secret_01', 'secret_02']
        assert [row.rule_id for row in artifact.canon.world_rules] == ['wr_02']
        assert [(row.surface, row.by, row.context, row.from_chapter)
                for row in artifact.entities.characters['char_002'].address] == [
            ('母亲', 'public', 'address', 2),
            ('母亲', 'char_001', 'reference', 2),
        ]

        rebuilt = ArtifactStore(_ctx(book, 'stage2.global', paid=False)).read(
            GlobalReview, extract.GLOBAL_REVIEW_REL)
        secret_rows = [
            item for item in rebuilt.items
            if item.kind in {'secret_set_complete', 'secret', 'known_by', 'reveal'}]
        assert len(secret_rows) == 7  # set completeness + 2 × fact/known-by/reveal
        assert all(item.state == 'pending' for item in secret_rows)
        assert old_item_ids.isdisjoint({item.item_id for item in secret_rows})
        assert {item.subject_path for item in secret_rows if item.kind == 'secret'} == {
            'canon.secrets.secret_01.fact', 'canon.secrets.secret_02.fact'}
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 二、来源账：这一份此后是混版的，而账要说得出口
# ─────────────────────────────────────────────────────────────────────────────

def test_the_human_round_inherits_the_paid_ledger_and_marks_the_partial_rewrite():
    """⚠ **`prompts` / `model` 一个字不动，`partial_rewrite` 标上。**

    这一份此后是**混版的**：`entities` / `arc` 出自当初那次付费产出，
    被改的 secret 是现在由人给的。三个记法各错一半——沿用旧账说不清「照哪份原文算的」·
    整份覆盖等于宣称本次调过模型（伪造）· 借 `revalidated` 说的是状态变更而内容没动。
    """
    root, book = _sandbox('来源账_004')
    try:
        _seed(book)
        store = ArtifactStore(_ctx(book, 'stage2.global', paid=False))
        before = store.read(GlobalGrounding, extract.GLOBAL_REL).provenance

        _write_edit(book, {'secrets': [
            {'secret_id': 'secret_01', 'fact': '父亲早已回城，只是不肯认这笔账'}]})
        artifact, _report = _apply(book)

        after = artifact.provenance
        assert after.prompts == before.prompts, '重盖了 prompt 指纹 —— 那是伪造'
        assert after.model == before.model
        assert after.partial_rewrite == '--apply-edit'
        assert after.gates_skipped is None
        assert after.usage is not None and after.usage.calls_made == before.usage.calls_made, \
            '0 成本路把调用数改了？那本账就不是账了'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_previous_round_is_archived_before_the_human_round_lands():
    """覆盖前有处可回：旧的正式底座进 `_rounds/`。"""
    root, book = _sandbox('轮次归档_005')
    try:
        _seed(book)
        before = (book / extract.GLOBAL_REL).read_bytes()
        _write_edit(book, {'secrets': [
            {'secret_id': 'secret_01', 'character_known_by': ['char_002']}]})
        _apply(book)
        archived = sorted(p.name for p in (book / 'stage2' / '_rounds').iterdir())
        assert archived == ['global_grounding.r1.json', 'global_review.r1.json']
        assert (book / 'stage2' / '_rounds' / 'global_grounding.r1.json').read_bytes() == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_global_and_review_roll_back_as_one_history_byte_set(monkeypatch):
    """The companion review cannot leave a successfully replaced global behind."""
    root, book = _sandbox('组合事务_005b')
    try:
        _seed(book)
        _write_edit(book, {'secrets': [{
            'secret_id': 'secret_01',
            'character_known_by': ['char_001', 'char_002'],
        }]})
        stage = book / 'stage2'
        before = {
            path.relative_to(stage).as_posix(): path.read_bytes()
            for path in stage.rglob('*') if path.is_file()
        }
        original_write = ArtifactStore.write

        def fail_review(self, rel, artifact):
            if rel == extract.GLOBAL_REVIEW_REL:
                raise OSError('模拟 global review 刷新失败')
            return original_write(self, rel, artifact)

        monkeypatch.setattr(ArtifactStore, 'write', fail_review)
        with pytest.raises(OSError, match='review 刷新失败'):
            _apply(book)

        after = {
            path.relative_to(stage).as_posix(): path.read_bytes()
            for path in stage.rglob('*') if path.is_file()
        }
        assert after == before, (
            'global 已换新而 review 失败时，formal/INVALID/review/'
            '_rounds/_revalidation 必须整组回到事务前')
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 三、反例：四条各挡一种会静默发生的错
# ─────────────────────────────────────────────────────────────────────────────

def test_an_unknown_field_is_refused_by_the_carrier():
    """未知字段 ⇒ 载体当场拒收。**拼错一个键不许被静默忽略**——
    那会让「我改过了」与「什么都没改」在盘上同形。"""
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'secrets': [
            {'secret_id': 'secret_01', 'reveal_chapter': 3}]})           # 正确的是 reader_reveal_events
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'secret': [{'secret_id': 'secret_01', 'fact': 'x'}]})


def test_an_empty_patch_is_refused():
    """一个字段都没给的补丁 ⇒ 拒收。空补丁什么都不改，
    而它在盘上与「人看过并改了」长得一样。"""
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'secrets': [{'secret_id': 'secret_01'}]})
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'secrets': []})


def test_two_patches_for_one_secret_are_refused():
    """同一条 secret 给两份补丁 ⇒ 拒收：哪一份生效取决于遍历顺序，
    而那意味着**人的决定被实现细节改写**。"""
    with pytest.raises(ValidationError):
        GlobalEdit.model_validate({'secrets': [
            {'secret_id': 'secret_01', 'fact': 'A'},
            {'secret_id': 'secret_01', 'fact': 'B'}]})


@pytest.mark.parametrize('payload,why', [
    ({'secrets': [{'secret_id': 'secret_01', 'reader_reveal_events': [{'chapter': 9}]}]},
     '揭示章越界'),
    ({'secrets': [{'secret_id': 'secret_01', 'setup_chapters': [7]}]},
     '埋点章越界'),
])
def test_an_out_of_range_chapter_is_refused_before_anything_is_written(payload, why):
    """⭐ 越界章号 ⇒ **显式终态，且一个字节都没落盘。**

    ⚠ 它是 `TerminalState` **不是** `GateBlocked`：后者说「这份**产物**过不了闸」
    （问题稿留 `.INVALID`，人接着改），而这里错的是**决策集本身**——
    把一份写着第 9 章的编辑文件落成 `.INVALID`，等于给一个打不开的东西建一份归档。
    ⚠ 越界之所以危险，是因为它**不报错**：越界的揭示章让逐章剧透闸门永不触发，
    而产物里那一格看着一切正常。
    """
    root, book = _sandbox('越界章号_006')
    try:
        _seed(book)
        before = (book / extract.GLOBAL_REL).read_bytes()
        _write_edit(book, payload)
        with pytest.raises(TerminalState, match='章号不在这本书的章集合内'):
            _apply(book)
        assert (book / extract.GLOBAL_REL).read_bytes() == before, f'{why}：正式底座被动过了'
        assert not (book / extract._GLOBAL_INVALID).exists(), \
            f'{why}：决策集自己错了，不该给它建一份 `.INVALID` 归档'
        assert not (book / 'stage2' / '_rounds').exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_an_unknown_secret_id_is_refused_with_the_current_set_spelled_out():
    """指向盘上没有的 secret ⇒ 显式终态，并把现有集合写出来（人才知道该改什么）。"""
    root, book = _sandbox('未知secret_007')
    try:
        _seed(book)
        _write_edit(book, {'secrets': [{'secret_id': 'secret_09', 'fact': '凭空一条'}]})
        with pytest.raises(TerminalState, match='secret_01'):
            _apply(book)
        assert not (book / extract._GLOBAL_INVALID).exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_dangling_known_by_reference_is_rejected_before_any_artifact_write():
    """A typed registry reference is an invalid decision, not a bad candidate.

    Waiting for ``secret_known_by_dangling`` would first mutate/normalize the
    candidate and then create an INVALID round.  Target and reference validity
    are instead checked atomically before any artifact or archive write.
    """
    root, book = _sandbox('闸门拦下_008')
    try:
        _seed(book)
        before = (book / extract.GLOBAL_REL).read_bytes()
        _write_edit(book, {'secrets': [
            {'secret_id': 'secret_01', 'character_known_by': ['char_777']}]})
        with pytest.raises(TerminalState, match='char_777'):
            _apply(book)
        assert (book / extract.GLOBAL_REL).read_bytes() == before
        assert not (book / extract._GLOBAL_INVALID).exists()
        assert not (book / 'stage2' / '_rounds').exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_missing_edit_file_and_a_missing_base_are_two_different_terminal_states():
    """两种「没得改」要分开说：**缺决策集**与**缺前稿**指向的下一步完全不同。"""
    root, book = _sandbox('缺件_009')
    try:
        _seed(book)
        with pytest.raises(TerminalState, match='global_edit.json'):
            _apply(book)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    root2, book2 = _sandbox('缺前稿_010')
    try:
        (book2 / 'stage2').mkdir(parents=True)
        ArtifactStore(_ctx(book2, 'stage2.global')).write(
            extract.MAIN_TEXT, MainText.model_validate(main_text()))
        _write_edit(book2, {'secrets': [{'secret_id': 'secret_01', 'fact': 'x'}]})
        with pytest.raises(Exception) as caught:
            _apply(book2)
        assert 'stage2.global' in str(caught.value), \
            '缺前稿时要说「先跑一次 stage2.global」，不是抱怨编辑文件'
    finally:
        shutil.rmtree(root2, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 四、契约登记：这条路要在三张表上同时存在
# ─────────────────────────────────────────────────────────────────────────────

def test_the_entry_is_registered_everywhere_it_has_to_be():
    """⚠ 少登记一张表的后果各不相同，而**没有一个会报错**：
    不在 `NODES[...].flags` ⇒ `build_argv` 当场拒收（这条路敲不出来）·
    不在 `ARTIFACTS` ⇒ 编辑文件的形状没有任何东西在管 ·
    不在 `FREE_ENTRIES` ⇒ 界面把一次 0 成本编辑画成付费动作并弹二次确认。
    """
    from contracts.invariants import FREE_ENTRIES, NODES, VALUED_FLAGS
    from contracts.schema import ARTIFACTS

    assert '--apply-edit' in NODES['stage2.global'].flags
    assert ARTIFACTS[extract.GLOBAL_EDIT] is GlobalEdit
    assert '--apply-edit' in FREE_ENTRIES and '--apply-edit' in VALUED_FLAGS


def test_the_blueprint_node_has_no_secret_edit_entry():
    """`--apply-edit` 只挂 `stage2.global`。逐章蓝图的人工路是 `--chapters` 定向重生成
    ——两个节点两份产物，一个 flag 不许同时代表两件事。"""
    from contracts.invariants import NODES

    assert '--apply-edit' not in NODES['stage2.blueprint'].flags
    root, book = _sandbox('走错节点_011')
    try:
        _seed(book)
        _write_edit(book, {'secrets': [{'secret_id': 'secret_01', 'fact': 'x'}]})
        with pytest.raises(TerminalState, match='stage2.global'):
            extract.run_apply_edit(_ctx(book, 'stage2.blueprint', paid=False))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_edit_file_is_recorded_as_an_input_edge():
    """决策集**进来源账**：它落在产物目录里、被 `store.read` 读走 ⇒ 那一读要留一条边。

    没有这条边，「这一版底座是照哪份人工决策改的」事后说不出口。
    """
    root, book = _sandbox('来源边_012')
    try:
        _seed(book)
        _write_edit(book, {'secrets': [{'secret_id': 'secret_01', 'fact': '改一个字'}]})
        artifact, _report = _apply(book)
        assert extract.GLOBAL_EDIT in artifact.provenance.inputs, \
            f'来源账里没有 {extract.GLOBAL_EDIT} 这条边：{sorted(artifact.provenance.inputs)}'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stale_blueprints_are_displayable_but_not_consumable_after_base_edit():
    """人工订正后，旧蓝图可供只读诊断，但不得再进生产链或付费请求。"""
    root, book = _sandbox('下游可读_013')
    try:
        _seed(book, with_blueprints=True)
        _write_edit(book, {'secrets': [
            {'secret_id': 'secret_01', 'reader_reveal_events': [{'chapter': 3}]}]})
        _apply(book)
        production_store = ArtifactStore(_ctx(book, 'stage2.blueprint', paid=False))
        with pytest.raises(ProvenanceRefused, match='已过期'):
            production_store.read(ChapterBlueprints, extract.BLUEPRINT_REL)

        display_store = ArtifactStore(readonly_ctx(
            book, 'stage2.blueprint', 'test:stale-blueprint-display'))
        stored = display_store.read(ChapterBlueprints, extract.BLUEPRINT_REL)
        assert [bp.chapter_ordinal for bp in stored.blueprints] == [1, 2, 3]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_patch_does_not_touch_anything_outside_the_named_secret():
    """只动点名的那条 secret 的那几格；其余全书事实**逐字节不变**。"""
    root, book = _sandbox('最小改动_014')
    try:
        _seed(book)
        store = ArtifactStore(_ctx(book, 'stage2.global', paid=False))
        _artifact_before, before = store.read_own(GlobalGrounding, extract.GLOBAL_REL)
        _write_edit(book, {'secrets': [
            {'secret_id': 'secret_01', 'reader_reveal_events': [{'chapter': 3}]}]})
        _apply(book)
        _artifact_after, after = ArtifactStore(
            _ctx(book, 'stage2.global', paid=False)).read_own(
                GlobalGrounding, extract.GLOBAL_REL)

        expected = copy.deepcopy(before)
        expected['canon']['secrets'][0]['reader_reveal_events'] = [{'chapter': 3}]
        expected.pop('_provenance')
        got = copy.deepcopy(after)
        got.pop('_provenance')
        assert got == expected, '改动溢出了点名的那一格'
    finally:
        shutil.rmtree(root, ignore_errors=True)
