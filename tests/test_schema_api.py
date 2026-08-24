#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_schema_api.py —— HTTP 响应模型的载体层守卫（`docs/schema/_api.md` ↔ `contracts/schema/api.py`）。

【本文件盯三件事】
  ① `StyleView` 那五条后置条件，**每条配一个会红的反例**——
     一条只有正例的后置条件，与一条被注释掉的后置条件在测试结果上长得一样。
  ② **契约层不许手抄取值面**（`Literal['a','b']`）：取值面的唯一落点是 `invariants`。
  ③ **契约层不许背上 web 栈**：离线产 TS 类型的脚本一行 HTTP 都不发。

⚠ 本文件**不需要 fastapi**。
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.invariants import HOOK_KEYS, VOICE_KEYS
from contracts.schema.api import (
    DraftAlignView, DraftView, DriftItem, ErrorBody, GlobalView, HookCopyView, JobList,
    LogChunk, LogStreamEvent, NovelList,
    PreviewRequest, ReadingView, ReviewView,
    ReviewChapterView,
    Stage0EditRequest, Stage0Segment, Stage0Segments,
    Stage1ChapterRow, Stage1Chapters, Stage1Segment, Stage1Segments,
    Stage4View, Stage5View, Stage6View, Stage7View, StyleView, UploadResult,
)
from contracts.schema.api import ReadingBeatLine
from contracts.schema.stage5 import ChapterSegment
from contracts.schema.stage3 import (
    ChapterCharStats, Measurable, MeasurableBlock, MeasurableBody, PerChapterSentence,
    StyleProvenance,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PY = os.path.join(ROOT, 'contracts', 'schema', 'api.py')


def test_active_global_fixture_is_a_v3_view_and_every_located_span_matches_chapter_text():
    fixtures = Path(ROOT) / 'web' / 'src' / 'fixtures' / 'backend_v1'
    raw = json.loads((fixtures / 'global.json').read_text(encoding='utf-8'))
    view = GlobalView.model_validate(raw)
    assert {r.visibility.kind for r in view.relations} == {'public', 'via_secret'}
    assert {e.subject_kind for s in view.secrets for e in s.knowledge_events} == {
        'character', 'group'}

    reading = json.loads((fixtures / 'reading_ch3.json').read_text(encoding='utf-8'))
    chapter = ''.join(segment['text'] for segment in reading['segments'])
    pending: list[object] = [raw]
    located = 0
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            source = item.get('source')
            if isinstance(source, dict):
                assert chapter[source['start']:source['end']] == source['text']
                located += 1
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    assert located >= 7, '七类 evidence 没有都在 active fixture 里留下可回取正例'


def test_preview_sid_is_nullable_but_never_empty():
    assert PreviewRequest(sid=None, node='stage0.remove').sid is None
    with pytest.raises(ValidationError):
        PreviewRequest(sid='', node='stage0.remove')


def test_stage0_continuous_view_rejects_a_gap_and_duplicate_ids():
    first = Stage0Segment(
        id='same', kind='keep', start=0, end=2, chars=2, flagged=False, text='甲乙')
    gap = Stage0Segment(
        id='same', kind='keep', start=3, end=4, chars=1, flagged=False, text='丙')
    with pytest.raises(ValidationError, match='连续|重复'):
        Stage0Segments(
            normalized_source_sha1='0' * 40,
            available=True,
            reason=None,
            segments=[first, gap],
        )
    with pytest.raises(ValidationError):
        Stage0Segments(
            normalized_source_sha1='0' * 40,
            available=False,
            reason='',
            segments=[],
        )


def test_stage1_continuous_view_and_chapter_index_reject_gaps():
    first = Stage1Segment(
        id='hook', kind='hook', start=0, end=1, chars=1, flagged=False, text='甲')
    gap = Stage1Segment(
        id='ch2', kind='chapter', start=2, end=3, chars=1, flagged=False, text='乙',
        chapter_ordinal=2, source_chapter_no=2,
    )
    with pytest.raises(ValidationError, match='连续'):
        Stage1Segments(input_sha1='0' * 40, segments=[first, gap])
    with pytest.raises(ValidationError, match='连续有序'):
        Stage1Chapters(chapters=[
            Stage1ChapterRow(
                chapter_ordinal=2,
                source_chapter_no=2,
                chars=1,
                preview='乙',
                label_delta=1,
            ),
        ])


def test_reading_present_is_always_an_array_not_nullable():
    base = dict(
        beat_id='b1', located=False, start=None, end=None,
        time_layer='present', place=None, event='空院里没有别人。', hinges=[],
    )
    assert ReadingBeatLine(**base, present=[]).present == []
    with pytest.raises(ValidationError):
        ReadingBeatLine(**base, present=None)


def test_anchor_coverage_warning_is_a_server_decision():
    low = DraftAlignView(
        beats=5, anchors=2, rows=2, inversions=0, coverage=0.4,
        coverage_threshold=0.6, coverage_state='low', note='对应关系，不是判定')
    assert low.coverage_state == 'low'
    with pytest.raises(ValidationError, match='coverage_state'):
        DraftAlignView(
            beats=5, anchors=2, rows=2, inversions=0, coverage=0.4,
            coverage_threshold=0.6, coverage_state='adequate', note='错误地说安全')


def test_stage5_views_only_expose_text_as_the_segment_projection():
    segments = [
        ChapterSegment(slot='s01', text='前段。'),
        ChapterSegment(slot='s02', text='后段。'),
    ]
    provenance = {
        'code': 'test', 'root_clean_sha1': '0' * 40, 'inputs': {},
        'origin': 'human', 'round': 1, 'revision_of': None,
        'review': None, 'fixes': None,
    }
    draft = dict(
        mode='deep', relpath='stage5/deep/ch01.json', invalid=False,
        round=1, origin='human', chars=8, budget_chars=8, source_chars=8,
        overlap_excl_verbatim=0.0, segments=segments, text='前段。\n\n后段。',
        align=None, align_rows=[], freshness='fresh', reason='match',
        _provenance=provenance,
    )
    assert DraftView.model_validate(draft).text == '前段。\n\n后段。'
    with pytest.raises(ValidationError, match='确定性投影'):
        DraftView.model_validate({**draft, 'text': '另一份整章正文。'})

    review = dict(
        output_ordinal=1, findings=[], mode='deep', chars=8, budget_chars=8,
        round=1, origin='human', segments=segments, text='前段。\n\n后段。',
        rounds=[], stale=False,
    )
    assert ReviewChapterView.model_validate(review).segments == segments
    with pytest.raises(ValidationError, match='必须为 null'):
        ReviewChapterView.model_validate({**review, 'segments': None})


# ───────────────────────── 造两份最小的合法视图 ─────────────────────────
#
# ⚠ **合成，不读盘。** 本文件测的是**载体层**的后置条件，而载体层的正确性
#   不该取决于「这台机器上有没有那三本语料」——那样它会在没有语料时静静地 skip，
#   而 skip 掉的东西看起来像绿的。盘上语料的体检在 `tests/test_views_stage3.py`。

_BLOCK = dict(chars=139, paragraphs=8, sentences=9, avg_paragraph_chars=17.4,
              paragraph_sep_chars=1.75, avg_sentence_chars=15.4,
              dialogue_ratio=0.496, dialogue_ratio_applicable=True, quote_styles=['“”'])


def _measurable() -> Measurable:
    return Measurable(
        body=MeasurableBody(**{**_BLOCK, 'chars': 9516,
                               'chapter_chars': ChapterCharStats(min=1053, max=1391, avg=1189.5),
                               'per_chapter_avg_sentence': PerChapterSentence(min=16.6, max=18.4)}),
        hook=MeasurableBlock(**_BLOCK))


def _empty_view(**over):
    """产物不在盘上、而左半边也量不出来的那一态。**九个端点里最常见的一态。**"""
    base = dict(
        sid='7615069877019300633', title='某本书',
        exists=False, condition='missing', invalid_exists=False, readable=None,
        measurable_stored=None, bands_stored=None, _provenance=None,
        hook_copy_text=None,
        measured_now=None, bands_now=None,
        measure_error='stage1/main_text.json 不在盘上——先跑 stage1',
        measure_error_kind='UpstreamMissing',
        drift=None,
        freshness='unknown', reason='no_provenance',
    )
    base.update(over)
    return StyleView(**base)


def _loaded_view(**over):
    """产物读回来了、左半边也量到了的那一态（**全部字段都在**）。"""
    meas = _measurable()
    base = dict(
        sid='7615069877019300633', title='某本书',
        exists=True, condition='ready', invalid_exists=False, readable=True,
        voice=[{'key': k, 'text': f'{k} 的描述', 'chars': 12} for k in VOICE_KEYS],
        surface_habits=['一条写作小动作'],
        hook_style=[{'key': k, 'text': f'{k} 的描述'} for k in HOOK_KEYS],
        measurable_stored=meas, bands_stored={'句子形态': '短句'},
        _provenance=StyleProvenance(
            code='b371244', model='deepseek-v4-pro',
            root_clean_sha1='0' * 40,
            prompts={'STYLE_SYS': '0ca5dc44e0'},
            inputs={'stage1/main_text.json': {'file': 'b8eb890096', 'consumed': None}},
            attempts=[{'kind': 'chapter_aggregate', 'user_prompt': '37b4277e08',
                       'output': '345dd96743', 'cache_policy': 'default'}]),
        hook_copy_text='全家被贬为奴籍发配青楼。',
        measured_now=meas, bands_now={'句子形态': '短句'},
        measure_error=None, measure_error_kind=None,
        drift=[],
        freshness='fresh', reason='match',
    )
    base.update(over)
    return StyleView(**base)


def test_both_baseline_views_are_legal():
    """守卫的守卫：两个底座本身要能构造出来。

    否则下面每一条反例都会「因为别的原因」红，而一条**为了错的理由而红**的反例，
    与一条根本没在查的反例，在测试结果上长得一模一样。
    """
    empty, loaded = _empty_view(), _loaded_view()
    assert empty.exists is False and empty.readable is None and empty.drift is None
    assert loaded.exists is True and loaded.drift == [] and loaded.provenance is not None


# ───────────────────────── ① 五条后置条件，各配一个反例 ─────────────────────────

def test_readable_is_none_exactly_when_the_artifact_is_absent():
    """三态：`readable=None` ⟺ 产物不在盘上。

    ⚠ `readable=False` 与 `exists=False` **必须分开**：盘上真有一份读不回来的产物
    （`成年人不做选择` 那本的 `stage4/plan_chapters.json` 根本没有 `_provenance`），
    折成「不存在」会让界面说「还没跑」——**而它跑过，只是那次把来源账丢了**。
    """
    with pytest.raises(ValidationError, match='readable'):
        _empty_view(readable=False)          # 不在盘上却宣称「读不回来」
    with pytest.raises(ValidationError, match='readable'):
        _empty_view(exists=True, readable=None)


def test_the_right_half_is_all_or_nothing():
    """产物没读回来，右半边就不许有内容——**那份内容是从哪儿来的？**"""
    with pytest.raises(ValidationError, match='右半边'):
        _empty_view(surface_habits=['凭空冒出来的一条'], voice=[])
    with pytest.raises(ValidationError, match='右半边'):
        _empty_view(bands_stored={'句子形态': '短句'})


def test_measured_and_measure_error_are_exactly_one():
    """「量到了」与「量不了，原因是 X」**恰好一个**成立。

    同空 = 什么都没说；同非空 = 说了两件互相矛盾的事。
    ⚠ 这一条与 `FRESHNESS` 的 `unknown`、`JOB_STATE` 的 `unknown` 是同一条纪律
    在这一屏上的样子：**「不知道」必须说得出为什么**。
    """
    with pytest.raises(ValidationError, match='恰好一个'):
        _empty_view(measure_error=None, measure_error_kind=None)   # 两边都空
    with pytest.raises(ValidationError, match='同进同出'):
        _empty_view(measure_error='量不了', measure_error_kind=None)


def test_bands_track_the_numbers_they_are_computed_from():
    """档位词是 `bands()` 从实测数字**现算**的纯函数结果 ⇒ 它有没有完全由实测决定。

    ⚠ 抓包里另有一个 `bands_error`，**本模型刻意不要它**：`bands()` 在一个合法的
    `Measurable` 上是全函数，那个格子只可能与 `measure_error` 说同一件事。
    """
    with pytest.raises(ValidationError, match='bands_now'):
        _empty_view(bands_now={'句子形态': '短句'})


def test_drift_null_and_empty_are_two_different_things():
    """⭐ `[]` = 「比过了，没有漂移」；`null` = 「有一边算不出来，**无从比**」。

    压成一个 `[]` 就回到 `Q-2` 那个病——本项目一路在防的那一个。
    """
    with pytest.raises(ValidationError, match='drift'):
        _empty_view(drift=[])                # 两份实测一份都没有，却宣称「比过了」


def test_voice_order_is_not_a_second_landing_point():
    """`voice` / `hook_style` 的顺序恒等于 `VOICE_KEYS` / `HOOK_KEYS`。

    ⚠ 顺序如果落在**字段声明顺序**上，那就是第二个落点——而界面上八维的顺序有意义
    （`narrative_distance` 打头，`mode_variation` 收尾且明显长于其余七维）。
    """
    view = _loaded_view()
    assert [v.key for v in view.voice] == list(VOICE_KEYS)
    assert [h.key for h in view.hook_style] == list(HOOK_KEYS)
    with pytest.raises(ValidationError, match='VOICE_KEYS'):
        _loaded_view(voice=[{'key': k, 'text': f'{k} 的描述', 'chars': 12}
                            for k in reversed(VOICE_KEYS)])
    with pytest.raises(ValidationError, match='HOOK_KEYS'):
        _loaded_view(hook_style=[{'key': k, 'text': f'{k} 的描述'}
                                 for k in reversed(HOOK_KEYS)])


def test_a_loaded_view_cannot_hide_an_empty_right_half():
    """反方向：产物读回来了，右半边就不许有空格子——**那不是一份合法的产物**。"""
    with pytest.raises(ValidationError, match='右半边'):
        _loaded_view(surface_habits=[])
    with pytest.raises(ValidationError, match='右半边'):
        _loaded_view(_provenance=None)


def test_drift_is_required_once_both_sides_are_there():
    """两份实测都在 ⇒ **必须给出比较结果**，`null` 不许再出现。

    ⚠ 这是 `test_drift_null_and_empty_are_two_different_things` 的另一半：
    只查一个方向的话，一个恒为 `null` 的 `drift` 会永远绿——
    而那正是「比过了没漂移」被静默说成「无从比」的形态。
    """
    with pytest.raises(ValidationError, match='drift'):
        _loaded_view(drift=None)


# ───────────────────────── ② 契约层不许手抄取值面 ─────────────────────────

def test_the_api_models_never_spell_out_a_value_domain():
    """⭐ `contracts/schema/api.py` 里不许出现 `Literal['a', 'b']` 这种**字面量**取值面。

    取值面的唯一落点是 `contracts/invariants.py`。一份响应模型抄一份取值面，
    就是那份取值面的第 N 个落点——而本仓每一处 `Literal[XXX]`（大写的名字，不是字符串）
    都是在说同一件事：**取值面从表里来**。
    """
    tree = ast.parse(open(API_PY, encoding='utf-8').read(), filename=API_PY)
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name) and node.value.id == 'Literal'):
            continue
        inner = node.slice
        items = inner.elts if isinstance(inner, ast.Tuple) else [inner]
        if any(isinstance(x, ast.Constant) and isinstance(x.value, str) for x in items):
            bad.append(node.lineno)
    assert not bad, (
        f'`contracts/schema/api.py` 第 {bad} 行把取值面**抄成了字面量**。'
        f'⇒ 写成 `Literal[SOME_TUPLE]`，取值面只许有一处（`contracts/invariants.py`）')
    # 守卫的守卫：一个 `Literal[...]` 都没扫到时，上面那条会静静地绿。
    lits = [n for n in ast.walk(tree) if isinstance(n, ast.Subscript)
            and isinstance(n.value, ast.Name) and n.value.id == 'Literal']
    assert len(lits) >= 2, f'只扫到 {len(lits)} 个 Literal——AST 多半解析错了'


def test_the_api_models_never_carry_a_verdict():
    """⭐ `R-11.4`：视图不带判定。**`badge` / `passed` / `counts` 只许住在 `CheckReport` 里。**

    视图给的是「两份数字并排放着让人自己看」（`R-11` 节首语的原话），
    判定给的是「合不合格」。混在一格里，界面上就分不出
    「这是服务端判的」与「这是这一屏自己凑出来的」。
    """
    verdicts = {'badge', 'passed', 'counts', 'hard_count', 'unver_count'}
    for model in (
        StyleView, NovelList, JobList, LogChunk, LogStreamEvent, PreviewRequest, ErrorBody,
        Stage0Segment, Stage0Segments, Stage1Segment, Stage1Segments,
        Stage1ChapterRow, Stage1Chapters, HookCopyView, UploadResult,
        GlobalView, Stage6View, Stage7View,
        ReadingView, Stage4View, Stage5View, ReviewView,
    ):
        leaked = verdicts & set(model.model_fields)
        assert not leaked, f'{model.__name__} 带着判定字段 {leaked}——那是第二个判官'


# ───────────────────────── ③ 契约层不许背上 web 栈 ─────────────────────────

def test_the_contract_layer_never_imports_the_web_stack():
    """离线产前端 TS 类型的脚本要 import 契约层，而它一行 HTTP 都不发。

    ⚠ 这条与 `tests/test_layering.py` 那份是**一对**：那边扫整个 `contracts/`，
    这边只盯新增的 `api.py`（它是最容易顺手写进 `from fastapi import ...` 的那一个）。
    """
    tree = ast.parse(open(API_PY, encoding='utf-8').read(), filename=API_PY)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split('.')[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split('.')[0])
    assert not (mods & {'fastapi', 'starlette', 'uvicorn', 'novel'}), \
        f'contracts/schema/api.py 长出了不该有的依赖：{sorted(mods)}'
    assert 'pydantic' in mods, 'pydantic 都没 import 到——AST 多半解析错了'


# ───────────────────────── 四、小模型 ─────────────────────────

def test_log_chunk_counts_bytes_not_characters():
    """`next_byte` 是**字节**偏移。

    ⚠ 中文一字跨 3 字节：拿 `len(text)` 去推下一次的 offset，
    偏移会越滑越远，而表现是日志「跳着显示」——没有任何东西会报错。
    """
    chunk = LogChunk(text='中文三字', next_byte=12)
    assert chunk.next_byte != len(chunk.text)
    with pytest.raises(ValidationError):
        LogChunk(text='x', next_byte=-1)


def test_log_stream_event_has_an_explicit_terminal_shape():
    """SSE 数据块与终态不能长得一样，否则前端只能再猜一次连接是否结束。"""
    live = LogStreamEvent(text='中文', next_byte=6, done=False, state=None)
    end = LogStreamEvent(text='', next_byte=6, done=True, state='ok')
    assert not live.done and end.state == 'ok'
    with pytest.raises(ValidationError):
        LogStreamEvent(text='', next_byte=6, done=True, state=None)
    with pytest.raises(ValidationError):
        LogStreamEvent(text='', next_byte=6, done=True, state='running')


def test_drift_item_is_a_named_object_not_a_triple():
    """具名对象，不是位置数组——理由同 `/api/enums` 拒收 `NamedTuple` 的那一条。"""
    d = DriftItem(key='body.chars', stored=9516, now='（缺）')
    assert set(d.model_dump()) == {'key', 'stored', 'now'}


def test_error_body_carries_an_id_not_a_phrase():
    """`kind` 是异常类名。前端的四态靠它分「后端没起来 / 这本书没有 / 这一步还没跑」。"""
    e = ErrorBody(kind='UpstreamMissing', message='这本书不在 Output 下', missing='novel')
    assert e.kind == 'UpstreamMissing' and e.hint is None
    with pytest.raises(ValidationError):
        ErrorBody(kind='', message='空 kind 等于没说')


def test_every_response_model_forbids_extra_fields():
    """`extra='forbid'` 是不可协商的那一条——也适用于响应模型。

    ⚠ 它在这一层守的是**反方向**：产物模型防「上游多写了一个字段被静默丢掉」，
    响应模型防「我们多发了一个字段而契约文档里没有它」——
    前端会开始依赖那个字段，而 `_api.md` 不知道它存在。
    """
    for model in (
        StyleView, NovelList, JobList, LogChunk, LogStreamEvent, PreviewRequest, ErrorBody,
        Stage0Segment, Stage0Segments, Stage0EditRequest,
        Stage1Segment, Stage1Segments, Stage1ChapterRow, Stage1Chapters,
        HookCopyView, UploadResult, GlobalView, Stage6View, Stage7View,
        ReadingView, Stage4View, Stage5View, ReviewView,
    ):
        assert model.model_config['extra'] == 'forbid', model.__name__


# ───────────────────────── 五、三个新读屏模型的后置条件 ─────────────────────────

def test_a_name_ref_that_failed_to_resolve_keeps_the_raw_id():
    """view 不编名字：解析不动时 `display == raw`，且 `resolved=False` 看得见。"""
    from contracts.schema.api import NameRef
    ref = NameRef(raw='char_099', display='char_099', resolved=False)
    assert ref.display == ref.raw
    with pytest.raises(ValidationError):
        NameRef(raw='', display='x', resolved=True)


def test_stage7_chapter_block_is_all_or_nothing():
    """`ordinal` 与 `rows` 同进同出——「问了 :o」与「书级」不许混出一个半吊子。

    ⚠ 用 `model_construct` 骗过必填字段、只让待测的那条后置条件跑——
    这里测的是校验器本身，不是造一份全须全尾的产物。
    """
    base = Stage7View.model_construct()
    ok = dict(base.__dict__, chapters=[1, 2], ordinal=None, rows=None)
    Stage7View._chapter_block_is_all_or_nothing(Stage7View.model_construct(**ok))
    bad = dict(base.__dict__, chapters=[1, 2], ordinal=1, rows=None)
    with pytest.raises(ValueError):
        Stage7View._chapter_block_is_all_or_nothing(Stage7View.model_construct(**bad))
    out_of_range = dict(base.__dict__, chapters=[1, 2], ordinal=9, rows=[])
    with pytest.raises(ValueError):
        Stage7View._chapter_block_is_all_or_nothing(
            Stage7View.model_construct(**out_of_range))
