#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_store.py —— 唯一读写口 + 一次跑的上下文。

每条断言各对应一次**已经发生过**的损失（原型实测，不是设想）：
进程全局来源账把 A 书的边记到 B 书上 · 提升路伪造一份来源账 ·
`prompts` 非空而 `inputs` 是空的却照样落盘 · 归档没编号被覆盖 ·
`.INVALID` 读完就删造出一条幽灵边 · Windows 上 `open(p,'w')` 把 LF 吐成 CRLF。

⚠ 本机 `%TEMP%\\pytest-of-yh255` 的 ACL 坏了，pytest 的 `tmp_path` 一用就炸
——全仓一律 `tempfile.mkdtemp()`。
"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, RootModel

from contracts.schema import InputEdge, StyleGrounding, Usage
from contracts.schema.stage3 import StyleProvenance
from novel.consumption import CONSUMED_PROJECTIONS, producer_node, projection_for
from novel.errors import (
    ArtifactUnreadable, ByteFormRefused, PathRefused, ProvenanceRefused, UpstreamMissing,
)
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, ledger_path
from novel.run import RunContext
from novel.store import ArtifactStore
from tests import corpus

STYLE_REL = 'stage3/style_grounding.json'
UPSTREAM = 'stage1/main_text.json'


class Loose(BaseModel):
    """只用来测记账的最小上游模型——本文件不测 stage1 的契约。"""
    sections: list


class MainTextLoose(RootModel[list[dict]]):
    """只验证注册投影的最小正文载体；业务 schema 另有自己的测试。"""


# ─────────────────────────────────────────────────────────────────────────────
# 夹具：一本书 + 一次跑
# ─────────────────────────────────────────────────────────────────────────────

def _book(sid: str = '123', title: str = '书名', payload: bytes = b'{"sections": []}') -> Path:
    base = Path(tempfile.mkdtemp())
    (base / f'{title}_{sid}' / 'stage1').mkdir(parents=True)
    (base / f'{title}_{sid}' / 'stage1' / 'main_text.json').write_bytes(payload)
    return base


def _ctx(base: Path, sid: str = '123', node: str = 'stage3.style', *, paid: bool = True,
         write_ledger: bool = False) -> RunContext:
    return RunContext.open(base, sid, node, paid=paid, write_ledger=write_ledger)


def _style(ctx: RunContext, *, habit: str = '习惯一') -> StyleGrounding:
    """一份最小的合法 stage3 产物。字段值不重要——本文件测的是读写口，不是文风。"""
    voice = {k: '描述' for k in (
        'narrative_distance', 'register_tone', 'interiority', 'sentence_rhythm',
        'dialogue_presentation', 'description_density', 'chapter_open_close', 'mode_variation')}
    hook = {k: '写法' for k in (
        'person', 'open_mode', 'info_hiding', 'twist', 'end_question', 'tone', 'relation_to_body')}
    blk = dict(chars=1, paragraphs=1, sentences=1, avg_paragraph_chars=1.0,
               paragraph_sep_chars=1.0, avg_sentence_chars=1.0, dialogue_ratio=None,
               dialogue_ratio_applicable=True, quote_styles=['「」'])
    body = dict(blk, chapter_chars=dict(min=1, max=2, avg=1.5),
                per_chapter_avg_sentence=dict(min=1.0, max=2.0))
    prov = ctx.llm_provenance(
        StyleProvenance, prompts={'STYLE_SYS': '插值之后的系统提示词'}, model='deepseek-v4-pro',
        attempts=[dict(kind='initial', user_prompt='0' * 10, output='1' * 10,
                       cache_policy='default')])
    return StyleGrounding(voice=voice, surface_habits=[habit], hook_style=hook,
                          measurable=dict(body=body, hook=blk), _provenance=prov)


def _prov_on_disk(p: Path) -> dict:
    return json.loads(p.read_text(encoding='utf-8'))['_provenance']


# ─────────────────────────────────────────────────────────────────────────────
# 一、⭐ 两个 `RunContext` 互不串扰 —— 本模块存在的头号理由
# ─────────────────────────────────────────────────────────────────────────────

def test_two_runs_in_one_process_never_share_a_source_ledger():
    """⚠⚠ **原型的来源账是一个模块级 dict，键只有两级、不含书名。**

    于是同一个进程里**读过 A 书再写 B 书**的产物，会落下一条**看不出来的假匹配边**
    ——`_provenance.inputs` 里多一条指向另一本书的记录，而它长得和真的一模一样。
    `R-11.3`「一切写操作走子进程」至今写着这条理由。

    ⇒ 来源账做成 run 级的实例字段之后，那条边**在结构上不存在**。
    这条测试就是那个结构：两本书、同一个进程、同一个键，两份账互不相干。
    """
    base_a, base_b = _book('111', 'A', b'{"sections": [1]}'), _book('222', 'B', b'{"sections": [2]}')
    ctx_a, ctx_b = _ctx(base_a, '111'), _ctx(base_b, '222')
    ArtifactStore(ctx_a).read(Loose, UPSTREAM)
    ArtifactStore(ctx_b).read(Loose, UPSTREAM)

    assert set(ctx_a.edges) == set(ctx_b.edges) == {UPSTREAM}, '两级键本来就该撞——作用域靠实例给'
    assert ctx_a.edges[UPSTREAM].file != ctx_b.edges[UPSTREAM].file, \
        'A 书的指纹与 B 书的一样 —— 两个实例串了，那正是原型那条假匹配边'

    p = ArtifactStore(ctx_a).write(STYLE_REL, _style(ctx_a))
    assert list(_prov_on_disk(p)['inputs']) == [UPSTREAM]
    assert _prov_on_disk(p)['inputs'][UPSTREAM]['file'] == ctx_a.edges[UPSTREAM].file


def test_edges_are_a_snapshot_not_the_live_dict():
    """外面改 `ctx.edges` 不该影响这一次跑——来源账是账，不是可编辑的草稿。"""
    ctx = _ctx(_book())
    ArtifactStore(ctx).read(Loose, UPSTREAM)
    ctx.edges.clear()
    assert set(ctx.edges) == {UPSTREAM}


# ─────────────────────────────────────────────────────────────────────────────
# 二、读：记账在结构上不可能漏
# ─────────────────────────────────────────────────────────────────────────────

def test_reading_records_the_edge_without_anyone_listing_it():
    """⚠ **不在每个落盘点手工列举 `inputs`**：那份清单会烂，而且是**静默**地烂。

    某个节点以后多吃一份上游、却忘了加进清单时，`_provenance` 会继续显示「匹配」。
    改成「谁读了就记谁」之后，**结构上不可能漏**——拿不到数据就等于没读。
    """
    ctx = _ctx(_book(), node='stage0.remove', paid=False)
    obj = ArtifactStore(ctx).read(Loose, UPSTREAM)
    assert obj.sections == []
    assert ctx.edges[UPSTREAM].consumed is None, '没声明投影 ⇒ 这条边诚实地是「不知道」'


def test_reading_with_a_projection_fingerprints_the_projection_not_the_whole_file():
    """**建槽位，不填槽位**——但槽位一旦填了，新鲜度才可能判成 `fresh`。

    ⚠⚠ **必须断言到值，不能只断言「非 None」。**
    把 `projection_fingerprint(project(data))` 写成 `projection_fingerprint(data)`
    ——只错一个函数调用——`consumed` 照样非空、照样是 10 位十六进制，
    而这条边从此指的是**整份文件**：`Q-4` 那一格（「改一个不相干的字段不该让我的结论作废」）
    **静默失效**，`fresh` 再也不会因为「我依赖的那部分没变」而出现。
    一条只查「非 None」的断言在这里等于没查。
    """
    payload = json.dumps([
        {'chapter_ordinal': 1, 'text': '真正读的正文', 'source_chapter_label': '没读的标签'},
    ], ensure_ascii=False).encode('utf-8')
    base = _book('123', '书名', payload)
    ctx = _ctx(base)
    ArtifactStore(ctx).read(MainTextLoose, UPSTREAM)

    from novel.prov import projection_fingerprint
    assert ctx.edges[UPSTREAM].consumed == projection_fingerprint(['真正读的正文'])
    assert ctx.edges[UPSTREAM].consumed != projection_fingerprint(
        json.loads((base / '书名_123' / 'stage1' / 'main_text.json').read_text('utf-8'))), \
        '投影指纹等于整份文件的指纹 —— `project()` 多半没被调用'


def test_freshness_reuses_the_registered_projection_that_wrote_the_edge():
    payload = [
        {'chapter_ordinal': 1, 'text': '正文不变', 'source_chapter_label': '第一章'},
    ]
    base = _book(
        payload=json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    ctx = _ctx(base)
    st = ArtifactStore(ctx)
    st.read(MainTextLoose, UPSTREAM)
    st.write(STYLE_REL, _style(ctx))

    upstream = base / '书名_123' / UPSTREAM
    assert st.freshness(STYLE_REL).state == 'fresh'

    payload[0]['source_chapter_label'] = '标签改了但本节点不读'
    upstream.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    same = st.freshness(STYLE_REL)
    assert (same.state, same.reason) == ('fresh', 'match')

    payload[0]['text'] = '真正消费的正文改了'
    upstream.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    changed = st.freshness(STYLE_REL)
    assert (changed.state, changed.reason) == ('stale', 'consumed_changed')


def test_stage4_blueprint_projection_tracks_exact_material_and_ignores_provenance():
    plan_project = projection_for('stage4.plan', 'stage2/chapter_blueprints.json')
    context_project = projection_for('stage4.context', 'stage2/chapter_blueprints.json')
    assert plan_project is not None and context_project is not None
    base = {
        'blueprints': [{
            'chapter_ordinal': 1, 'summary': '摘要',
            'narration': [{
                'grammatical_person': 'third', 'narrator_char_id': None,
                'focalizer_char_id': 'char_001', 'pronoun_surfaces': [],
            }],
            'beats': [{
                'beat_id': 'b01_01', 'event': '事件', 'time_layer': 'present',
                'place': '庭院', 'present_char_ids': ['char_001'],
                'span': {
                    'text': '原文区间。', 'start': 0, 'end': 5,
                    'normalized': '原文区间', 'recovery': 'exact',
                },
                'hinges': [{
                    'kind': '要害台词',
                    'source': {
                        'text': '原话。', 'start': 0, 'end': 3,
                        'normalized': '原话', 'recovery': 'exact',
                    },
                }],
            }],
        }],
        '_provenance': {'code': 'old'},
    }
    changed_provenance = {**base, '_provenance': {'code': 'new'}}
    assert plan_project(base) == plan_project(changed_provenance)
    assert context_project(base) == context_project(changed_provenance)
    changed = copy.deepcopy(base)
    changed['blueprints'][0]['beats'][0]['hinges'][0]['source']['text'] = '改过。'
    assert plan_project(base) != plan_project(changed)
    assert context_project(base) != context_project(changed)

    changed_summary = copy.deepcopy(base)
    changed_summary['blueprints'][0]['summary'] = '新摘要'
    assert plan_project(base) != plan_project(changed_summary)
    assert context_project(base) == context_project(changed_summary)

    changed_narration = copy.deepcopy(base)
    changed_narration['blueprints'][0]['narration'][0]['grammatical_person'] = 'first'
    assert plan_project(base) == plan_project(changed_narration)
    assert context_project(base) != context_project(changed_narration)


def test_projection_registry_resolves_dynamic_and_invalid_artifact_paths():
    assert producer_node('stage5/deep/ch01.json') == 'stage5.write'
    assert producer_node('stage6/hook.INVALID.json') == 'stage6.hook'
    assert set(CONSUMED_PROJECTIONS['stage4.context']) == {
        'stage1/main_text.json',
        'stage2/chapter_blueprints.json',
        'stage2/global_grounding.json',
        'stage4/plan_chapters.json',
    }
    assert projection_for(
        'stage3.style', 'stage1/main_text.INVALID.json'
    ) is projection_for('stage3.style', UPSTREAM)


def test_stage5_review_tracks_context_code_without_overinvalidating_writing():
    write_projection = projection_for('stage5.write', 'stage4/writer_context.json')
    review_projection = projection_for('stage5.review', 'stage4/writer_context.json')
    assert write_projection is not None and review_projection is not None
    before = {
        'chapters': [{'output_ordinal': 1}],
        'world_facts': {'characters': {'c1': {'display_name': '甲'}}},
        '_provenance': {'code': 'old-code'},
    }
    after = {**before, '_provenance': {'code': 'new-code'}}
    assert write_projection(before) == write_projection(after)
    assert review_projection(before) != review_projection(after)


def test_a_draft_cannot_be_read_through_the_edge_path():
    """⚠⚠ 成稿走 `read_judged`，**不走 `read`**（`_common.md` §3.2）。

    `stage5/deep/ch01.json` 在两级键下会塌成一个**反查不到文件**的键 ⇒ 指纹取不到
    ⇒ 那条边**永远显示对得上**。**一个假的匹配比没有记录更坏。**
    """
    base = _book()
    (base / '书名_123' / 'stage5' / 'deep').mkdir(parents=True)
    (base / '书名_123' / 'stage5' / 'deep' / 'ch01.json').write_bytes(b'{"sections": []}')
    ctx = _ctx(base)
    st = ArtifactStore(ctx)
    with pytest.raises(PathRefused, match='read_judged'):
        st.read(Loose, 'stage5/deep/ch01.json')

    _obj, fp, consumed = st.read_judged(Loose, 'stage5/deep/ch01.json')
    assert ctx.judged == {'stage5/deep/ch01.json': fp}, '`judged` 存的是**完整**相对路径'
    assert ctx.edges == {}, '成稿不许进 inputs'
    assert consumed is None


def test_a_missing_upstream_and_a_broken_one_are_different_terminal_states():
    """⚠ 「上游还没跑」是流程问题，「上游读不出来」是产物坏了。

    混成一条之后，报告会把「先跑 stage2」说成「stage2 的产物损坏了」——
    而半截 JSON 被读成「上游还没产出」正是原型把一次**成功的付费跑**报成失败的路径。
    """
    base = _book()
    st = ArtifactStore(_ctx(base))
    with pytest.raises(UpstreamMissing):
        st.read(Loose, 'stage2/global_grounding.json')

    (base / '书名_123' / 'stage2').mkdir()
    (base / '书名_123' / 'stage2' / 'global_grounding.json').write_bytes(b'{"sections": [1')
    with pytest.raises(ArtifactUnreadable, match='合法 JSON'):
        st.read(Loose, 'stage2/global_grounding.json')


# ─────────────────────────────────────────────────────────────────────────────
# 三、写：往返 · 原子 · 两道闸
# ─────────────────────────────────────────────────────────────────────────────

def test_what_we_write_is_what_we_can_read_back():
    """⭐ **落盘 → 加载的往返**，A2/A3 从来没跑过一次（它们只做过「盘上 → 模型」）。

    ⚠ 这条抓到过一个真 bug：`Provenance.to_disk()` 原来是 `model_dump(exclude_none=True)`，
    而 `exclude_none` 是**全局**的 ⇒ 它连着剥掉两处**必填但可为 `null`** 的键
    （`inputs[].consumed` 与 `usage.*_tokens`），于是**落下去的产物读不回来**。
    """
    ctx = _ctx(_book())
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    ctx.record_edge(UPSTREAM, InputEdge(
        file=ctx.edges[UPSTREAM].file, consumed=None))
    ctx.ledger.record(node=ctx.node, sid=ctx.sid, model='m', code=ctx.code,
                      cached=False, cache_policy='default')     # 响应没给 usage ⇒ token 三项 null
    p = st.write(STYLE_REL, _style(ctx))

    raw = json.loads(p.read_text(encoding='utf-8'))
    assert raw['_provenance']['inputs'][UPSTREAM]['consumed'] is None, \
        '`consumed` 的键被抹掉了——「必填但可为 null」与「三态字段」语义相反，不许一个开关同管'
    assert raw['_provenance']['usage']['prompt_tokens'] is None, \
        '`null` = 这次响应没给 usage，**不是 0**；抹掉这个键，产物就再也加载不回来'
    StyleGrounding.model_validate(raw)


def _spend(ctx: RunContext, **tokens) -> None:
    """记一次真调用。`tokens` 里没给的那几项 = 这次响应没报它们。"""
    ctx.ledger.record(node=ctx.node, sid=ctx.sid, model='m', code=ctx.code,
                      cached=False, cache_policy='default', **tokens)


def _mint(ctx: RunContext) -> StyleProvenance:
    """铸一份付费来源账。**只为了拿它的 `usage`**，别的字段取最小合法值。"""
    return ctx.llm_provenance(
        StyleProvenance, prompts={'STYLE_SYS': 'x'}, model='m',
        attempts=[dict(kind='initial', user_prompt='0' * 10, output='1' * 10,
                       cache_policy='default')])


def test_each_artifact_gets_its_own_usage_not_the_whole_runs_total():
    """⭐⭐ `_provenance.usage` 答的是「**这一份**花了多少」，不是「这次跑花了多少」。

    ⚠ 这里原来直接写 `ledger.usage()`，而那是 **run 累计**。一次跑产 N 份产物
    （stage5 逐章正是这个形状）时，第 2 份带着第 1 份的账、第 3 份带着前两份的，
    一路加上去 —— 而**返工回路的边际成本**正是要从这一格里读的，从此读不出来，
    账面还看着都对。两份账答的是两个问题（`_common.md` §八）：
    账本答「这周/这本书花了多少」，产物里那一份答「这一份花了多少」。
    """
    ctx = _ctx(_book())
    _spend(ctx, prompt_tokens=10, completion_tokens=5, reasoning_tokens=1)
    a = _mint(ctx)
    _spend(ctx, prompt_tokens=20, completion_tokens=7, reasoning_tokens=2)
    b = _mint(ctx)

    assert (a.usage.calls_made, a.usage.prompt_tokens) == (1, 10)
    assert (b.usage.calls_made, b.usage.prompt_tokens) == (1, 20), \
        '第二份产物带上了第一份的账'
    assert b.usage.completion_tokens == 7 and b.usage.reasoning_tokens == 2
    # 账本自己仍然是 run 总量——它答的是另一个问题
    assert (ctx.ledger.usage().calls_made, ctx.ledger.usage().prompt_tokens) == (2, 30)


def test_every_artifacts_usage_sums_back_to_the_run_total():
    """守卫的守卫：按份切开之后，**各份之和必须等于账本的总量**。

    差一分钱就说明有一段窗口的账**谁都没认领**（或者被认领了两次）。
    """
    ctx = _ctx(_book())
    parts = []
    for i in (1, 2, 3):
        _spend(ctx, prompt_tokens=i, completion_tokens=i * 2, reasoning_tokens=i * 3)
        parts.append(_mint(ctx).usage)
    total = ctx.ledger.usage()
    for k in ('calls_made', 'calls_cached', 'prompt_tokens',
              'completion_tokens', 'reasoning_tokens'):
        assert sum(getattr(u, k) for u in parts) == getattr(total, k), f'{k} 的账对不上'


def test_a_token_kind_the_response_never_reported_stays_null():
    """⭐ **`null` = 这次响应没给 usage，不是 0**（`_common.md` §八）。

    ⚠ 原来三项共用**一个** `_saw_token_counts` 开关。于是一次只给了 `prompt_tokens`
    的响应（DeepSeek 实测有）会把另外两项写成 `0`——而「没给」与「真的没花」
    是必须分得开的两件事。一个全局开关同时管三件事，必然把其中两件搞错。
    """
    ctx = _ctx(_book())
    _spend(ctx, prompt_tokens=10)              # ← 只报了这一项
    u = _mint(ctx).usage
    assert u.prompt_tokens == 10
    assert u.completion_tokens is None and u.reasoning_tokens is None, \
        '没报过的那两项被写成了 0——「没给」与「真的没花」混掉了'


def test_the_same_usage_mark_can_never_be_committed_twice():
    """同一把刻度提交两次 = 两份产物各自认领同一批 token。**显式终态，不是一笔悄悄重复的账。**"""
    led = CostLedger()
    mark = led.snapshot()
    led.record(node='stage3.style', sid='1', model='m', code='c',
               cached=False, cache_policy='default', prompt_tokens=3)
    assert led.usage_since(mark).prompt_tokens == 3
    led.commit(mark)
    with pytest.raises(ProvenanceRefused, match='结算了两次'):
        led.commit(mark)


def test_a_failed_mint_does_not_push_the_usage_mark():
    """⭐ **推刻度必须在构造成功之后。**

    来源账自己带后置条件（`StyleProvenance.attempts` 非空），构造是会抛的。
    抛了还把刻度推走，那段窗口的账就再也没有产物认领它——
    Σ（每份）== run 总量 当场破，而破得**一声不响**。
    """
    ctx = _ctx(_book())
    _spend(ctx, prompt_tokens=10)
    with pytest.raises(Exception):
        ctx.llm_provenance(StyleProvenance, prompts={'S': 'x'}, model='m', attempts=[])
    # 刻度没动 ⇒ 下一份产物照样认领得到这 10 个 token
    assert _mint(ctx).usage.prompt_tokens == 10


def test_a_run_with_no_calls_still_has_no_usage_key():
    """0 成本节点是**键不在**，不是一份全 0 的 usage——按份结算之后这条仍然成立。"""
    ctx = _ctx(_book(), paid=False)
    assert ctx.free_provenance().usage is None


def test_three_state_keys_are_absent_on_disk_not_null():
    """三态那四个键：`None` ⇒ **键不在**。`revalidated: null` 与「没有这个键」是两份字节。"""
    ctx = _ctx(_book())
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    d = _prov_on_disk(st.write(STYLE_REL, _style(ctx)))
    for k in ('revalidated', 'gates_skipped', 'origin', 'input_basis', 'usage'):
        assert k not in d, f'{k} 是 None，不该出现在盘上'


def test_the_write_gate_refuses_prompts_without_inputs():
    """⚠⚠ **原型这里是 `print` 一句警告然后照写。**

    代价：`chapter_blueprints` 漏记 global · `style_grounding` 与 `fidelity_reference`
    的 `inputs` 干脆是空的。一份自称能证明来源的记录悄悄漏掉一个来源，
    **比没有这个记录更坏**——下游会拿它当证据。
    """
    ctx = _ctx(_book())                         # ← 一次 read 都没有 ⇒ inputs 为空
    with pytest.raises(ProvenanceRefused, match='inputs'):
        ArtifactStore(ctx).write(STYLE_REL, _style(ctx))


def test_a_free_run_cannot_mint_a_provenance_that_names_a_model():
    """⚠ 「0 成本不许记 `model`」这道闸开在**铸造口**，不开在写盘口。

    理由是实测的：写盘口拦不住它，因为**五条 0 成本路径本来就要写回一份带 `model` 的
    来源账**（`--revalidate` / `--remeasure` / `--recheck` / `stage6.approve` /
    `stage7.approve` 改写的都是**付费节点产的**那份产物，它记的 model 属于当初那次付费产出）。
    ⇒ 真正该拦的是「0 成本的跑**现造**一份声称模型产了它的账」，而现造只有一个入口。
    """
    ctx = _ctx(_book(), paid=False)
    with pytest.raises(ProvenanceRefused, match='不花钱'):
        _style(ctx)
    # 0 成本的那一份**结构上**没有 model 这个字段
    free = ctx.free_provenance(prompts={'RULES': '判据表全文'})
    assert 'model' not in free.to_disk() and 'model' not in type(free).model_fields


def test_a_failed_write_never_touches_the_existing_artifact(monkeypatch):
    """**先验证再 replace**：往返校验没过 ⇒ 一个字节都不落盘。

    ⚠ `open(p,'w')` 是「先截断再写」：在它写完之前，盘上那份是**半截 JSON**。
    原型并发落盘时踩过——半截 JSON 的坏处不是读失败，是**读失败会被当成「上游还没产出」**。
    """
    ctx = _ctx(_book())
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    p = st.write(STYLE_REL, _style(ctx, habit='第一版'))
    before = p.read_bytes()

    def boom(*_a, **_k):
        raise ProvenanceRefused('故意让往返校验失败')

    monkeypatch.setattr(ArtifactStore, '_verify_round_trip', staticmethod(boom))
    with pytest.raises(ProvenanceRefused):
        st.write(STYLE_REL, _style(ctx, habit='第二版'))

    assert p.read_bytes() == before, '旧产物被截断/覆盖了——那正是原子写要防的那一瞬间'
    assert not list(p.parent.glob('*.tmp')), '临时文件没清掉'


def test_json_artifacts_are_written_without_cr():
    ctx = _ctx(_book())
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    assert b'\r' not in st.write(STYLE_REL, _style(ctx)).read_bytes()


# ─────────────────────────────────────────────────────────────────────────────
# 四、字节形态产物：`book.txt` 无 `\r`、无 BOM
# ─────────────────────────────────────────────────────────────────────────────

def test_byte_form_artifacts_are_lf_only_and_have_no_bom():
    """⚠ 三份字节形态产物（`clean_text.txt` / `draft.md` / `book.txt`）盘上**全是 LF-only**，
    而 Windows 上 `open(p, 'w')` 默认吐 CRLF ⇒ 同一份代码在两台机器上产出两种字节。
    """
    ctx = _ctx(_book())
    p = ArtifactStore(ctx).write_text('stage7/book.txt', '第一章\n正文\n')
    data = p.read_bytes()
    assert b'\r' not in data
    assert not data.startswith(b'\xef\xbb\xbf'), 'BOM —— `utf-8` 不是 `utf-8-sig`'
    assert data == '第一章\n正文\n'.encode('utf-8')


def test_write_text_refuses_cr_instead_of_normalising_it():
    """⚠ **不静默归一。** 归一是**内容决策**，属产地（它要为「我改了什么」负责）；
    写盘口只保证**平台不掺东西**。替产地做一次它自己没记账的改写，
    正是本项目要消灭的那种「安静地改了点什么」。
    """
    ctx = _ctx(_book())
    with pytest.raises(ByteFormRefused, match=r'\\r'):
        ArtifactStore(ctx).write_text('stage7/book.txt', '第一章\r\n正文\r\n')


def test_read_text_does_not_translate_newlines_either():
    """两侧口径必须一致：写的一侧拒 `\\r`，读的一侧就不许把 `\\r\\n` 悄悄翻成 `\\n`
    ——否则「盘上那份带 CRLF」这件事在读的一侧看不见。"""
    base = _book()
    (base / '书名_123' / 'stage7').mkdir()
    (base / '书名_123' / 'stage7' / 'book.txt').write_bytes(b'a\r\nb\n')
    assert ArtifactStore(_ctx(base)).read_text('stage7/book.txt') == 'a\r\nb\n'


def test_a_non_utf8_byte_artifact_is_an_explicit_terminal_state():
    """⭐ 解不出来的字节形态产物是 `ArtifactUnreadable`，**不是一条裸 `UnicodeDecodeError`**。

    ⚠ `_loads` 早就把 `UnicodeDecodeError` 兜成显式终态了，而 `read_text` 那一侧
    是裸 `raw.decode('utf-8')`——同一件事在两条读路上一条有词汇、一条没有。
    `errors.py` 明写「**不设默认异常**，每一处终态都要选一个具体的类」，
    而只有 `novel/cli/` 会把这些类翻译成退出码：漏网的那一条到了 CLI 就是一段栈。

    ⚠ 顺带钉住**记边在解码之后**：一份读不出来的文件不许在来源账里留下
    一条说「我读过它」的边。
    """
    base = _book()
    (base / '书名_123' / 'stage7').mkdir()
    (base / '书名_123' / 'stage7' / 'book.txt').write_bytes(b'\xff\xfe\x00\x41')
    ctx = _ctx(base)
    with pytest.raises(ArtifactUnreadable, match='UTF-8'):
        ArtifactStore(ctx).read_text('stage7/book.txt')
    assert ctx.edges == {}, '一份一个字都没读到的文件，在来源账里留下了一条边'


def test_a_non_utf8_json_artifact_still_says_it_is_not_utf8_not_bad_json():
    """两条报错必须分得开：**不是 UTF-8** 与 **不是合法 JSON** 是两种不同的坏法。

    合成一句之后，「盘上这份编码错了」会被当成「模型吐了段坏 JSON」去查。
    """
    base = _book(payload=b'\xff\xfe\x00\x41')
    with pytest.raises(ArtifactUnreadable, match='UTF-8'):
        ArtifactStore(_ctx(base)).read(Loose, UPSTREAM)
    base2 = _book(payload=b'{"sections": [')
    with pytest.raises(ArtifactUnreadable, match='合法 JSON'):
        ArtifactStore(_ctx(base2)).read(Loose, UPSTREAM)


# ─────────────────────────────────────────────────────────────────────────────
# 五、`.INVALID`、`gates_skipped`、提升路
# ─────────────────────────────────────────────────────────────────────────────

def _write_invalid(base: Path, gates: list[str], *, habit: str = '习惯一') -> tuple[RunContext, Path]:
    ctx = _ctx(base)
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    return ctx, st.write_invalid(STYLE_REL, _style(ctx, habit=habit), gates_skipped=gates)


def test_gates_skipped_is_written_even_when_empty():
    """⚠ **空列表也要写。**

    空列表 = 这次没有闸门缺料；键不在 = 这份产物早于这套机制。
    它是判据 36 那整套设计的**唯一痕迹**，而「闸门没跑成」与「闸门跑过没事」
    正是 `Q-2` 要求必须能区分的两件事。
    """
    _ctx_, inv = _write_invalid(_book(), [])
    assert inv.name == 'style_grounding.INVALID.json'
    assert _prov_on_disk(inv)['gates_skipped'] == []


def test_invalid_does_not_overwrite_the_official_artifact():
    """`Q-5` 先校验后落盘：阻断级错误**不覆盖已有产物**。"""
    base = _book()
    ctx = _ctx(base)
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    good = st.write(STYLE_REL, _style(ctx, habit='合格的那一版'))
    before = good.read_bytes()
    st.write_invalid(STYLE_REL, _style(ctx, habit='被拦下的那一版'), gates_skipped=['name_leak'])
    assert good.read_bytes() == before


def test_the_official_write_path_refuses_an_invalid_name():
    ctx = _ctx(_book())
    with pytest.raises(PathRefused, match='write_invalid'):
        ArtifactStore(ctx).write('stage3/style_grounding.INVALID.json', _style(ctx))


def test_promotion_keeps_the_original_prompts_and_archives_the_invalid():
    """⭐⭐ 提升路两件事，各对应一次实测损失。

    ① **不许伪造**：原型的 `--revalidate` 盖上当前 SYS 指纹 + 当前 model + 当前 inputs，
       `round` 还 +1，而**没有任何一次新落笔发生过**。
    ② **不许删**（`Q-8`）：原型「读完问题稿就把它删了」造出的正是盘上那条幽灵边
       ——另一份产物的 `inputs` 还指着它。**删一个还被别人指着的文件，会同时制造两个问题。**
    """
    base = _book()
    ctx, inv = _write_invalid(base, ['name_leak'])
    was = _prov_on_disk(inv)

    # 提升走的是一次 **0 成本**的跑（`--revalidate` 在 `FREE_ENTRIES` 里）
    ctx2 = _ctx(base, paid=False)
    out = ArtifactStore(ctx2).promote_invalid(STYLE_REL, StyleGrounding)
    now = _prov_on_disk(out)

    assert now['prompts'] == was['prompts'], '盖上了当前的 SYS 指纹 —— 这就是伪造'
    assert now['model'] == was['model'] and now['inputs'] == was['inputs']
    assert now['revalidated'] is True
    assert 'origin' not in now, '刻意不宣称 human——「判据改了」与「人手改过」在节点这一侧分不开'
    assert 'gates_skipped' not in now, '提升上来的是正式产物，不该再挂缺料记录'
    assert now['code'] == ctx2.code, 'code 是唯一该变的：重判确实是这一次跑的'

    assert not inv.exists()
    archived = sorted(p.name for p in (base / '书名_123' / 'stage3' / '_rounds').iterdir())
    assert archived == ['style_grounding.INVALID.r1.json'], \
        '⚠ 原型归档叫 `*.INVALID.superseded.json`（**没有编号**），第二次会静默覆盖第一次'


def test_a_second_promotion_lands_in_the_next_round_not_on_top_of_the_first():
    """第二次被拦下的稿归档到 `r2`，**不覆盖 `r1`**；同时**上一轮正式稿也进 `_rounds`**。

    ⚠ 这一条挡的是原型那个 `*.INVALID.superseded.json`（无编号）：
    第二次会静默覆盖第一次。轮次由 `seen_rounds` 扫出来，缺号也不会退回去。

    ⚠ 两份 `.INVALID` **必须内容不同**（`habit`），否则第二次提升渲染出的字节
    与盘上正式稿逐字节相同、归档那一步按设计跳过，这条断言就变成靠巧合绿的。
    """
    base = _book()
    _write_invalid(base, [], habit='第一版')
    p = ArtifactStore(_ctx(base, paid=False)).promote_invalid(STYLE_REL, StyleGrounding)
    first = p.read_bytes()
    _write_invalid(base, ['name_leak'], habit='第二版')
    ArtifactStore(_ctx(base, paid=False)).promote_invalid(STYLE_REL, StyleGrounding)

    rounds = base / '书名_123' / 'stage3' / '_rounds'
    archived = sorted(q.name for q in rounds.iterdir())
    assert archived == ['style_grounding.INVALID.r1.json', 'style_grounding.INVALID.r2.json',
                        'style_grounding.r1.json']
    assert (rounds / 'style_grounding.r1.json').read_bytes() == first, \
        '归档里那份不是被换掉的那一版正式稿'
    assert p.read_bytes() != first, '第二次提升根本没改动正式稿？夹具白搭了'


def test_promotion_archives_the_paid_official_instead_of_destroying_it():
    """⭐⭐ **提升不许销毁一份花过钱的正式稿。**

    落正式稿走的是原子替换，而原子替换是**无条件**的：盘上那份原地消失，
    `_rounds/` 里连一份副本都没有——原来只归档 `.INVALID` 那一条历史。
    对 stage5 这直接违反「上一轮正式稿进入 `_rounds`」；对别的层，它违反的是
    本模块自己那句「**归档是出事之后唯一能回去的那一份**」（而且它是花过钱的产物）。
    """
    base = _book()
    # 先有一份**正式**产物（付费产出的那一版）
    ctx0 = _ctx(base)
    st0 = ArtifactStore(ctx0)
    st0.read(Loose, UPSTREAM)
    official = st0.write(STYLE_REL, _style(ctx0, habit='付过钱的那一版'))
    was = official.read_bytes()

    _write_invalid(base, ['name_leak'], habit='被拦下的那一版')
    out = ArtifactStore(_ctx(base, paid=False)).promote_invalid(STYLE_REL, StyleGrounding)

    rounds = base / '书名_123' / 'stage3' / '_rounds'
    assert (rounds / 'style_grounding.r1.json').read_bytes() == was, \
        '旧正式稿被原子替换销毁了，而 `_rounds/` 里没有任何副本'
    assert (rounds / 'style_grounding.INVALID.r1.json').exists(), '`.INVALID` 那条历史也要归档'
    assert json.loads(out.read_text(encoding='utf-8'))['surface_habits'] == ['被拦下的那一版']
    assert _prov_on_disk(out)['revalidated'] is True


def test_a_resumed_promotion_does_not_invent_a_phantom_round():
    """⭐ **字节相同就不叫销毁。**

    ③④ 之间（落正式稿 → 挪 `.INVALID`）硬崩之后重跑，盘上那份已经是提升好的那一份。
    这时候再归档一次就是凭空往轮次账里塞一个**幻影轮**——对 stage5 就是
    「把一次重判记成第 N 轮写作」，正是 `promoted()` 存在要防的那件事。
    ⇒ 归档是为了保住一份**会被销毁**的版本；字节相同 ⇒ 什么都没被销毁 ⇒ 没有可归档的东西。

    这里直接模拟那个崩溃窗口：提升一次之后把 `.INVALID` 放回原位，再提升一次。
    """
    base = _book()
    _write_invalid(base, [], habit='唯一那一版')
    ArtifactStore(_ctx(base, paid=False)).promote_invalid(STYLE_REL, StyleGrounding)

    rounds = base / '书名_123' / 'stage3' / '_rounds'
    # 模拟「正式稿已落、`.INVALID` 还没挪走」那半个刻意选的窗口
    inv = base / '书名_123' / 'stage3' / 'style_grounding.INVALID.json'
    (rounds / 'style_grounding.INVALID.r1.json').rename(inv)
    ArtifactStore(_ctx(base, paid=False)).promote_invalid(STYLE_REL, StyleGrounding)

    archived = sorted(q.name for q in rounds.iterdir())
    assert archived == ['style_grounding.INVALID.r1.json'], \
        f'重跑凭空造出了轮次：{archived}——`--revalidate` 本该自愈，而不是往账上塞幻影'


def test_a_free_produced_invalid_can_be_promoted_too():
    """⭐ `promoted()` 原来只挂在 `LlmProvenance` 上。

    而 `store.promote_invalid` 拿到的 `src` 类型是 `Provenance`：
    `stage4.CtxProvenance` 与 `stage5.ReviewProvenance` 都是 `FreeProvenance` 的子类、
    都是**真实存在**的 0 成本产物，一样会被闸门拦下、一样要走提升路。
    提升它们抛 `AttributeError`——而 `errors.py` 明写「不设默认异常，
    每一处终态都要选一个具体的类」，一条裸 `AttributeError` 连终态都不算。
    """
    from contracts.schema.common import FreeProvenance

    class FreeArtifact(BaseModel):
        """一份 0 成本产出的最小产物。形状照 `CtxProvenance` / `ReviewProvenance` 那一族。"""
        model_config = {'populate_by_name': True}
        note: str
        provenance: FreeProvenance = Field(alias='_provenance')

    base = _book()
    ctx = _ctx(base, paid=False)
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    art = FreeArtifact(note='0 成本算出来的', _provenance=ctx.free_provenance(prompts={'RULES': '判据表'}))
    st.write_invalid(STYLE_REL, art, gates_skipped=['name_leak'])

    out = ArtifactStore(_ctx(base, paid=False)).promote_invalid(STYLE_REL, FreeArtifact)
    now = _prov_on_disk(out)
    assert now['revalidated'] is True
    assert 'model' not in now, '0 成本产物提升上来不许长出一个 `model`'
    assert 'gates_skipped' not in now


def test_promotion_refuses_before_touching_anything_when_the_slot_is_taken(monkeypatch):
    """提升有四步（占归档位 → 归档旧正式稿 → 落正式稿 → 挪 `.INVALID`），
    **大概率的那个失败必须发生在第一步**。

    ⚠ 归档位被占 ⇒ 轮次号算错了。这时候如果已经落了正式稿再报错，
    人看到的是「失败」而盘上其实**变过了**——那比直接失败更难收拾。

    ⚠ 这里 monkeypatch 的是**盘上那份自己记的轮次**（`_provenance.round`），
    因为只有那种产物（stage5 逐章成稿）才可能报出一个已经被占掉的号——
    没有 `round` 字段的层走 `next_round`，它按定义不会撞。
    **这条路今天只有 stage5 走得到，但闸门现在就该在**（C 期之后才发作的守卫，本项目已经踩过三次）。
    """
    base = _book()
    _ctx_, inv = _write_invalid(base, [])
    rounds = base / '书名_123' / 'stage3' / '_rounds'
    rounds.mkdir(parents=True)
    (rounds / 'style_grounding.INVALID.r1.json').write_bytes(b'{"taken": true}')

    st = ArtifactStore(_ctx(base, paid=False))
    monkeypatch.setattr(ArtifactStore, '_round_on_disk', lambda self, rel: 1)
    with pytest.raises(PathRefused, match='一个字节都没动'):
        st.promote_invalid(STYLE_REL, StyleGrounding)
    assert inv.exists(), '`.INVALID` 被挪走了'
    assert not st.exists(STYLE_REL), '正式稿落下去了，而调用方拿到的是一个异常'
    assert sorted(q.name for q in rounds.iterdir()) == ['style_grounding.INVALID.r1.json'], \
        '归档目录变了——有人把「归档旧正式稿」挪到了占位检查之前'


def test_promotion_without_an_invalid_is_a_terminal_state():
    with pytest.raises(UpstreamMissing):
        ArtifactStore(_ctx(_book(), paid=False)).promote_invalid(STYLE_REL, StyleGrounding)


# ─────────────────────────────────────────────────────────────────────────────
# 六、轮次与归档
# ─────────────────────────────────────────────────────────────────────────────

def test_archiving_never_overwrites_an_existing_round():
    """**归档是出事之后唯一能回去的那一份**，不许静默覆盖。

    轮次号算错（`len(glob)+1` 那个式子的典型症状）时，这条是唯一会出声的地方。
    """
    base = _book()
    ctx = _ctx(base)
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    st.write(STYLE_REL, _style(ctx, habit='第一轮'))
    st.archive_round(STYLE_REL)                       # → _rounds/style_grounding.r1.json
    st.write(STYLE_REL, _style(ctx, habit='第二轮'))
    with pytest.raises(PathRefused, match='归档目标已存在'):
        st.archive_round(STYLE_REL, round_no=1)


def test_seen_rounds_counts_the_invalid_and_the_gaps():
    """`round` = **盘上见过的最大轮次 + 1**，含 `.INVALID` 与 `_rounds/`，缺号也算。"""
    base = _book()
    ctx = _ctx(base)
    st = ArtifactStore(ctx)
    rounds = base / '书名_123' / 'stage3' / '_rounds'
    rounds.mkdir(parents=True)
    (rounds / 'style_grounding.r1.json').write_bytes(b'{}')
    (rounds / 'style_grounding.r3.json').write_bytes(b'{}')       # ← r2 被人挪走了
    assert st.seen_rounds(STYLE_REL) == {1, 3}
    assert st.next_round(STYLE_REL) == 4, 'len(glob)+1 会返回 3，与现存的 r3 撞号'


def test_archive_can_keep_the_original_when_the_layer_needs_it():
    """`keep=True` 与默认的「挪走」是**语义差别，不是风格差别**：
    逐章成稿归档之后当前文件必须消失（下一轮马上写一份新的到同一路径），
    而「先抄一份再原地改写」的那几层需要它留着。
    """
    base = _book()
    ctx = _ctx(base)
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    p = st.write(STYLE_REL, _style(ctx))
    st.archive_round(STYLE_REL, keep=True)
    assert p.exists()
    st.archive_round(STYLE_REL, round_no=2)
    assert not p.exists()


# ─────────────────────────────────────────────────────────────────────────────
# 七、新鲜度 —— 幽灵边必须点名
# ─────────────────────────────────────────────────────────────────────────────

def test_a_deleted_upstream_becomes_a_named_ghost_edge():
    """⚠⚠ 盘上真有这么一条（`成年人不做选择` 那本的 `stage4/writer_context.json`
    挂着 `stage4/plan_chapters.INVALID.json`，而那个文件已经不在了）。

    原型的比对函数对这种条目**静默跳过** ⇒ 既不报错、也不报「不知道」。
    """
    base = _book()
    ctx = _ctx(base)
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    st.write(STYLE_REL, _style(ctx))
    assert st.freshness(STYLE_REL).state == 'fresh'

    (base / '书名_123' / 'stage1' / 'main_text.json').unlink()
    r = st.freshness(STYLE_REL)
    assert (r.state, r.reason) == ('unknown', 'ghost')
    assert r.ghosts == (UPSTREAM,), '不点名 = 人无从判断这条边要不要重新挂钩'


def test_freshness_reads_raw_and_survives_an_artifact_that_no_longer_validates():
    """⚠ **判新鲜度不过模型。**

    一份陈旧产物很可能已经不合当前 schema（新契约裁了一批字段之后，
    原型产的 3/3 本全部加载失败），而「它是不是陈旧的」恰恰是这时候最该答得出来的问题。
    让新鲜度依赖校验通过，等于在最需要它的那一刻把它关掉。
    """
    base = _book()
    st = ArtifactStore(_ctx(base))
    (base / '书名_123' / 'stage3').mkdir()
    (base / '书名_123' / 'stage3' / 'style_grounding.json').write_bytes(json.dumps({
        '这份产物早就不合契约了': True,
        '_provenance': {'inputs': {UPSTREAM: {'file': 'ffffffffff', 'consumed': None}}},
    }, ensure_ascii=False).encode('utf-8'))
    r = st.freshness(STYLE_REL)
    assert (r.state, r.reason) == ('stale', 'file_changed')
    with pytest.raises(ArtifactUnreadable):
        st.read(StyleGrounding, STYLE_REL)          # ← 同一份，过模型就是读不出来


def test_a_bad_key_in_a_legacy_provenance_does_not_blow_up_the_report():
    """存量产物里的键可能是任何字符串。**别让一条坏键把整份报告干掉**——
    「有一条边指向已删文件」不该升级成「报告出不来」。"""
    base = _book()
    st = ArtifactStore(_ctx(base))
    (base / '书名_123' / 'stage3').mkdir()
    (base / '书名_123' / 'stage3' / 'style_grounding.json').write_bytes(json.dumps({
        '_provenance': {'inputs': {'../../别的书/x.json': {'file': 'ffffffffff', 'consumed': None}}},
    }).encode('utf-8'))
    r = st.freshness(STYLE_REL)
    assert (r.state, r.reason) == ('unknown', 'ghost')


# ─────────────────────────────────────────────────────────────────────────────
# 八、缓存的两个开关 · 成本账的两个计数器
# ─────────────────────────────────────────────────────────────────────────────

def test_cache_policy_keeps_read_and_write_apart():
    """⚠ 原型只有一个 `--no-cache`，于是「重新采样一次，但仍然记下来」**表达不出来**。

    而那正是最常需要的一档：`R-3.11` 明写定向重生成必须配 `refresh-write`——
    重生成的 prompt 与整跑那一批**逐字相同**，缓存必然命中，
    于是**一个字都没重新生成，而它照样打印「已重生成」**。
    """
    assert (CachePolicy.default().read, CachePolicy.default().write) == (True, True)
    assert (CachePolicy.disabled().read, CachePolicy.disabled().write) == (False, False)
    assert (CachePolicy.refresh().read, CachePolicy.refresh().write) == (False, True)
    assert CachePolicy.refresh().name == 'refresh-write'
    assert CachePolicy.of('disabled') == CachePolicy.disabled()


def test_an_unnamed_cache_combination_is_refused():
    """第四种组合被拒，而理由不是「它没有意义」——是**它在 `CACHE_POLICY` 里没有名字**。

    一个记不进 `_provenance` 的口径，事后就没有任何东西说得出「那一次到底是怎么跑的」。
    """
    with pytest.raises(ValueError, match='没有名字'):
        CachePolicy(read=True, write=False)


def test_the_ledger_keeps_two_counters_and_appends_lf_only():
    """⚠⚠ **`calls_made` 与 `calls_cached` 不许合并。**

    只数真调用，会让「参数不变的重跑什么都没跑」（`Q-11` / `R-12.4` 要求**显式说出来**
    的那条语义）与「什么都没干」在报告上**同形**。
    """
    base = Path(tempfile.mkdtemp())
    led = CostLedger(path=ledger_path(base))
    assert led.usage() is None, '一次调用都没有 ⇒ 键不在，不是一份全 0 的 usage'

    for cached in (False, True, True):
        led.record(node='stage3.style', sid='1', model='m', code='c',
                   cached=cached, cache_policy='default',
                   prompt_tokens=10, completion_tokens=5, reasoning_tokens=1)
    u = led.usage()
    assert isinstance(u, Usage) and (u.calls_made, u.calls_cached) == (1, 2)
    assert (u.prompt_tokens, u.completion_tokens, u.reasoning_tokens) == (30, 15, 3)

    data = ledger_path(base).read_bytes()
    assert b'\r' not in data, 'Windows 的文本模式会把 `\\n` 写成 `\\r\\n`'
    assert len(data.decode('utf-8').strip().splitlines()) == 3, '缓存命中也要记一行'


def test_the_ledger_reports_null_tokens_when_the_response_gave_none():
    """`null` = 这次响应没给 usage，**不是 0**（DeepSeek 实测会有不带 usage 的响应）。"""
    led = CostLedger()
    led.record(node='stage3.style', sid='1', model='m', code='c',
               cached=False, cache_policy='default')
    u = led.usage()
    assert u is not None and u.calls_made == 1
    assert (u.prompt_tokens, u.completion_tokens, u.reasoning_tokens) == (None, None, None)


# ─────────────────────────────────────────────────────────────────────────────
# 九、`RunContext` 的身份
# ─────────────────────────────────────────────────────────────────────────────

def test_an_unknown_node_id_is_refused_at_construction():
    """⚠ `n1..n9` 整套已退役——作业 id 是 `<stage>.<动词>`，唯一落点 `invariants.NODES`。"""
    base = _book()
    with pytest.raises(ValueError, match='NODES'):
        RunContext.open(base, '123', 'n3_style', paid=True, write_ledger=False)


def test_the_context_takes_its_identity_from_the_directory():
    ctx = _ctx(_book('7581413687886353176', '成年人不做选择，姐全都要'), '7581413687886353176')
    assert ctx.sid == '7581413687886353176'
    assert ctx.title == '成年人不做选择，姐全都要'
    assert ctx.stage == 'stage3'


def test_opening_a_book_that_is_not_there_is_a_terminal_state():
    with pytest.raises(UpstreamMissing, match='先跑 stage1'):
        RunContext.open(Path(tempfile.mkdtemp()), '没有这本书', 'stage3.style',
                        paid=True, write_ledger=False)


# ─────────────────────────────────────────────────────────────────────────────
# 十、⭐ 盘上 92 份真实产物走一遍**落盘往返** —— 八层一次全过，0 成本
# ─────────────────────────────────────────────────────────────────────────────

_CORPUS = list(corpus.iter_artifacts()) if corpus.corpus_root() else []
_NO_CORPUS = pytest.mark.skipif(
    not _CORPUS,
    reason=(f'找不到语料目录（`{corpus.CORPUS_ENV}` 或 `../Novel_refactoring/Output`）'
            f'——**这一条现在没在查，不是「查过没事」**'))


@_NO_CORPUS
@pytest.mark.parametrize('a', _CORPUS, ids=[f'{x.source_id[-4:]}/{x.rel}' for x in _CORPUS])
def test_every_corpus_artifact_survives_a_write_read_round_trip(a):
    """⭐ **模型 → 盘上 → 模型**，拿 3 本书 × 92 份真实产物跑一遍，覆盖八层全部产物类型。

    ⚠ A3 的语料体检做的是**单向**的（盘上 → 模型）。单向永远发现不了这一类：
    `Provenance.to_disk()` 曾经用 `model_dump(exclude_none=True)`，
    而 `exclude_none` 是**全局**的 ⇒ 它连着剥掉两处**必填但可为 `null`** 的键
    （`inputs[].consumed` 与 `usage.*_tokens`）⇒ **落下去的产物读不回来**。
    加载体检 92 份全绿，而第一份真落盘就会炸。

    ⚠ 这条**不产生第二份语料**：写进 `mkdtemp()`，跑完就扔（同 `tests/corpus.py`
    那条「别把迁移后的语料写成文件」——那说的是落点，不是临时目录）。
    """
    if (a.source_id, a.rel) in corpus.MISSING_PROVENANCE:
        pytest.skip('这一份盘上就没有 `_provenance`，加载不了——它是 stage4 事实⑤ 的铁证，'
                    '**不许靠伪造一份来源账把它变绿**')
    obj = a.model.model_validate(corpus.load_cut(a))

    base = Path(tempfile.mkdtemp())
    (base / 'roundtrip_1').mkdir(parents=True)
    ctx = RunContext.open(base, '1', 'stage3.style', paid=True, write_ledger=False)
    st = ArtifactStore(ctx)

    p = st.write(a.rel, obj)
    assert b'\r' not in p.read_bytes()
    assert a.model.model_validate(json.loads(p.read_text(encoding='utf-8'))) == obj, \
        '读回来的模型与写出去的那一份不等——某个键在落盘形态里被抹掉或改了形状'
