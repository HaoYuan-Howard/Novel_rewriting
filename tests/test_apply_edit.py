#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三条 `--apply-edit` 人工修订路（stage3 / stage4 / stage6）——BE-02 的完成判据。

⚠ **stage2 那条在 `tests/test_stage2_secret_edit.py`**（TODO-05 补的 secret 订正入口）：
它同样守下面这四件事，但**全程合成语料**——本文件的四条都要盘上有对应产物，
而 secret 的越界章号、未知 `secret_id` 那几个反例在真语料上造不出来。
两个文件不是重复：同一把尺子，一个量真产物、一个量构造得出来的反例。

一把尺子四件事，三层逐一钉住：
  · **同一判定落点**：人工稿走与生成稿完全相同的判据（没有免检写口）；
  · **闸门语义与生成稿一致**：stage3/4 任何 HARD ⇒ `.INVALID` 不覆盖正式稿，
    stage6 不整拦（本层闸门是「无一干净候选才拦」）、红行照报；
  · **轮次归档**：旧轮进 `_rounds/`；
  · **来源账不伪造**：`rewrite_provenance` 继承 `prompts`/`model`、标 `partial_rewrite`。

⚠ 盘上语料是只读证据；写盘全部发生在 `sandbox.Sandbox` 的 mkdtemp 拷贝里。
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from novel.errors import GateBlocked, TerminalState
from novel.run import RunContext
from tests import corpus, sandbox

STAGE4_INPUTS = ('stage1/main_text.json', 'stage2/global_grounding.json',
                 'stage2/chapter_blueprints.json', 'stage4/plan_chapters.json')
STAGE6_INPUTS = ('stage1/hook_copy.json', 'stage2/global_grounding.json',
                 'stage3/style_grounding.json', 'stage4/writer_context.json',
                 'stage6/hook.json')


def _first_book(rel: str) -> str | None:
    books = sorted({a.book_dir for a in corpus.iter_artifacts() if a.rel == rel})
    return books[0] if books else None

pytestmark = [
    pytest.mark.corpus,
    pytest.mark.skipif(
        _first_book('stage3/style_grounding.json') is None,
        reason='盘上没有语料——这几条**没在查**，不是「查过没事」'),
]


def _drafts_of(book: str) -> tuple[str, ...]:
    return tuple(sorted(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == book and a.rel.startswith('stage5/')
        and '_rounds' not in a.rel and a.rel.endswith('.json')))


@pytest.fixture
def box3():
    s = sandbox.Sandbox(_first_book('stage3/style_grounding.json'))
    yield s
    s.close()


def _stage4_book() -> str | None:
    """挑一本 `plan_chapters.json` 带来源账的书。

    ⚠ 语料里有一本已知残次品（`成年人不做选择` 那本根本没有 `_provenance`，
    `orchestrator/state.py` 点过名）——它按字母序恰好排第一，别撞上去。
    """
    for book in sorted({a.book_dir for a in corpus.iter_artifacts()
                        if a.rel == 'stage4/plan_chapters.json'}):
        data = sandbox.cut(book, 'stage4/plan_chapters.json')
        if data and data.get('_provenance'):
            return book
    return None


@pytest.fixture
def box4():
    book = _stage4_book()
    if book is None:
        pytest.skip('语料里没有带来源账的 stage4 计划——本组**没在查**')
    s = sandbox.Sandbox(book, STAGE4_INPUTS)
    yield s
    s.close()


@pytest.fixture
def box6():
    book = _first_book('stage6/hook.json')
    s = sandbox.Sandbox(book, STAGE6_INPUTS + _drafts_of(book))
    yield s
    s.close()


# ── stage3 ──────────────────────────────────────────────────────────────────

def test_stage3_apply_edit_patches_walk_the_same_judge_and_remeasure(box3):
    """补丁落盘 = 同一判据 + `measurable` 照盘上 stage1 重算 + 归档 + partial_rewrite。"""
    from contracts.rules import REGISTRY
    from novel.stages.stage3 import style

    # 先把盘上的 measurable 改坏一格——apply-edit 必须把它按 stage1 重算回来，
    # 否则人工路会带着一份伪造的实测继续往下走。
    data = box3.read('stage3/style_grounding.json')
    tampered = data['measurable']['body']['chars']
    data['measurable']['body']['chars'] = tampered + 999
    box3.write('stage3/style_grounding.json', data)

    habits = list(data['surface_habits'])
    box3.write('stage3/style_edit.json', {'surface_habits': list(reversed(habits))})

    ctx = RunContext.open(str(box3.root), box3.sid, 'stage3.style', paid=False)
    art, report = style.run_apply_edit(ctx)

    assert list(art.surface_habits) == list(reversed(habits))
    assert art.measurable.body.chars == tampered, 'measurable 没有被照 stage1 重算'
    after = box3.read('stage3/style_grounding.json')
    assert after['_provenance'].get('partial_rewrite') == '--apply-edit'
    before_prov = data['_provenance']
    assert after['_provenance']['prompts'] == before_prov['prompts'], '继承的 prompts 被重盖'
    assert after['_provenance']['model'] == before_prov['model']
    assert list((box3.dir / 'stage3' / '_rounds').glob('style_grounding.r*.json')), \
        '旧轮没有归档'
    assert {r.rule for r in report.rules} == {r.rule for r in REGISTRY.of_stage('stage3')}


def test_stage3_apply_edit_with_hard_goes_to_invalid_not_formal(box3):
    """人工补丁同样过闸：引号字面在语料里找不到 ⇒ `habit_quote` HARD ⇒ `.INVALID`。

    ⚠ 与付费首轮不同，这里**不许自动触发付费自救**——0 成本编辑不代扣钱。
    """
    from novel.stages.stage3 import style

    before = (box3.dir / 'stage3' / 'style_grounding.json').read_bytes()
    box3.write('stage3/style_edit.json',
               {'surface_habits': ['爱用「这句原话绝无可能出现在任何语料正文里」收尾']})
    ctx = RunContext.open(str(box3.root), box3.sid, 'stage3.style', paid=False)
    with pytest.raises(GateBlocked, match='人工画像补丁'):
        style.run_apply_edit(ctx)
    assert (box3.dir / 'stage3' / 'style_grounding.json').read_bytes() == before, \
        '正式产物被一份带 HARD 的人工稿覆盖了'
    assert (box3.dir / 'stage3' / 'style_grounding.INVALID.json').exists()


def test_stage3_apply_edit_requires_a_previous_artifact(box3):
    from novel.stages.stage3 import style

    box3.write('stage3/style_edit.json', {'surface_habits': ['短句收尾。']})
    (box3.dir / 'stage3' / 'style_grounding.json').unlink()
    ctx = RunContext.open(str(box3.root), box3.sid, 'stage3.style', paid=False)
    with pytest.raises(TerminalState, match='修订路'):
        style.run_apply_edit(ctx)


def test_stage3_apply_edit_refuses_a_missing_edit_file(box3):
    from novel.stages.stage3 import style

    ctx = RunContext.open(str(box3.root), box3.sid, 'stage3.style', paid=False)
    with pytest.raises(TerminalState, match='决策集'):
        style.run_apply_edit(ctx)


# ── stage4 ──────────────────────────────────────────────────────────────────

def _plan_edit_of(box4) -> dict:
    raw = box4.read('stage4/plan_chapters.json')
    return {'reconstruction_strategy': raw['reconstruction_strategy'],
            'output_chapters': raw['output_chapters']}


def test_stage4_apply_edit_lands_a_round_and_rebuilds_context(box4):
    from novel.stages.stage4 import plan

    edit = _plan_edit_of(box4)
    edit['output_chapters'][0]['chapter_goal']['rationale'] += '（人工微调）'
    box4.write('stage4/plan_edit.json', edit)

    ctx = RunContext.open(str(box4.root), box4.sid, 'stage4.plan', paid=False)
    report = plan.run_apply_edit(ctx)

    after = box4.read('stage4/plan_chapters.json')
    assert after['output_chapters'][0]['chapter_goal']['rationale'].endswith('（人工微调）')
    assert after['_provenance'].get('partial_rewrite') == '--apply-edit'
    assert (box4.dir / 'stage4' / 'writer_context.json').exists(), '写作上下文没有同批重建'
    assert list((box4.dir / 'stage4' / '_rounds').glob('plan_chapters.r*.json'))
    link = next(r for r in report.rules if r.rule == 'context_link')
    assert link.status != 'hit', '同批重建之后 context_link 不该红'


def test_stage4_apply_edit_with_hard_goes_to_invalid_not_formal(box4):
    """goal 里混进写法词（STYLE_WORDS）是本层 HARD——人工稿同样被拦。"""
    from novel.stages.stage4 import plan

    before = (box4.dir / 'stage4' / 'plan_chapters.json').read_bytes()
    edit = _plan_edit_of(box4)
    edit['output_chapters'][0]['chapter_goal']['rationale'] = '把本章的张力拉到最满'
    box4.write('stage4/plan_edit.json', edit)

    ctx = RunContext.open(str(box4.root), box4.sid, 'stage4.plan', paid=False)
    with pytest.raises(GateBlocked, match='阻断级'):
        plan.run_apply_edit(ctx)
    assert (box4.dir / 'stage4' / 'plan_chapters.json').read_bytes() == before
    assert (box4.dir / 'stage4' / 'plan_chapters.INVALID.json').exists()


def test_stage4_apply_edit_requires_a_previous_artifact(box4):
    from novel.stages.stage4 import plan

    box4.write('stage4/plan_edit.json', _plan_edit_of(box4))
    (box4.dir / 'stage4' / 'plan_chapters.json').unlink()
    ctx = RunContext.open(str(box4.root), box4.sid, 'stage4.plan', paid=False)
    with pytest.raises(TerminalState, match='修订路'):
        plan.run_apply_edit(ctx)


# ── stage6 ──────────────────────────────────────────────────────────────────

def _hook_text(box6, *, truncated: bool = False) -> str:
    """合本书段间分隔档位的一条文案（照 `test_stage6._sample` 的手法）。"""
    from novel.core.bands import sep_band_of_text
    src = (sandbox.cut(box6.book, 'stage1/hook_copy.json') or {}).get('text', '').strip()
    sep = '\n\n' if sep_band_of_text(src) == '空行分段' else '\n'
    body = sep.join(['她站在门口，手里攥着那张没署名的纸',
                     '不知道该不该推门进去，风把檐下的灯吹得直晃'])
    return body if truncated else body + '。'


def _formal_hook_edit(text: str) -> dict:
    return {
        'text': text,
        'actor': 'test-editor',
        'reason': '已人工核对全书事实',
        'subject_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
    }


def test_stage6_apply_edit_normalizes_and_rejudges_the_candidate(box6):
    from novel.stages.stage6 import hook

    art_before = box6.read('stage6/hook.json')
    target = art_before['candidates'][0]['approach']
    box6.write('stage6/hook_edit.json', {'candidate': target, 'text': _hook_text(box6)})

    ctx = RunContext.open(str(box6.root), box6.sid, 'stage6.hook', paid=False)
    new, report = hook.run_apply_edit(ctx)

    edited = next(c for c in new.candidates if c.approach == target)
    assert '她站在门口' in edited.text
    assert all(f.detail.startswith(f'[{target}]') for f in edited.findings), \
        '人工稿的 findings 没有走同一条判定（缺标签前缀）'
    after = box6.read('stage6/hook.json')
    assert after['_provenance'].get('partial_rewrite') == '--apply-edit'
    assert after['_provenance']['prompts'] == art_before['_provenance']['prompts']
    assert list((box6.dir / 'stage6' / '_rounds').glob('hook.r*.json'))
    # 语义结论按文本 sha1 绑定：改字之后（且盘上无语义产物）两条语义判据只能是 unver。
    semantic_rows = [r for r in report.rules if r.check_kind == 'semantic']
    assert semantic_rows and all(r.status == 'unver' for r in semantic_rows)


def test_stage6_apply_edit_replaces_the_approved_text_and_keeps_its_approach(box6):
    from novel.stages.stage6 import hook

    art_before = box6.read('stage6/hook.json')
    assert art_before.get('approved'), '本条测试要一本已定稿的书'
    box6.write('stage6/hook_edit.json', _formal_hook_edit(_hook_text(box6)))

    ctx = RunContext.open(str(box6.root), box6.sid, 'stage6.hook', paid=False)
    new, _report = hook.run_apply_edit(ctx)

    assert new.approved is not None
    assert new.approved.origin == 'human'
    assert new.approved.approach == art_before['approved']['approach'], \
        '「这份人工稿是从哪一条改出来的」被抹掉了'
    assert '她站在门口' in new.approved.text


def test_stage6_formal_edit_hash_binds_normalized_text_and_checks_semantic_guard(box6, monkeypatch):
    from novel.stages.stage6 import hook, semantic
    from novel.stages.stage6.measure import normalize_hook

    raw = _hook_text(box6).replace('。', '.')
    src = (sandbox.cut(box6.book, 'stage1/hook_copy.json') or {})['text'].strip()
    normalized, _mech = normalize_hook(raw, src_hook=src)
    expected = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    seen = {}

    def require(_store, _art, text, label, **kwargs):
        seen.update(text=text, label=label, **kwargs)
        return SimpleNamespace(semantic_review_seq=None), 'human_verification'

    monkeypatch.setattr(semantic, 'require_human_resolution', require)
    box6.write('stage6/hook_edit.json', _formal_hook_edit(normalized) | {'text': raw})
    ctx = RunContext.open(str(box6.root), box6.sid, 'stage6.hook', paid=False)

    new, _report = hook.run_apply_edit(ctx)

    assert new.approved is not None and new.approved.text == normalized
    assert new.approved.resolution.subject_sha256 == expected
    assert seen['text'] == normalized
    assert seen['expected_subject_sha256'] == expected


def test_stage6_formal_edit_refuses_a_hash_of_raw_pre_normalized_bytes(box6):
    from novel.stages.stage6 import hook

    raw = _hook_text(box6).replace('。', '.')
    box6.write('stage6/hook_edit.json', _formal_hook_edit(raw))
    before = box6.read('stage6/hook.json')
    ctx = RunContext.open(str(box6.root), box6.sid, 'stage6.hook', paid=False)

    with pytest.raises(TerminalState, match='已变化'):
        hook.run_apply_edit(ctx)

    assert box6.read('stage6/hook.json') == before


def test_stage6_apply_edit_does_not_gate_but_reports_red(box6):
    """本层闸门是「无一干净候选才拦」——编辑单条文本不整拦，红行照报。"""
    from novel.stages.stage6 import hook

    target = box6.read('stage6/hook.json')['candidates'][0]['approach']
    box6.write('stage6/hook_edit.json',
               {'candidate': target, 'text': _hook_text(box6, truncated=True)})
    ctx = RunContext.open(str(box6.root), box6.sid, 'stage6.hook', paid=False)
    new, report = hook.run_apply_edit(ctx)

    edited = next(c for c in new.candidates if c.approach == target)
    assert any(f.rule == 'hook_truncated' for f in edited.findings), '截断该被判出来'
    assert report.badge == 'fail' and report.passed is False


def test_stage6_apply_edit_requires_the_formal_artifact(box6):
    from novel.stages.stage6 import hook

    box6.write('stage6/hook_edit.json', _formal_hook_edit(_hook_text(box6)))
    (box6.dir / 'stage6' / 'hook.json').unlink()
    ctx = RunContext.open(str(box6.root), box6.sid, 'stage6.hook', paid=False)
    with pytest.raises(TerminalState, match='stage6.hook'):
        hook.run_apply_edit(ctx)
