#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage3_free_paths.py —— stage3 的四条 0 成本路，**第一次被真的执行一遍**。

【它与 `test_stage3.py` 的分界线】那一份对的是**判据的账**（结论集与原型逐条一致），
它绕开 `style.py` 直接调 `checks.check`。本份对的是**节点函数的接线**：
`RunContext` → `ArtifactStore` → 判据 → 报告 → 落盘，整条走一遍。
在 D 期之前那四个函数**零调用点、零覆盖**——写好了，但从没有人跑过。

【沙箱怎么来】拿显式配置的当前产物按 `corpus.load_current` 严格验证，
写进 `Sandbox` 管理的一份临时拷贝里。
⚠ **原语料一个字节都不许动**；所有写盘动作只在临时目录执行。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.schema import encode_artifact
from contracts.schema.stage1 import HookCopy, MainText
from contracts.schema.stage2 import GlobalGrounding
from contracts.schema.stage2 import GlobalProvenance
from contracts.schema.stage3 import StyleGrounding, StyleProvenance
from novel import prov
from novel.core.naming import names_of
from novel.errors import GateBlocked, UpstreamMissing
from novel.run import RunContext
from novel.stages.stage3 import style
from novel.store import ArtifactStore
from tests import corpus, factories, sandbox

class Box(sandbox.Sandbox):
    def ctx(self, node: str = 'stage3.style') -> RunContext:
        # `write_ledger=False`：账本只在内存里攒，不往沙箱里追加行。
        return RunContext.open(self.root, self.sid, node, paid=False, write_ledger=False)


def _corpus_params():
    books = sandbox.books_with_stage3()
    if books:
        return [pytest.param(book, marks=pytest.mark.corpus) for book in books]
    return [pytest.param(
        None,
        marks=(pytest.mark.corpus, pytest.mark.skip(
            reason='盘上没有 stage3 语料——这些集成用例没在查')),
        id='no-corpus',
    )]


@pytest.fixture(params=_corpus_params(),
                ids=lambda b: b.rsplit('_', 1)[0] if b else 'no-corpus')
def box(request):
    s = Box(request.param)
    yield s
    s.close()


def _write_fixture(book: Path, rel: str, payload: object) -> None:
    path = book / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(encode_artifact(rel, payload), ensure_ascii=False),
        encoding='utf-8', newline='\n')


def test_synthetic_check_only_runs_in_the_default_suite(tmp_path):
    """任意当前 schema 书都必须成立的节点接线，不得依赖现役 ``NOVEL_OUTPUT``。"""
    from contracts.rules import REGISTRY
    from tests.test_stage2 import global_grounding

    sid = '303'
    book = tmp_path / f'合成文风_{sid}'
    stage5_book = factories.stage5_book()
    main = MainText.model_validate([
        {
            'section_id': f'sec_{ordinal:04d}',
            'chapter_ordinal': ordinal,
            'source_chapter_label': str(ordinal),
            'text': text,
        }
        for ordinal, text in sorted(stage5_book.source_text.items())
    ])
    hook = HookCopy(
        section_id='sec_0001',
        text='一把银匙，一本被调换的账册，足以让旧日秘密重新开口。')
    clean = ('\n'.join(stage5_book.source_text.values()) + '\n').encode('utf-8')
    (book / 'stage0').mkdir(parents=True)
    (book / 'stage0' / 'clean_text.txt').write_bytes(clean)
    root_sha1 = prov.content_sha1(clean)

    grounding_payload = global_grounding()
    grounding_payload['_provenance'] = GlobalProvenance(
        code='fixture', root_clean_sha1=root_sha1, inputs={},
        prompts={'GLOBAL_SYS': '0' * 10}, model='fixture-model',
        address_by_fallback=0,
        address_surface_rewrites=[], address_from_chapter_raised=[],
        canonical_distinguishers_stripped=[], setup_chapters_pruned=[],
    ).model_dump(mode='json')
    grounding = GlobalGrounding.model_validate(grounding_payload)

    style_payload = factories.stage3_artifact()
    style_payload['_provenance'] = StyleProvenance(
        code='fixture', root_clean_sha1=root_sha1, inputs={},
        prompts={'STYLE_SYS': '1' * 10}, model='fixture-model',
        attempts=[{
            'kind': 'chapter_aggregate', 'user_prompt': '2' * 10,
            'output': '3' * 10, 'cache_policy': 'default',
        }],
    ).model_dump(mode='json')
    artifact = StyleGrounding.model_validate(style_payload)
    _write_fixture(book, style.MAIN_TEXT, main.model_dump(mode='json'))
    _write_fixture(book, style.HOOK_COPY, hook.model_dump(mode='json'))
    _write_fixture(book, style.GLOBAL_GROUNDING, grounding.model_dump(
        by_alias=True, mode='json'))
    _write_fixture(book, style.REL, artifact.model_dump(by_alias=True, mode='json'))

    report = style.run_check_only(RunContext.open(
        tmp_path, sid, 'stage3.style', paid=False, write_ledger=False))
    assert {row.rule for row in report.rules} == {
        row.rule for row in REGISTRY.of_stage('stage3')}
    assert not [row for row in report.rules if row.status == 'skipped']


# ───────────────── `--check-only`：节点函数第一次真的跑 ─────────────────

def test_check_only_runs_end_to_end_and_reports_every_registered_rule(box):
    """⭐ 这是 `run_check_only` 的**第一个调用点**（C 期它零覆盖）。

    报告按**判据注册表**编排（B3），所以九条注册判据一条都不能少——
    「实现忘了调」不该让一条判据从报告里消失。
    """
    from contracts.rules import REGISTRY
    report = style.run_check_only(box.ctx())
    assert {r.rule for r in report.rules} == {r.rule for r in REGISTRY.of_stage('stage3')}
    assert not [r for r in report.rules if r.sev == 'hard' and r.status == 'hit'], \
        '盘上三本原型对账是 PASS/PASS/PASS——出现 hard 命中就是骨架抄错了'


def test_check_only_refuses_when_there_is_nothing_to_check(box):
    """产物不在盘上 ⇒ **显式终态**，不是「查过没事」。"""
    (box.dir / 'stage3' / 'style_grounding.json').unlink()
    with pytest.raises(UpstreamMissing, match='先跑'):
        style.run_check_only(box.ctx())


def test_check_only_reads_the_blocked_draft_when_no_formal_artifact_exists(box):
    """首次付费即被闸门拦下时，`.INVALID` 是唯一可审阅稿，复检必须能读它。"""
    src = box.dir / 'stage3' / 'style_grounding.json'
    bad = box.dir / 'stage3' / 'style_grounding.INVALID.json'
    bad.write_bytes(src.read_bytes())
    src.unlink()

    report = style.run_check_only(box.ctx())
    assert report.provenance.invalid is True
    assert report.provenance.freshness in ('fresh', 'unknown', 'stale')
    assert {r.rule for r in report.rules}


# ───────────────── `--measure-only`：只看不动 ─────────────────

def test_measure_only_writes_nothing_and_matches_what_is_on_disk(box):
    """`--measure-only` 与 `--remeasure` **是两件事**（`FREE_ENTRIES` 自己写着）。

    这一条同时是那笔对账：盘上那份 `measurable` **23 项（body 14 + hook 9）逐值相同**。
    ⚠ 不一致就是确定性核心抄错了，不是数据变了。
    """
    before = {p: p.read_bytes() for p in box.dir.rglob('*.json')}
    meas = style.run_measure_only(box.ctx())
    assert {p: p.read_bytes() for p in box.dir.rglob('*.json')} == before, \
        '`--measure-only` 不许写盘'

    stored = StyleGrounding.model_validate(box.read('stage3/style_grounding.json'))
    from novel.core.measure import drift
    diff = [d for scope in ('body', 'hook')
            for d in drift(getattr(stored.measurable, scope), getattr(meas, scope))]
    assert diff == [], f'measurable 与盘上不一致：{diff}'


# ───────────────── `--remeasure`：免费**但会写盘** ─────────────────

def test_remeasure_writes_back_and_marks_the_artifact_as_mixed_version(box):
    """⭐ **来源账怎么记，是这条路最容易做错的地方**（`_common.md` §5.1b）。

    继承 `prompts` / `model`（那次付费产出仍然是它们记的）· 换掉 `inputs`
    （本次真读到的那几份）· 标 `partial_rewrite`（让混版这件事看得见）。
    """
    before = box.read('stage3/style_grounding.json')
    written, diff, report = style.run_remeasure(box.ctx())

    assert diff == [], f'语料没动过，重算不该有差异：{diff}'
    after = box.read('stage3/style_grounding.json')
    assert after != before, '`--remeasure` 是**会写盘**的 0 成本路'

    old, new = before['_provenance'], after['_provenance']
    assert new['partial_rewrite'] == '--remeasure'
    assert new['prompts'] == old['prompts'], 'prompts 是当初那次付费产出记的，不许改'
    assert new['model'] == old['model'], 'model 同上——改了就是伪造'
    assert new['inputs'], '本次真读了三份上游，inputs 必须换成这一次的'

    # ⚠⚠ **这一趟的徽章就该是 `fail`，别顺手兜掉**（C 期已裁决，README「别顺手兜掉」）：
    # `measurable` 刚被本次覆盖，`measurable_drift` 在这一趟里恒等成立 ⇒ 按设计**不排**它
    # （一条永远不会红的检查不该打 `[OK]`，判据 31）⇒ 报告里它是 `skipped`，
    # 而 **HARD 的 `skipped` 进 `fail`**——你确实没跑那道闸，就不该拿到一个绿灯。
    # 那道闸由 `build_steps` 自动追跑的那条 `--check-only` 去跑。
    drift = next(r for r in report.rules if r.rule == 'measurable_drift')
    assert (drift.status, report.badge) == ('skipped', 'fail'), (drift.status, report.badge)
    # ⚠ 只禁 hard：三岁那本按契约就该有 2 处 `micro_quote`（INFO），
    # 把它一起禁掉就是拿一条会红的真实基线换一句好看的断言。
    assert not [r for r in report.rules if r.status == 'hit' and r.sev == 'hard']


def test_remeasure_does_not_touch_anything_else_in_the_artifact(box):
    """「其余字段不动」是本条的字面承诺，逐键对一遍。"""
    before = box.read('stage3/style_grounding.json')
    style.run_remeasure(box.ctx())
    after = box.read('stage3/style_grounding.json')
    for k in set(before) | set(after):
        if k in ('measurable', '_provenance'):
            continue
        assert before.get(k) == after.get(k), f'`--remeasure` 动了 {k}'


def test_remeasure_refuses_when_there_is_no_artifact(box):
    (box.dir / 'stage3' / 'style_grounding.json').unlink()
    with pytest.raises(UpstreamMissing):
        style.run_remeasure(box.ctx())


# ───────────────── `--revalidate`：0 成本提升路 ─────────────────

def _leak_a_real_name(box: Box) -> str:
    """往 `voice` 里塞一个**真的人物专名** —— `name_leak` 是 HARD，这会让它红。

    ⚠ 用真名而不是随手编一个：判据查的是 stage2 名表里的专名，
    编一个名字命不中，那条测试就会变成「构造不出反例」的假绿。
    """
    gg = GlobalGrounding.model_validate(box.read('stage2/global_grounding.json'))
    name = sorted(names_of(gg))[0]
    data = box.read('stage3/style_grounding.json')
    data['voice']['register_tone'] = f'{data["voice"]["register_tone"]}（例如{name}那几段）'
    ArtifactStore(box.ctx()).write_invalid(
        style.REL, StyleGrounding.model_validate(data), gates_skipped=[])
    return name


def _revalidate(box: Box):
    return style.run_revalidate(
        box.ctx(), actor='reviewer', reason='review the blocked style draft')


def test_revalidate_refuses_when_there_is_no_blocked_draft(box):
    """没有被拦下的稿就没什么可提升的。**这与「复检正式产物」是两件事。**"""
    with pytest.raises(UpstreamMissing, match='没有被闸门拦下的稿'):
        _revalidate(box)


def test_revalidate_promotes_a_draft_that_now_passes(box):
    """⭐ 提升路第一次通了。**钱一分没花。**

    ⚠ 提升走 `store.promote_invalid`——它的签名里没有 `prompts`/`model`/`inputs`，
    **没有参数就没有可以传错的东西**（全链四处实测伪造/丢失，见 `_common.md` §5.2）。
    """
    src = box.dir / 'stage3' / 'style_grounding.json'
    bad = box.dir / 'stage3' / 'style_grounding.INVALID.json'
    ArtifactStore(box.ctx()).write_invalid(
        style.REL, StyleGrounding.model_validate(box.read(style.REL)),
        gates_skipped=[])
    src.unlink()                                   # 正式稿还没有，只有被拦下的那一份

    promoted, report = _revalidate(box)
    assert promoted is True, [r.rule for r in report.rules if r.status == 'hit']
    assert src.is_file(), '合格就该成为正式产物'
    assert not bad.exists(), '`.INVALID` 该被挪走'
    prov = box.read('stage3/style_grounding.json')['_provenance']
    assert prov['revalidated'] is True
    audit = prov['revalidation_audit']
    assert audit['actor'] == 'reviewer'
    assert audit['reason'] == 'review the blocked style draft'
    assert audit['content_changed'] is False
    assert audit['edited_by_human'] is False


def test_revalidate_leaves_a_still_failing_draft_where_it_is(box):
    """仍有阻断错误 ⇒ **不提升、不覆盖正式产物**，问题稿留在原地。

    ⚠ 判准是「一条 hard 都没有」，**不是**「没有新引入的」——
    带着一条旧的阻断错误被提升上去，等于把闸门本身作废掉。
    """
    name = _leak_a_real_name(box)
    before = (box.dir / 'stage3' / 'style_grounding.json').read_bytes()

    promoted, report = _revalidate(box)
    assert promoted is False, f'塞了专名 {name} 还能提升，说明 name_leak 没跑'
    assert (box.dir / 'stage3' / 'style_grounding.INVALID.json').is_file()
    assert (box.dir / 'stage3' / 'style_grounding.json').read_bytes() == before, \
        '不合格不许覆盖现役产物（`Q-5`：先校验后落盘）'
    hit = [r.rule for r in report.rules if r.status == 'hit' and r.sev == 'hard']
    assert 'name_leak' in hit, hit


def test_the_gate_and_the_promotion_path_share_one_judge(box):
    """落盘闸门与 `--revalidate` 走**同一段判据**——两个入口一段代码。

    第二段实现意味着「拦下来的理由」与「提升时查的东西」会慢慢分家，
    而分家之后一份稿可能**既过不了闸门、又被提升上去**。
    """
    _leak_a_real_name(box)
    _promoted, report = _revalidate(box)
    bad = {r.rule for r in report.rules if r.status == 'hit' and r.sev == 'hard'}

    data = box.read('stage3/style_grounding.INVALID.json')
    store = ArtifactStore(box.ctx())
    gate = style._judge(store, data, measured_now=None)
    assert {o.rule for o in gate
            if any(f.sev == 'hard' for f in (o.findings or []))} == bad


def test_remeasure_blocks_only_newly_introduced_errors(box):
    """⚠ **旧违约照报但不挡路**，否则一条不相干的旧问题会把重算这条路**永久堵死**。

    这里把一条 HARD 违规写进**正式产物**：重算前它就在，所以 `--remeasure` 照跑不误。
    """
    gg = GlobalGrounding.model_validate(box.read('stage2/global_grounding.json'))
    data = box.read('stage3/style_grounding.json')
    data['voice']['register_tone'] += f'（例如{sorted(names_of(gg))[0]}那几段）'
    box.write('stage3/style_grounding.json', data)

    _written, _diff, report = style.run_remeasure(box.ctx())
    assert 'name_leak' in {r.rule for r in report.rules
                           if r.status == 'hit' and r.sev == 'hard'}, '旧违约要照报'
    assert box.read('stage3/style_grounding.json')['_provenance']['partial_rewrite'] == '--remeasure', \
        '旧违约不该挡住重算这条路'


def test_a_newly_introduced_error_does_block_the_write(box):
    """反方向：**本次新引入**的阻断错误必须拦住落盘。

    ⚠ 本实现比原型严一格：原型是**先写回、再重判**，
    于是新引入错误的那一趟已经把产物盖掉了。这里判在写之前（`Q-5`）。
    """
    box_ctx = box.ctx()
    real = style.remeasured

    def poisoned(ctx):
        art, diff = real(ctx)
        gg = GlobalGrounding.model_validate(box.read('stage2/global_grounding.json'))
        v = art.voice.model_copy(
            update={'register_tone': f'{art.voice.register_tone}（{sorted(names_of(gg))[0]}）'})
        return art.model_copy(update={'voice': v}), diff

    before = (box.dir / 'stage3' / 'style_grounding.json').read_bytes()
    style.remeasured = poisoned
    try:
        with pytest.raises(GateBlocked, match='本次新引入'):
            style.run_remeasure(box_ctx)
    finally:
        style.remeasured = real
    assert (box.dir / 'stage3' / 'style_grounding.json').read_bytes() == before, \
        '被拦下就不许覆盖已有产物'
