#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_store.py —— 唯一读写口 + 一次跑的上下文。

每条断言各对应一次**已经发生过**的损失（原型实测，不是设想）：
进程全局来源账把 A 书的边记到 B 书上 · 提升路伪造一份来源账 ·
`prompts` 非空而 `inputs` 是空的却照样落盘 · 归档没编号被覆盖 ·
`.INVALID` 读完就删造出一条幽灵边 · Windows 上 `open(p,'w')` 把 LF 吐成 CRLF。

测试临时根由 pytest 配置固定在仓库内，避免依赖机器级 `%TEMP%` ACL。
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, RootModel

from contracts.artifacts import (
    STAGE0_REMOVAL_REPORT, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
    STAGE1_PARTITION_REPORT, STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW,
)
from contracts.schema import (
    ArtifactVersionError, InputEdge, StyleGrounding, Usage, artifact_spec,
    decode_artifact, encode_artifact,
)
from contracts.schema.stage3 import StyleProvenance
from novel.consumption import CONSUMED_PROJECTIONS, producer_node, projection_for
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, ByteFormRefused, PathRefused,
    ProvenanceRefused, UpstreamMissing,
)
from novel import paths, prov
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, ledger_path
from novel.orchestrator.uploads import save_upload
from novel.quality.global_review import build_review
from novel.run import RunContext
from novel.store import (
    ArtifactStore, SELF_FRESH_DETERMINISTIC, _json_pointer_diff,
)
from tests import corpus, sandbox

STYLE_REL = 'stage3/style_grounding.json'
UPSTREAM = 'stage1/main_text.json'
REVALIDATION = {'actor': 'test-reviewer', 'reason': '逐项复核当前判据'}


class Loose(BaseModel):
    """只用来测记账的最小上游模型——本文件不测 stage1 的契约。"""
    sections: list


class MainTextLoose(RootModel[list[dict]]):
    """只验证注册投影的最小正文载体；业务 schema 另有自己的测试。"""


# ─────────────────────────────────────────────────────────────────────────────
# 夹具：一本书 + 一次跑
# ─────────────────────────────────────────────────────────────────────────────

def _book(sid: str = '123', title: str = '书名', payload: bytes = b'{"sections": []}',
          *, with_clean: bool = True, root: Path | None = None) -> Path:
    base = root if root is not None else Path(tempfile.mkdtemp())
    book = base / f'{title}_{sid}'
    (book / 'stage1').mkdir(parents=True)
    if with_clean:
        (book / 'stage0').mkdir()
        (book / 'stage0' / 'clean_text.txt').write_bytes(b'fixture clean root\n')
    try:
        logical = json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        disk = payload
    else:
        disk = json.dumps(
            encode_artifact(UPSTREAM, logical), ensure_ascii=False,
        ).encode('utf-8')
    (book / 'stage1' / 'main_text.json').write_bytes(disk)
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
        attempts=[dict(kind='chapter_aggregate', user_prompt='0' * 10, output='1' * 10,
                       cache_policy='default')])
    return StyleGrounding(
        voice=voice,
        surface_habits=[habit],
        hook_style=hook,
        measurable=dict(body=body, hook=blk),
        chapter_styles=[dict(
            source_ordinal=1,
            voice=voice,
            surface_habits=[habit],
        )],
        _provenance=prov,
    )


def _prov_on_disk(p: Path) -> dict:
    return json.loads(p.read_text(encoding='utf-8'))['_provenance']


def _write_payload(book: Path, rel: str, payload: dict) -> None:
    p = book / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(encode_artifact(rel, payload), ensure_ascii=False),
        encoding='utf-8', newline='',
    )


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
        {'chapter_ordinal': 1, 'text': '真正读的正文', 'source_chapter_no': 7},
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
        {'chapter_ordinal': 1, 'text': '正文不变', 'source_chapter_no': 1},
    ]
    base = _book(
        payload=json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    ctx = _ctx(base)
    st = ArtifactStore(ctx)
    st.read(MainTextLoose, UPSTREAM)
    st.write(STYLE_REL, _style(ctx))

    upstream = base / '书名_123' / UPSTREAM
    assert st.freshness(STYLE_REL).state == 'fresh'

    payload[0]['source_chapter_no'] = 99      # 标号改了，但本节点不读它
    upstream.write_text(
        json.dumps(encode_artifact(UPSTREAM, payload), ensure_ascii=False),
        encoding='utf-8',
    )
    same = st.freshness(STYLE_REL)
    assert (same.state, same.reason) == ('fresh', 'match')

    payload[0]['text'] = '真正消费的正文改了'
    upstream.write_text(
        json.dumps(encode_artifact(UPSTREAM, payload), ensure_ascii=False),
        encoding='utf-8',
    )
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


def _stage4_grounding_payload() -> dict:
    return {
        'entities': {
            'characters': {
                'char_001': {
                    'canonical': '甲',
                    'role': '角色',
                    'name_distinctiveness': 'high',
                    'address': [{
                        'surface': '掌柜', 'from_chapter': 1,
                        'by': 'public', 'context': 'address',
                    }],
                },
            },
            'relations': [],
        },
        'canon': {
            'setting': {'时代背景': '架空', '世界观': '普通城镇'},
            'secrets': [{
                'secret_id': 'secret_01', 'fact': '真相',
                'character_known_by': ['char_001'],
                'setup_chapters': [1],
                'reader_reveal_events': [{'chapter': 2}],
            }],
            'world_rules': [{
                'rule_id': 'wr_01', 'kind': 'legal_rule',
                'versions': [{
                    'effective_from_chapter': 1, 'desc': '夜间实行宵禁',
                }],
            }],
        },
        'arc': {'conflict_stages': []},
        '_provenance': {'code': 'source-v1'},
    }


def _write_stage4_projection_subject(
    base: Path, grounding: dict, *, node: str, rel: str,
) -> ArtifactStore:
    book = base / '书名_123'
    _write_payload(book, STAGE2_GLOBAL_GROUNDING, grounding)
    source = book / STAGE2_GLOBAL_GROUNDING
    project = projection_for(node, STAGE2_GLOBAL_GROUNDING)
    assert project is not None
    _write_payload(book, rel, {
        'output_chapters': [],
        '_provenance': {
            'root_clean_sha1': prov.content_sha1(b'fixture clean root\n'),
            'inputs': {
                STAGE2_GLOBAL_GROUNDING: {
                    'file': prov.fingerprint_bytes(source.read_bytes()),
                    'consumed': prov.projection_fingerprint(project(grounding)),
                },
            },
        },
    })
    return ArtifactStore(_ctx(base, node=node, paid=False))


@pytest.mark.parametrize(('node', 'rel'), (
    ('stage4.plan', 'stage4/plan_chapters.json'),
    ('stage4.context', 'stage4/writer_context.json'),
))
@pytest.mark.parametrize('edited_field', ('known_by', 'world_rule_kind'))
def test_stage4_grounding_edits_invalidate_real_store_freshness(
    node: str, rel: str, edited_field: str,
):
    base = _book()
    grounding = _stage4_grounding_payload()
    store = _write_stage4_projection_subject(
        base, grounding, node=node, rel=rel)
    assert store.freshness(rel).state == 'fresh'

    if edited_field == 'known_by':
        grounding['canon']['secrets'][0]['character_known_by'] = ['在场众人']
    else:
        grounding['canon']['world_rules'][0]['kind'] = 'institution'
    _write_payload(base / '书名_123', STAGE2_GLOBAL_GROUNDING, grounding)

    report = store.freshness(rel)
    assert (report.state, report.reason) == ('stale', 'consumed_changed')
    edge = next(edge for edge in report.edges
                if edge.key == STAGE2_GLOBAL_GROUNDING)
    assert edge.recorded_consumed != edge.now_consumed


@pytest.mark.parametrize(('node', 'rel'), (
    ('stage4.plan', 'stage4/plan_chapters.json'),
    ('stage4.context', 'stage4/writer_context.json'),
))
def test_stage4_grounding_projection_ignores_unconsumed_source_provenance(
    node: str, rel: str,
):
    base = _book()
    grounding = _stage4_grounding_payload()
    store = _write_stage4_projection_subject(
        base, grounding, node=node, rel=rel)

    grounding['_provenance']['code'] = 'source-v2'
    _write_payload(base / '书名_123', STAGE2_GLOBAL_GROUNDING, grounding)

    report = store.freshness(rel)
    assert (report.state, report.reason) == ('fresh', 'match')
    edge = next(edge for edge in report.edges
                if edge.key == STAGE2_GLOBAL_GROUNDING)
    assert edge.recorded_file != edge.now_file
    assert edge.recorded_consumed == edge.now_consumed


def test_projection_registry_resolves_dynamic_and_invalid_artifact_paths():
    assert producer_node('stage5/ch01.json') == 'stage5.write'
    assert producer_node('stage5/deep/ch01.json') is None, \
        '退役双臂路径只作 legacy 证据，不能冒充当前 producer 产物'
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

    这里保留旧三级路径作反例：它在两级 inputs 键下会塌成一个反查不到的键。
    现役 canonical 虽只有两级也仍走 judged，因为成稿是被审判主体，且要另记正文消费投影。
    """
    base = _book()
    (base / '书名_123' / 'stage5' / 'deep').mkdir(parents=True)
    (base / '书名_123' / 'stage5' / 'deep' / 'ch01.json').write_text(
        json.dumps(encode_artifact('stage5/deep/ch01.json', {'sections': []})),
        encoding='utf-8',
    )
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
    artifact = _style(ctx)
    # consumed=null 会让正式稿的自新鲜度只能是 unknown；正式盘口现在必须拒绝。
    with pytest.raises(ProvenanceRefused, match='freshness 后置条件'):
        st.write(STYLE_REL, artifact)
    assert not st.exists(STYLE_REL), '后置条件失败后新正式稿没有回滚'
    p = st.write_invalid(STYLE_REL, artifact, gates_skipped=[])

    raw = json.loads(p.read_text(encoding='utf-8'))
    assert raw['_provenance']['inputs'][UPSTREAM]['consumed'] is None, \
        '`consumed` 的键被抹掉了——「必填但可为 null」与「三态字段」语义相反，不许一个开关同管'
    assert raw['_provenance']['usage']['prompt_tokens'] is None, \
        '`null` = 这次响应没给 usage，**不是 0**；抹掉这个键，产物就再也加载不回来'
    assert raw['_schema_version'] == artifact_spec(STYLE_REL).current_version
    StyleGrounding.model_validate(decode_artifact(STYLE_REL, raw).payload)


def test_a_tampered_schema_version_is_refused_on_the_real_read_path():
    """⭐⭐ 版本闸在**真 store 路径**上也拦得住，不只在合成 dict 上。

    ⚠ 这条曾叫 `test_v2_provenance_artifact_remains_readable_and_fresh_after_v3_upgrade`，
    断言的正好相反：把版本号改成 2 之后**照样读得回来、照样 fresh**——那是迁移梯子的
    配套用例。梯子拆掉之后（`contracts/schema/__init__.py` 模块头【版本】），
    同一个场景的正确结论是**拒读**。

    ⚠ 与 `tests/test_artifact_versions.py` 那两条参数化的分界线：那边拿合成 dict 扫
    30 份产物的分母，这边走 `ArtifactStore.write` → 真文件 → `read_own` 的整条路，
    查的是「闸真的装在读盘口上」而不是只装在解码函数里。

    ⚠ 版本号**从注册表现算**：本条查的是「篡改版本号就读不回来」，与当前是第几版无关。
    写死 `1`/`2` 时，将来一次合法的 `current_version` +1 会让本条一起红——而那一天真正
    该红的只有 `tests/test_artifact_versions.py` 里那条「全表恒 v1」的反向守卫。
    """
    base = _book()
    ctx = _ctx(base)
    store = ArtifactStore(ctx)
    store.read(Loose, UPSTREAM)
    official = store.write(STYLE_REL, _style(ctx))
    current = artifact_spec(STYLE_REL).current_version
    encoded = json.loads(official.read_text(encoding='utf-8'))
    assert encoded['_schema_version'] == current, '写盘口吐的不是当前版本'

    # 先证明原样能读回来——否则下面那条拒读证明不了是版本号造成的。
    assert ArtifactStore(_ctx(base)).read_own(
        StyleGrounding, STYLE_REL)[0].surface_habits == ['习惯一']

    encoded['_schema_version'] = current + 1
    official.write_text(
        json.dumps(encoded, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='',
    )
    with pytest.raises(ArtifactUnreadable,
                       match=f'schema v{current + 1} 不是当前 v{current}'):
        ArtifactStore(_ctx(base)).read_own(StyleGrounding, STYLE_REL)


def _spend(ctx: RunContext, **tokens) -> None:
    """记一次真调用。`tokens` 里没给的那几项 = 这次响应没报它们。"""
    ctx.ledger.record(node=ctx.node, sid=ctx.sid, model='m', code=ctx.code,
                      cached=False, cache_policy='default', **tokens)


def _mint(ctx: RunContext) -> StyleProvenance:
    """铸一份付费来源账。**只为了拿它的 `usage`**，别的字段取最小合法值。"""
    return ctx.llm_provenance(
        StyleProvenance, prompts={'STYLE_SYS': 'x'}, model='m',
        attempts=[dict(kind='chapter_aggregate', user_prompt='0' * 10, output='1' * 10,
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


def test_self_freshness_failure_restores_official_and_keeps_invalid():
    base = _book()
    ctx = _ctx(base)
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    official = st.write(STYLE_REL, _style(ctx, habit='原正式稿'))
    before = official.read_bytes()
    invalid = st.write_invalid(
        STYLE_REL, _style(ctx, habit='待修问题稿'), gates_skipped=[])
    invalid_before = invalid.read_bytes()

    edge = ctx.edges[UPSTREAM]
    ctx.record_edge(UPSTREAM, InputEdge(file=edge.file, consumed=None))
    with pytest.raises(ProvenanceRefused, match='freshness 后置条件'):
        st.write(STYLE_REL, _style(ctx, habit='来源账写错的新稿'))

    assert official.read_bytes() == before, '后置条件失败没有逐字节恢复旧正式稿'
    assert invalid.read_bytes() == invalid_before, '正式稿还没自证 fresh 就退休了 INVALID'


def test_retiring_invalid_failure_rolls_the_formal_write_back(monkeypatch):
    base = _book()
    ctx = _ctx(base)
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    official = st.write(STYLE_REL, _style(ctx, habit='原正式稿'))
    before = official.read_bytes()
    invalid = st.write_invalid(
        STYLE_REL, _style(ctx, habit='问题稿'), gates_skipped=[])
    invalid_before = invalid.read_bytes()

    def fail_retirement(_rel):
        raise RuntimeError('simulated retirement failure')

    monkeypatch.setattr(st, 'retire_invalid', fail_retirement)
    with pytest.raises(RuntimeError, match='retirement failure'):
        st.write(STYLE_REL, _style(ctx, habit='本来合格的新稿'))
    assert official.read_bytes() == before
    assert invalid.read_bytes() == invalid_before


def test_deterministic_self_fresh_policy_only_guards_artifacts_that_can_self_prove_now():
    assert STAGE1_PARTITION_REPORT in SELF_FRESH_DETERMINISTIC
    assert STAGE2_GLOBAL_REVIEW in SELF_FRESH_DETERMINISTIC
    assert STAGE1_MAIN_TEXT not in SELF_FRESH_DETERMINISTIC
    assert STAGE1_HOOK_COPY not in SELF_FRESH_DETERMINISTIC
    assert STAGE0_REMOVAL_REPORT not in SELF_FRESH_DETERMINISTIC, \
        'reject-new-source 会故意保留旧 clean；该 report 合法地把新 source 显示为 stale'


def test_global_review_pending_decisions_are_fresh_when_required_surface_is_exact():
    base = _book()
    book = base / '书名_123'
    grounding = {
        'entities': {'characters': {}},
        'canon': {'world_rules': [], 'secrets': []},
    }
    _write_payload(book, STAGE2_GLOBAL_GROUNDING, grounding)
    review = build_review(grounding)
    assert review.items and all(item.state == 'pending' for item in review.items)

    store = ArtifactStore(_ctx(base, node='stage2.global', paid=False))
    store.write(STAGE2_GLOBAL_REVIEW, review)

    report = store.freshness(STAGE2_GLOBAL_REVIEW)
    assert (report.state, report.reason) == ('fresh', 'match')
    assert report.edges[0].recorded_consumed == report.edges[0].now_consumed


def test_global_review_surface_drift_is_stale_even_when_source_hash_is_copied():
    base = _book()
    book = base / '书名_123'
    grounding = {
        'entities': {'characters': {}},
        'canon': {'world_rules': [], 'secrets': []},
    }
    _write_payload(book, STAGE2_GLOBAL_GROUNDING, grounding)
    incomplete = build_review(grounding)
    incomplete = incomplete.model_copy(update={'items': incomplete.items[:-1]})
    _write_payload(book, STAGE2_GLOBAL_REVIEW, incomplete.model_dump())

    store = ArtifactStore(_ctx(base, node='stage2.global', paid=False))
    report = store.freshness(STAGE2_GLOBAL_REVIEW)
    assert (report.state, report.reason) == ('stale', 'consumed_changed')
    assert report.edges[0].recorded_file == report.edges[0].now_file
    assert report.edges[0].recorded_consumed != report.edges[0].now_consumed


def test_global_review_source_drift_refuses_write_and_restores_previous_bytes():
    base = _book()
    book = base / '书名_123'
    grounding = {
        'entities': {'characters': {}},
        'canon': {'world_rules': [], 'secrets': []},
    }
    _write_payload(book, STAGE2_GLOBAL_GROUNDING, grounding)
    review = build_review(grounding)
    store = ArtifactStore(_ctx(base, node='stage2.global', paid=False))
    official = store.write(STAGE2_GLOBAL_REVIEW, review)
    before = official.read_bytes()

    changed = copy.deepcopy(grounding)
    changed['canon']['world_rules'].append({'rule_id': 'r1', 'kind': 'hard'})
    _write_payload(book, STAGE2_GLOBAL_GROUNDING, changed)
    assert store.freshness(STAGE2_GLOBAL_REVIEW).reason == 'file_changed'

    with pytest.raises(ProvenanceRefused, match='freshness 后置条件'):
        store.write(STAGE2_GLOBAL_REVIEW, review)
    assert official.read_bytes() == before


def test_global_review_missing_grounding_is_unknown():
    base = _book()
    book = base / '书名_123'
    grounding = {
        'entities': {'characters': {}},
        'canon': {'world_rules': [], 'secrets': []},
    }
    _write_payload(book, STAGE2_GLOBAL_REVIEW, build_review(grounding).model_dump())

    report = ArtifactStore(
        _ctx(base, node='stage2.global', paid=False),
    ).freshness(STAGE2_GLOBAL_REVIEW)
    assert (report.state, report.reason) == ('unknown', 'ghost')
    assert report.edges[0].key == STAGE2_GLOBAL_GROUNDING


def test_json_pointer_diff_uses_rfc6901_escaping_and_stable_order():
    before = {'z': 1, 'a/b': {'~key': ['same', 'old']}}
    after = {'z': 2, 'a/b': {'~key': ['same', 'new']}}
    assert _json_pointer_diff(before, after) == ('/a~1b/~0key/1', '/z')


def test_json_artifacts_are_written_without_cr():
    ctx = _ctx(_book())
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    assert b'\r' not in st.write(STYLE_REL, _style(ctx)).read_bytes()


# ─────────────────────────────────────────────────────────────────────────────
# 四、字节形态产物：成书无 `\r`、无 BOM
# ─────────────────────────────────────────────────────────────────────────────

def _stage0_on_disk(book: Path, source: Path, source_bytes: bytes, clean_bytes: bytes,
                    *, revision_id: str = 'a' * 64) -> None:
    """stage0 的权威格 + 两份物化投影。**freshness 读原始 dict，所以只给它要的那几格。**"""
    (book / 'stage0').mkdir(exist_ok=True)
    (book / 'stage0' / 'clean_text.txt').write_bytes(clean_bytes)
    identity = {
        'source_ref': {'kind': 'external_file',
                       'absolute_path': str(source.resolve())},
        'normalized_source_sha1': prov.content_sha1(source_bytes),
        'clean_sha1': prov.content_sha1(clean_bytes),
    }
    _write_payload(book, 'stage0/revisions.json', {
        'revisions': [{'revision_id': revision_id, 'parent_revision_id': None,
                       'origin': 'machine', 'content': dict(identity)}],
        'current': {'revision_id': revision_id, 'approval': {'status': 'pending'}},
        'candidate_revision_id': None,
        'dismissed': [],
    })
    _write_payload(book, 'stage0/removal_report.json', dict(identity))


def test_deterministic_artifacts_use_their_existing_content_fingerprints_for_freshness():
    """stage0×3、stage1×3 与成书不再结构性 `unknown`。

    这些载体没有通用 `_provenance`，但其 companion 已经记着同一条 HARD 判据使用的
    40 位身份。完成度必须复用那份事实，并在上游变化时给出 stale。
    """
    base = _book(with_clean=False)
    book = base / '书名_123'
    source = base / 'source.txt'
    source_bytes = '开篇。\n\n1\n第一章。\n\n2\n第二章。\n'.encode('utf-8')
    clean_bytes = source_bytes
    source.write_bytes(source_bytes)
    _stage0_on_disk(book, source, source_bytes, clean_bytes)

    input_sha1 = prov.content_sha1(clean_bytes)
    _write_payload(book, 'stage1/hook_copy.json', {'hook': '开篇。'})
    _write_payload(book, 'stage1/partition_report.json', {'input_sha1': input_sha1})

    book_bytes = '1\n第一章。\n\n2\n第二章。\n'.encode('utf-8')
    # 成书按书名命名 ⇒ 取生产那一个落点。⚠ 手写 `stage7/book.txt` 的后果**不是红**：
    #   那条 rel 不再是任何产物，确定性 freshness 桥够不着它，于是它会被当成一份普通
    #   JSON 去解码 ⇒ 报「不是合法 JSON」，把人指向错的层。
    book_rel = paths.book_txt_rel(book)
    (book / 'stage7').mkdir()
    (book / book_rel).write_bytes(book_bytes)
    _write_payload(book, 'stage7/name_map.json', {
        'book_txt': {'sha1': prov.content_sha1(book_bytes), 'chars': 18, 'chapters': 2},
    })

    store = ArtifactStore(_ctx(base, paid=False))
    formerly_unknown = (
        'stage0/revisions.json',
        'stage0/clean_text.txt', 'stage0/removal_report.json',
        'stage1/main_text.json', 'stage1/hook_copy.json',
        'stage1/partition_report.json', book_rel,
    )
    assert {rel: store.freshness(rel).state for rel in formerly_unknown} == {
        rel: 'fresh' for rel in formerly_unknown}

    (book / 'stage0' / 'clean_text.txt').write_bytes(clean_bytes + b'changed\n')
    for rel in formerly_unknown[1:6]:
        assert store.freshness(rel).state == 'stale', rel
    assert store.freshness(book_rel).state == 'fresh'
    # ⚠ **权威格不跟着投影动。** `clean_text.txt` 是 `revisions.json` 的物化投影，
    #   在它**下游**：投影被人动了不代表这条链不是照盘上那份原文建的。
    #   「两份 live 还是不是 `current` 的投影」由判据 `s0_revision_chain` 回答；
    #   若在这里也判 stale，等于让权威格拿自己的影子自证，那条边永远没有判别力。
    assert store.freshness('stage0/revisions.json').state == 'fresh'

    (book / book_rel).write_bytes(book_bytes + b'changed\n')
    assert store.freshness(book_rel).state == 'stale'


def test_the_revision_chain_goes_stale_when_the_original_novel_changes():
    """`stage0/revisions.json` 的**唯一上游是原文**，它变了必须判 stale。

    这份产物 2026-08-22 落地时漏接了确定性 freshness 桥，于是完成度对它恒
    `unknown/no_provenance`——盘上 8 本语料 8/8 复现。修的方式**不是**给它补一份
    `_provenance`（本层是全链源头、`inputs` 恒空，理由写在
    `contracts/schema/stage0.py` 模块头），而是用它 `current` 那一版自己的
    `source_ref` + `normalized_source_sha1` 回答同一个问题。
    """
    base = _book(with_clean=False)
    book = base / '书名_123'
    source = base / 'source.txt'
    source_bytes = '开篇。\n\n1\n第一章。\n'.encode('utf-8')
    source.write_bytes(source_bytes)
    _stage0_on_disk(book, source, source_bytes, source_bytes)
    store = ArtifactStore(_ctx(base, paid=False))
    assert store.freshness('stage0/revisions.json').state == 'fresh'

    source.write_bytes(source_bytes + '2\n第二章。\n'.encode('utf-8'))
    report = store.freshness('stage0/revisions.json')
    assert (report.state, report.reason) == ('stale', 'file_changed')
    # ⚠ 边的 key 是真正读到的那条路径：报告要点名的就是它。
    assert [edge.key for edge in report.edges] == [str(source.resolve())]

    source.unlink()
    ghost = store.freshness('stage0/revisions.json')
    assert (ghost.state, ghost.reason) == ('unknown', 'ghost'), \
        '原文没了是「不知道」，不是「不匹配」——两者对人的下一步是相反的指示'


def test_the_chain_identity_comes_from_the_current_pointer_not_the_first_revision():
    """身份取 `current` 指向的那一版，不是碰巧排在第一条的那一版。

    链上每条 revision 的 `normalized_source_sha1` 由 schema 保证相同，但
    `source_ref` 没有这条约束（人工编辑那一版可以带别的 locator）。读错指针的后果
    不是崩，是**拿一份不生效的旧身份去判新鲜度**——一条永远自洽的假答案。
    """
    base = _book(with_clean=False)
    book = base / '书名_123'
    source_bytes = '开篇。\n\n1\n第一章。\n'.encode('utf-8')
    stale_source = base / 'moved_away.txt'
    live_source = base / 'source.txt'
    live_source.write_bytes(source_bytes)
    identity = {
        'normalized_source_sha1': prov.content_sha1(source_bytes),
        'clean_sha1': prov.content_sha1(source_bytes),
    }
    (book / 'stage0').mkdir()
    (book / 'stage0' / 'clean_text.txt').write_bytes(source_bytes)
    _write_payload(book, 'stage0/revisions.json', {
        'revisions': [
            {'revision_id': 'b' * 64, 'parent_revision_id': None, 'origin': 'machine',
             'content': dict(identity, source_ref={
                 'kind': 'external_file',
                 'absolute_path': str(stale_source.resolve())})},
            {'revision_id': 'c' * 64, 'parent_revision_id': 'b' * 64,
             'origin': 'human_edit',
             'content': dict(identity, source_ref={
                 'kind': 'external_file',
                 'absolute_path': str(live_source.resolve())})},
        ],
        'current': {'revision_id': 'c' * 64, 'approval': {'status': 'pending'}},
        'candidate_revision_id': None,
        'dismissed': [],
    })
    store = ArtifactStore(_ctx(base, paid=False))
    report = store.freshness('stage0/revisions.json')
    assert (report.state, report.reason) == ('fresh', 'match')
    assert [edge.key for edge in report.edges] == [str(live_source.resolve())], \
        '读的是链上第一条而不是 current —— 那条 locator 根本不在盘上'


def test_every_produced_artifact_without_provenance_reaches_the_deterministic_bridge():
    """⭐ **本条守的就是 `stage0/revisions.json` 那次漏接本身。**

    没有通用 `_provenance` 槽位的产物只能靠 `_deterministic_freshness` 那座桥回答
    新鲜度。漏接一条的后果**不是报错**，是它在完成度视图里恒
    `unknown/no_provenance`——一份判得出新鲜度的产物被显示成「没有来源账」，
    而这正是新增产物时最容易忘的一步（阶段 C 就忘了）。

    ⚠ 作用域是「节点 `produces` 的、且真会落进完成度 `artifacts[]` 的那些」：
      · `*_edit.json` / `wontfix.json` 这类**人写的输入**不是任何节点的产出，
        它们没有来源账是本分，不该被这条逼着去造一个；
      · `stage5/draft.md` 是无来源块的 canonical 字节投影，必须由确定性桥拿
        当前 `writer_context` 分母与 `stage5/chNN.json` 同尺重算，不能恒 unknown。
    """
    from contracts.invariants import NODES
    from contracts.schema import ARTIFACT_SPECS
    from novel.orchestrator import state as state_view
    from novel.store import PROVENANCE_KEY

    base = _book()
    book = base / '书名_123'
    store = ArtifactStore(_ctx(base, paid=False))

    def carries_provenance(rel: str) -> bool:
        spec = ARTIFACT_SPECS.get(rel)
        if spec is None:                      # 字节形态产物，本来就没有槽位
            return False
        return any((f.alias or name) == PROVENANCE_KEY
                   for name, f in spec.model.model_fields.items())

    produced = {rel for node in NODES.values() for rel in node.produces}
    owed = sorted(
        resolved for rel in produced
        for resolved in (state_view._resolve_book_scoped(rel, book),)
        if not state_view._is_pattern(resolved) and not carries_provenance(resolved))

    assert owed, '一条都没算出来 ⇒ 这条守卫在空转，先修它自己'
    unreachable = [rel for rel in owed
                   if store._deterministic_freshness(rel) is None]
    assert unreachable == [], (
        f'这些产物没有 `_provenance`，又没接进 `_deterministic_freshness`：'
        f'{unreachable}。它们在完成度里会恒 unknown/no_provenance')


def test_stage5_draft_freshness_reuses_the_canonical_byte_renderer(
        monkeypatch):
    """draft 新鲜度按生产同一 renderer 的 UTF-8 字节回答，章或投影任一变更都 stale。"""
    from novel.consumption import render_stage5_draft
    from novel.stages.stage5 import measure as stage5_measure

    base = _book()
    book = base / '书名_123'
    (book / 'stage5').mkdir()
    by_ordinal = {
        1: {
            'output_ordinal': 1,
            'from_source_ordinals': [1],
            'chars': 4,
            'overlap_excl_verbatim': 0.0,
            'text': '第一章。',
        },
    }
    expected = render_stage5_draft([1], by_ordinal).encode('utf-8')
    (book / 'stage5' / 'draft.md').write_bytes(expected)
    store = ArtifactStore(_ctx(base, paid=False))
    context = type('Context', (), {
        'chapters': [type('Chapter', (), {'output_ordinal': 1})()],
    })()
    monkeypatch.setattr(store, 'read_own', lambda *_args, **_kwargs: (context, {}))
    monkeypatch.setattr(
        stage5_measure, 'load_drafts',
        lambda *_args, **_kwargs: (by_ordinal, [], {}),
    )

    assert store.freshness('stage5/draft.md').state == 'fresh'

    by_ordinal[1]['text'] = '正文已经变化。'
    changed = store.freshness('stage5/draft.md')
    assert (changed.state, changed.reason) == ('stale', 'file_changed')

    by_ordinal[1]['text'] = '第一章。'
    (book / 'stage5' / 'draft.md').write_bytes(expected.replace(b'\n', b'\r\n'))
    assert store.freshness('stage5/draft.md').state == 'stale', \
        'CRLF 也是字节漂移，不能被文本模式归一后伪装成 fresh'


# ─────────────────────────────────────────────────────────────────────────────
# 四之二、stage0 源身份那条边：**记录的位置优先，base 重锚只是回落**
# ─────────────────────────────────────────────────────────────────────────────

_SOURCE_BYTES = '开篇。\n\n1\n第一章。\n\n2\n第二章。\n'.encode('utf-8')


def _stage0_book(recorded_source: Path,
                 base: Path | None = None) -> tuple[Path, ArtifactStore]:
    """一本只有 stage0 的书，报告里的 `source_ref` 按 `recorded_source` 的形状写。

    ⚠ 产地那一刻的判定走**唯一落点** `source_ref_for`，不在这里手拼分支——
    手拼一份就是「上传路径长什么样」的第二个定义，而它必然与产地漂开。
    `base` 可以给现成的那个——「源本来就在当前 base 底下」的情形要靠它才造得出来。
    """
    from novel.core.ingest import decode_source_bytes
    from novel.core.source import source_ref_for

    base = _book(with_clean=False, root=base)
    book = base / '书名_123'
    clean_bytes = _SOURCE_BYTES
    (book / 'stage0').mkdir()
    (book / 'stage0' / 'clean_text.txt').write_bytes(clean_bytes)
    _write_payload(book, STAGE0_REMOVAL_REPORT, {
        'source_ref': source_ref_for(recorded_source).model_dump(mode='json'),
        'normalized_source_sha1': decode_source_bytes(_SOURCE_BYTES).normalized_source_sha1,
        'clean_sha1': prov.content_sha1(clean_bytes),
    })
    return base, ArtifactStore(_ctx(base, paid=False))


def _source_edge(store: ArtifactStore):
    """源那一条边（另一条是 `clean_text`），按 key 认。"""
    edges = [e for e in store.freshness(STAGE0_REMOVAL_REPORT).edges
             if e.key != 'stage0/clean_text.txt']
    assert len(edges) == 1, [e.key for e in edges]
    return edges[0]


def test_stage0_source_edge_survives_moving_the_output_root():
    """Output 根搬家：记下来的绝对路径断了，**而文件跟着 base 一起搬**⇒ 仍是 fresh。

    ⚠⚠ 这一条防的是**假阴性**，与「不许把没了的原文说成好消息」是同一条纪律的两面。
      `ghost` 的语义是「上游真的不在盘上」。界面上传的原文住在
      `<base>/_service/uploads/<内容地址>/`——根一改，记录里那条绝对路径指向旧根，
      而文件一个字节没变。此时报 ghost 会把一条真信号冲淡，还会连带让
      「不带 `--source` 复跑 stage0」、stage0 审校屏、`_require_stage0_fresh`
      三条路一起失效（那本书从此连人工编辑都提交不了）。
      实测过一次：《夫君与鲛人孰_2024515569832010055》，见 `novel/core/source.py`。

    ⚠ 边的 key 必须是**真正读到的那个位置**：ghost 要按名字点名（`R-5.10`），
      点一条其实读到了的旧路径，会把人送去找一个并不存在的问题。
    """
    old_root = Path(tempfile.mkdtemp())
    recorded, _sid = save_upload(old_root, '书名_123.txt', _SOURCE_BYTES)
    shutil.rmtree(old_root)                      # 根搬走：这条绝对路径从此不存在
    assert not recorded.is_file()

    base, store = _stage0_book(recorded)
    current, _sid = save_upload(base, '书名_123.txt', _SOURCE_BYTES)
    assert current != recorded

    edge = _source_edge(store)
    assert (edge.state, edge.reason) == ('fresh', 'match')
    assert edge.key == str(current)
    assert store.freshness(STAGE0_REMOVAL_REPORT).state == 'fresh'


def test_stage0_source_edge_refuses_a_reanchored_file_that_fails_its_content_address():
    """重锚拿到的候选**必须自证内容地址**，否则宁可继续答「不在」。

    ⚠⚠ 这是「回落永远不可能把另一份文件冒充成原文」的全部依据。上传目录名就是
      `sha256(原始字节)`（产地唯一：`novel/orchestrator/uploads.py::save_upload`），
      所以「同一个内容地址目录里躺着别的字节」只可能是被人动过手脚或搬错了东西——
      那不是「源变了」（`file_changed` 会暗示产物只是过期），是**这份候选不可信**。
    """
    old_root = Path(tempfile.mkdtemp())
    recorded, _sid = save_upload(old_root, '书名_123.txt', _SOURCE_BYTES)
    shutil.rmtree(old_root)

    base, store = _stage0_book(recorded)
    current, _sid = save_upload(base, '书名_123.txt', _SOURCE_BYTES)
    current.write_bytes(_SOURCE_BYTES + '\n三、混进来的别的字节。\n'.encode('utf-8'))

    edge = _source_edge(store)
    assert (edge.state, edge.reason) == ('unknown', 'ghost')
    # ⚠ 认不下来时点名的是**记录里那条引用**。上传那一支不记绝对路径（它跟着 base
    #   走），所以点的是「内容地址 + 文件名」——那才是产物真正说过的话。
    assert '内容地址' in edge.key and '书名_123.txt' in edge.key
    assert store.freshness(STAGE0_REMOVAL_REPORT).state == 'unknown'


def test_stage0_source_edge_still_goes_ghost_for_an_external_corpus_that_is_gone():
    """外部语料被删就是**世界真的变了**：`ghost` 是正确答案，不许"救"。

    ⚠ 契约有意接受这个代价（`docs/schema/stage0.md`：文件被移走时是「不知道」，
      不是「不匹配」），补偿是报告里的 `head_preview`/`tail_preview` 残片。
      重锚只认 `_service/uploads/<内容地址>/` 这一种形状——**多认一种就是把一条
      真信号洗成好消息**，而这正是本仓最贵的那条纪律（三态不许互相冒充）。
    """
    corpus_root = Path(tempfile.mkdtemp())
    recorded = corpus_root / 'Test_data' / '书名_123.txt'
    recorded.parent.mkdir(parents=True)
    recorded.write_bytes(_SOURCE_BYTES)

    base, store = _stage0_book(recorded)
    assert _source_edge(store).state == 'fresh'

    shutil.rmtree(corpus_root)
    # 同样的字节确实躺在当前 base 的上传目录里，**也不许拿它顶上**：
    # 记录说的是外部语料那一份，那一份没了就是没了。
    save_upload(base, '书名_123.txt', _SOURCE_BYTES)

    edge = _source_edge(store)
    assert (edge.state, edge.reason) == ('unknown', 'ghost')
    assert edge.key == str(recorded)


def test_an_external_source_replaced_in_place_is_stale_not_ghost():
    """**「源变了」与「源不在了」对人的下一步是相反的指示**，不许被抹平。

    外部语料被就地改写：路径还在、读出来是新字节 ⇒ `stale/file_changed`
    （产物过期了，重跑即可），而不是「找不着东西」。
    `removal.py::_require_stage0_fresh` 正是靠这条边发现「提交这一批的过程中
    源被换掉了」并整批回滚的。

    ⚠ 这一条 2026-08-22 从上传那一支**改到了外部语料这一支**。「就地改写」在两支上
    是两件不同的事，而 `source_ref` 让它们分得开：
      · `external_file` 就地改写 = **人真的改了原文**（最常见的情形）⇒ file_changed；
      · `managed_upload` 就地改写 = 内容寻址存储被破坏（目录名声称是
        `sha256(bytes)`，里面的字节却不是它）⇒ 那不是「源变了」，是**这份候选不可信**，
        由下面那条 content-address 用例判 ghost。报 file_changed 反而更坏：
        它暗示「重跑一次就好」，而重跑会再读一遍同一个坏掉的存储。
      此前两者被同一格 `original_path` 压平，只能靠「记录的那条路径碰巧还在不在」
      去区分——而那恰好是 Output 根搬没搬家的偶然。
    ⚠ 两种状态**都不是 fresh**，`_require_stage0_fresh` 两种都照拦——
      改变的只是给人的那句话，不是闸门的松紧。
    """
    corpus_root = Path(tempfile.mkdtemp())
    recorded = corpus_root / 'Test_data' / '书名_123.txt'
    recorded.parent.mkdir(parents=True)
    recorded.write_bytes(_SOURCE_BYTES)

    base, store = _stage0_book(recorded)
    assert _source_edge(store).state == 'fresh', '源就在记录的位置上'

    recorded.write_bytes(_SOURCE_BYTES + ('第三章。' + '\n').encode('utf-8'))
    edge = _source_edge(store)
    assert (edge.state, edge.reason) == ('stale', 'file_changed'),         '源被就地改写必须报 stale/file_changed，不是 ghost'
    assert edge.key == str(recorded)


def test_byte_form_artifacts_are_lf_only_and_have_no_bom():
    """⚠ 三份字节形态产物（`clean_text.txt` / `draft.md` / 成书）盘上**全是 LF-only**，
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


def _byte_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob('*') if path.is_file()
    }


def test_write_invalid_rolls_back_live_rounds_and_baselines_together(monkeypatch):
    """A replacement INVALID must not pre-archive its predecessor on IO failure."""
    base = _book()
    ctx = _ctx(base)
    store = ArtifactStore(ctx)
    store.read(Loose, UPSTREAM)
    store.write_invalid(
        STYLE_REL, _style(ctx, habit='第一份问题稿'), gates_skipped=['name_leak'])
    stage = store.path(STYLE_REL).parent
    before = _byte_tree(stage)

    def fail_baseline(_self, _destination, _data):
        raise OSError('模拟新基线写入失败')

    monkeypatch.setattr(ArtifactStore, '_write_immutable', fail_baseline)
    with pytest.raises(OSError, match='基线写入失败'):
        store.write_invalid(
            STYLE_REL, _style(ctx, habit='第二份问题稿'), gates_skipped=['name_leak'])

    assert _byte_tree(stage) == before


def test_write_round_rolls_back_formal_invalid_and_round_history(monkeypatch):
    """Post-write freshness failure restores the whole pre-archive byte set."""
    base = _book()
    ctx = _ctx(base)
    store = ArtifactStore(ctx)
    store.read(Loose, UPSTREAM)
    store.write(STYLE_REL, _style(ctx, habit='原正式稿'))
    store.write_invalid(
        STYLE_REL, _style(ctx, habit='原问题稿'), gates_skipped=['name_leak'])
    stage = store.path(STYLE_REL).parent
    before = _byte_tree(stage)
    original = ArtifactStore._assert_self_fresh

    def fail_new_round(self, rel, artifact):
        if rel == STYLE_REL:
            raise ProvenanceRefused('模拟后置 freshness 失败')
        return original(self, rel, artifact)

    monkeypatch.setattr(ArtifactStore, '_assert_self_fresh', fail_new_round)
    with pytest.raises(ProvenanceRefused, match='freshness 失败'):
        store.write_round(STYLE_REL, _style(ctx, habit='新正式稿'))

    assert _byte_tree(stage) == before


def test_history_transaction_restores_a_late_bound_delivery_retirement():
    """Content-addressed byte archives must join the caller's transaction journal."""
    base = _book()
    store = ArtifactStore(_ctx(base))
    live = store.path('stage7/book.txt')
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_bytes('旧交付物\n'.encode('utf-8'))
    before = _byte_tree(live.parent)
    digest = prov.content_sha1(live.read_bytes())

    with pytest.raises(RuntimeError, match='companion map failed'):
        with store.history_transaction('stage7/book.txt'):
            store.retire_bytes(
                'stage7/book.txt', f'stage7/_rounds/book.{digest}.txt')
            raise RuntimeError('companion map failed')

    assert _byte_tree(live.parent) == before


def test_the_official_write_path_refuses_an_invalid_name():
    ctx = _ctx(_book())
    with pytest.raises(PathRefused, match='write_invalid'):
        ArtifactStore(ctx).write('stage3/style_grounding.INVALID.json', _style(ctx))


def test_a_new_official_draft_retires_the_old_invalid_sibling():
    """⭐ **回归**：合格的新稿落盘 ⇒ 同名 `.INVALID` 进归档，不留在盘上。

    ⚠⚠ 留下来的后果不是「多一个文件」，是**四个只看兄弟文件在不在的消费方一起说假话**：
    语料库把一本已经出书的书报成 `broken` · 续跑判定去**重跑并重新付费**一个
    已经有新鲜正式稿的节点（实测发生过，stage6.hook 被多发了一次）·
    人定稿盖不上审批章 · 下游整条链被 `inbox:invalid` 挡住。
    ⚠ **归档不是删除**（`Q-8`）：那份稿花过钱，而且可能还被别人的 `inputs` 指着。
    """
    base = _book()
    ctx = _ctx(base)
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    inv = st.write_invalid(STYLE_REL, _style(ctx, habit='被拦下的那一版'),
                           gates_skipped=['name_leak'])
    assert inv.is_file()

    good = st.write(STYLE_REL, _style(ctx, habit='修好之后的那一版'))

    assert good.is_file(), '正式稿要落盘'
    assert not inv.exists(), '.INVALID 兄弟必须让位——留着它，四个消费方一起说假话'
    archived = good.parent / paths.ROUNDS_DIRNAME
    kept = sorted(p.name for p in archived.iterdir())
    assert kept and all('INVALID' in name for name in kept), \
        f'被拦下的那一份要留在 _rounds/ 里，不是删掉：{kept}'


def test_a_rejected_official_write_does_not_retire_the_invalid_sibling(monkeypatch):
    """新内容连完整预校验都过不了时，旧问题稿必须仍留在原位。"""
    base = _book()
    ctx = _ctx(base)
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    inv = st.write_invalid(
        STYLE_REL, _style(ctx, habit='仍待修的问题稿'), gates_skipped=['name_leak'])
    before = inv.read_bytes()

    def boom(*_args, **_kwargs):
        raise ProvenanceRefused('故意让新正式稿预校验失败')

    monkeypatch.setattr(ArtifactStore, '_verify_round_trip', staticmethod(boom))
    with pytest.raises(ProvenanceRefused, match='预校验失败'):
        st.write(STYLE_REL, _style(ctx, habit='结构坏掉的新稿'))

    assert inv.read_bytes() == before
    assert not (inv.parent / paths.ROUNDS_DIRNAME).exists()


def test_an_atomic_replace_failure_restores_the_just_retired_invalid(monkeypatch):
    base = _book()
    ctx = _ctx(base)
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    inv = st.write_invalid(
        STYLE_REL, _style(ctx, habit='仍待修的问题稿'), gates_skipped=['name_leak'])
    before = inv.read_bytes()
    original = ArtifactStore._replace_atomically

    def fail_official(path, data):
        if path == st.path(STYLE_REL):
            raise OSError('模拟正式稿原子替换失败')
        return original(path, data)

    monkeypatch.setattr(ArtifactStore, '_replace_atomically', staticmethod(fail_official))
    with pytest.raises(OSError, match='原子替换失败'):
        st.write(STYLE_REL, _style(ctx, habit='已预校验的新稿'))

    assert inv.read_bytes() == before
    rounds = inv.parent / paths.ROUNDS_DIRNAME
    assert not rounds.exists() or not any(rounds.iterdir())


def test_retiring_the_invalid_sibling_is_a_no_op_when_there_is_none():
    """没有问题稿时 `None` 表示「本来就没有」，不是「归档失败」。"""
    base = _book()
    ctx = _ctx(base)
    st = ArtifactStore(ctx)
    st.read(Loose, UPSTREAM)
    assert st.retire_invalid(STYLE_REL) is None
    st.write(STYLE_REL, _style(ctx))
    assert not (st.path(STYLE_REL).parent / paths.ROUNDS_DIRNAME).exists(), \
        '没有问题稿就不该凭空造一个归档目录'


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
    out = ArtifactStore(ctx2).promote_invalid(
        STYLE_REL, StyleGrounding, **REVALIDATION)
    now = _prov_on_disk(out)

    assert now['prompts'] == was['prompts'], '盖上了当前的 SYS 指纹 —— 这就是伪造'
    assert now['model'] == was['model'] and now['inputs'] == was['inputs']
    assert now['revalidated'] is True
    assert now['revalidation_audit']['actor'] == REVALIDATION['actor']
    assert now['revalidation_audit']['raw_changed'] is False
    assert now['revalidation_audit']['content_changed'] is False
    assert now['revalidation_audit']['edited_by_human'] is False
    assert now['revalidation_audit']['changed_json_pointers'] == []
    assert 'origin' not in now, 'origin=human 专属 --apply-edit 新一轮；INVALID 改动由 audit 留痕'
    assert 'gates_skipped' not in now, '提升上来的是正式产物，不该再挂缺料记录'
    assert now['code'] == ctx2.code, 'code 必须记本次重判真正使用的代码版本'

    assert not inv.exists()
    archived = sorted(p.name for p in (base / '书名_123' / 'stage3' / '_rounds').iterdir())
    assert archived == ['style_grounding.INVALID.r1.json'], \
        '⚠ 原型归档叫 `*.INVALID.superseded.json`（**没有编号**），第二次会静默覆盖第一次'


def test_revalidation_records_immutable_baseline_hashes_and_json_pointer_diff():
    base = _book()
    _ctx_, inv = _write_invalid(base, [], habit='机器原稿')
    provenance = _prov_on_disk(inv)
    locator = provenance['invalid_baseline_sha256']
    baselines = list((inv.parent / '_revalidation').glob(f'*.{locator}.snapshot'))
    assert len(baselines) == 1
    baseline = baselines[0]
    baseline_before = baseline.read_bytes()
    assert baseline_before == inv.read_bytes(), '基线不是 INVALID 创建时的完整原始字节'

    edited = json.loads(inv.read_text(encoding='utf-8'))
    edited['surface_habits'][0] = '人工修正'
    inv.write_text(json.dumps(edited, ensure_ascii=False, indent=4) + '\n', encoding='utf-8')

    store = ArtifactStore(_ctx(base, paid=False))
    audit = store.revalidation_audit(
        STYLE_REL, StyleGrounding, **REVALIDATION)
    assert audit.raw_changed is True and audit.content_changed is True
    assert audit.edited_by_human is True
    assert audit.changed_json_pointers == ('/surface_habits/0',)
    assert audit.baseline_raw_sha256 != audit.candidate_raw_sha256
    assert audit.baseline_canonical_sha256 != audit.candidate_canonical_sha256

    out = store.promote_invalid(STYLE_REL, StyleGrounding, **REVALIDATION)
    disk_audit = _prov_on_disk(out)['revalidation_audit']
    assert disk_audit == audit.model_dump(mode='json')
    assert baseline.read_bytes() == baseline_before, '提升过程改写了不可变基线'


def test_revalidation_distinguishes_raw_only_edits_from_logical_edits():
    base = _book()
    _ctx_, inv = _write_invalid(base, [])
    payload = json.loads(inv.read_text(encoding='utf-8'))
    payload['_provenance']['code'] = 'manually-touched-provenance-only'
    inv.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                   encoding='utf-8')

    audit = ArtifactStore(_ctx(base, paid=False)).revalidation_audit(
        STYLE_REL, StyleGrounding, **REVALIDATION)
    assert audit.raw_changed is True
    assert audit.content_changed is False
    assert audit.edited_by_human is False
    assert audit.changed_json_pointers == ()


def test_promotion_override_rebuilds_derived_fields_but_cannot_replace_live_audit_or_source():
    base = _book()
    _ctx_, inv = _write_invalid(base, [], habit='机器原稿')
    baseline_source = _prov_on_disk(inv)
    current = json.loads(inv.read_text(encoding='utf-8'))
    current['surface_habits'][0] = '人工提交'
    inv.write_text(json.dumps(current, ensure_ascii=False, indent=2) + '\n',
                   encoding='utf-8')
    live = StyleGrounding.model_validate(decode_artifact(STYLE_REL, current).payload)
    fake_source = live.provenance.model_copy(update={'code': 'fake-override-code', 'inputs': {}})
    rebuilt = live.model_copy(update={
        'surface_habits': ['确定性派生字段已重建'],
        'provenance': fake_source,
    })

    store = ArtifactStore(_ctx(base, paid=False))
    live_audit = store.revalidation_audit(
        STYLE_REL, StyleGrounding, **REVALIDATION)
    out = store.promote_invalid(
        STYLE_REL, StyleGrounding, artifact_override=rebuilt, **REVALIDATION)
    disk = json.loads(out.read_text(encoding='utf-8'))
    source = disk['_provenance']

    assert disk['surface_habits'] == ['确定性派生字段已重建']
    assert source['revalidation_audit'] == live_audit.model_dump(mode='json')
    assert source['revalidation_audit']['changed_json_pointers'] == ['/surface_habits/0']
    assert source['inputs'] == baseline_source['inputs'], 'override 自带的假来源账被信任了'
    assert source['code'] != 'fake-override-code'


@pytest.mark.parametrize(('actor', 'reason'), [('', '理由'), ('人', '  ')])
def test_revalidation_rejects_blank_human_credentials_without_touching_files(actor, reason):
    base = _book()
    _ctx_, inv = _write_invalid(base, [])
    before = inv.read_bytes()
    store = ArtifactStore(_ctx(base, paid=False))
    with pytest.raises(ApprovalRequired, match='actor.*reason'):
        store.promote_invalid(
            STYLE_REL, StyleGrounding, actor=actor, reason=reason)
    assert inv.read_bytes() == before
    assert not store.exists(STYLE_REL)


def test_revalidation_refuses_a_tampered_immutable_baseline_before_writing():
    base = _book()
    _ctx_, inv = _write_invalid(base, [])
    locator = _prov_on_disk(inv)['invalid_baseline_sha256']
    baseline = next((inv.parent / '_revalidation').glob(f'*.{locator}.snapshot'))
    damaged = json.loads(baseline.read_text(encoding='utf-8'))
    damaged['surface_habits'][0] = '基线被改'
    baseline.write_text(json.dumps(damaged, ensure_ascii=False, indent=2) + '\n',
                        encoding='utf-8')

    store = ArtifactStore(_ctx(base, paid=False))
    with pytest.raises(ProvenanceRefused, match='内容校验失败'):
        store.promote_invalid(STYLE_REL, StyleGrounding, **REVALIDATION)
    assert inv.is_file() and not store.exists(STYLE_REL)


def test_an_invalid_without_a_creation_snapshot_fails_closed():
    """被测的是**缺定位键就不许提升**，与版本号无关。

    ⚠ 这条曾顺手把 `_schema_version` 改成 2 来扮演「旧文件」。版本归零后那一笔会让它
    先死在版本闸上（拒读），根本走不到 `promote_invalid` 的定位键判断——**用例还是红的，
    但查的已经不是它要查的那件事**。⇒ 去掉那笔道具，只留真正被测的条件。
    """
    base = _book()
    _ctx_, inv = _write_invalid(base, [])
    legacy = json.loads(inv.read_text(encoding='utf-8'))
    legacy['_provenance'].pop('invalid_baseline_sha256')
    inv.write_text(
        json.dumps(legacy, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='',
    )
    before = inv.read_bytes()

    store = ArtifactStore(_ctx(base, paid=False))
    with pytest.raises(ProvenanceRefused, match='没有 write_invalid 记录的不可变基线定位键'):
        store.promote_invalid(STYLE_REL, StyleGrounding, **REVALIDATION)

    assert inv.read_bytes() == before
    assert not store.exists(STYLE_REL)


def test_first_write_invalid_creates_a_nested_snapshot_near_windows_max_path():
    root = Path(tempfile.mkdtemp())
    locator = '0' * 64
    empty_old = (root / '_123' / 'stage3' / '_revalidation' /
                 f'style_grounding.INVALID.{locator}.snapshot')
    # Reproduce the acceptance budget: the old target-derived temporary name
    # and even the long legacy target reach MAX_PATH, while every ordinary
    # input/output directory remains comfortably shorter.
    title = '长' * max(8, 264 - len(str(empty_old)))
    base = _book(title=title, root=root)
    ctx = _ctx(base)
    store = ArtifactStore(ctx)
    store.read(Loose, UPSTREAM)
    revalidation = store.path(STYLE_REL).parent / '_revalidation'
    assert not revalidation.exists()

    inv = store.write_invalid(STYLE_REL, _style(ctx), gates_skipped=[])
    locator = _prov_on_disk(inv)['invalid_baseline_sha256']
    snapshots = list(revalidation.glob(f'*.{locator}.snapshot'))
    assert len(snapshots) == 1
    assert snapshots[0].name == f'b.{locator}.snapshot'
    assert snapshots[0].read_bytes() == inv.read_bytes()

    legacy = revalidation / f'style_grounding.INVALID.{locator}.snapshot'
    legacy_tmp = legacy.with_name(f'{legacy.name}.{os.getpid()}.tmp')
    assert len(str(legacy)) >= 260, '夹具没有复现真实验收的 Windows 路径预算'
    assert len(str(snapshots[0])) + 20 < len(str(legacy_tmp))


def test_failed_first_snapshot_write_leaves_no_partial_or_live_invalid(monkeypatch):
    base = _book()
    ctx = _ctx(base)
    store = ArtifactStore(ctx)
    store.read(Loose, UPSTREAM)
    invalid = store.path(paths.invalid_of(STYLE_REL))
    revalidation = invalid.parent / '_revalidation'
    original = os.replace

    def fail_snapshot(source, destination):
        if '_revalidation' in os.fspath(destination):
            raise OSError('模拟不可变快照原子替换失败')
        return original(source, destination)

    monkeypatch.setattr(os, 'replace', fail_snapshot)
    with pytest.raises(OSError, match='快照原子替换失败'):
        store.write_invalid(STYLE_REL, _style(ctx), gates_skipped=[])

    assert not invalid.exists(), '快照没落成却留下了可提升的活动 INVALID'
    assert not revalidation.exists() or not any(revalidation.iterdir()), \
        '失败路径留下了临时文件或半份审计快照'


# ⚠ 这里曾有 `test_revalidation_can_read_the_legacy_long_snapshot_name`：它自己造一份
#   长名快照，再证明读侧回落读得到。那条回落删除时的实测是**盘上 8 份快照 8/8 都是短名**
#   ⇒ 它是这条兼容分支的唯一执行者，一个靠自己的测试活着的兼容层。
#   ⚠ 那条用例顺带还是一个 >260 字符路径的压力源；`MAX_PATH` 那一面由
#   `test_first_write_invalid_creates_a_nested_snapshot_near_windows_max_path` 独立守着，
#   删它没有让长路径失去守卫。


def test_write_invalid_never_overwrites_an_existing_baseline_snapshot():
    base = _book()
    ctx, inv = _write_invalid(base, [])
    store = ArtifactStore(ctx)
    artifact = store.read_own(StyleGrounding, paths.invalid_of(STYLE_REL))[0]
    locator = _prov_on_disk(inv)['invalid_baseline_sha256']
    baseline = next((inv.parent / '_revalidation').glob(f'*.{locator}.snapshot'))
    baseline.write_bytes(b'tampered immutable history')
    live_before = inv.read_bytes()

    with pytest.raises(ProvenanceRefused, match='拒绝覆盖审计历史'):
        store.write_invalid(STYLE_REL, artifact, gates_skipped=[])

    assert baseline.read_bytes() == b'tampered immutable history'
    assert inv.read_bytes() == live_before


def test_a_second_promotion_lands_in_the_next_round_not_on_top_of_the_first():
    """第二次被拦下的稿归档到 `r2`，**不覆盖 `r1`**；同时**上一轮正式稿也进 `_rounds`**。

    ⚠ 这一条挡的是原型那个 `*.INVALID.superseded.json`（无编号）：
    第二次会静默覆盖第一次。轮次由 `seen_rounds` 扫出来，缺号也不会退回去。

    ⚠ 两份 `.INVALID` **必须内容不同**（`habit`），否则第二次提升渲染出的字节
    与盘上正式稿逐字节相同、归档那一步按设计跳过，这条断言就变成靠巧合绿的。
    """
    base = _book()
    _write_invalid(base, [], habit='第一版')
    p = ArtifactStore(_ctx(base, paid=False)).promote_invalid(
        STYLE_REL, StyleGrounding, **REVALIDATION)
    first = p.read_bytes()
    _write_invalid(base, ['name_leak'], habit='第二版')
    ArtifactStore(_ctx(base, paid=False)).promote_invalid(
        STYLE_REL, StyleGrounding, **REVALIDATION)

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
    out = ArtifactStore(_ctx(base, paid=False)).promote_invalid(
        STYLE_REL, StyleGrounding, **REVALIDATION)

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
    ArtifactStore(_ctx(base, paid=False)).promote_invalid(
        STYLE_REL, StyleGrounding, **REVALIDATION)

    rounds = base / '书名_123' / 'stage3' / '_rounds'
    # 模拟「正式稿已落、`.INVALID` 还没挪走」那半个刻意选的窗口
    inv = base / '书名_123' / 'stage3' / 'style_grounding.INVALID.json'
    (rounds / 'style_grounding.INVALID.r1.json').rename(inv)
    ArtifactStore(_ctx(base, paid=False)).promote_invalid(
        STYLE_REL, StyleGrounding, **REVALIDATION)

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

    out = ArtifactStore(_ctx(base, paid=False)).promote_invalid(
        STYLE_REL, FreeArtifact, **REVALIDATION)
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
        st.promote_invalid(STYLE_REL, StyleGrounding, **REVALIDATION)
    assert inv.exists(), '`.INVALID` 被挪走了'
    assert not st.exists(STYLE_REL), '正式稿落下去了，而调用方拿到的是一个异常'
    assert sorted(q.name for q in rounds.iterdir()) == ['style_grounding.INVALID.r1.json'], \
        '归档目录变了——有人把「归档旧正式稿」挪到了占位检查之前'


def test_promotion_without_an_invalid_is_a_terminal_state():
    with pytest.raises(UpstreamMissing):
        ArtifactStore(_ctx(_book(), paid=False)).promote_invalid(
            STYLE_REL, StyleGrounding, **REVALIDATION)


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
    root_clean_sha1 = prov.content_sha1(
        (base / '书名_123' / 'stage0' / 'clean_text.txt').read_bytes())
    malformed = {
        '这份产物不合当前业务契约': True,
        '_provenance': {
            'root_clean_sha1': root_clean_sha1,
            'inputs': {UPSTREAM: {'file': 'ffffffffff', 'consumed': None}},
        },
    }
    (base / '书名_123' / 'stage3' / 'style_grounding.json').write_text(
        json.dumps(encode_artifact(STYLE_REL, malformed), ensure_ascii=False),
        encoding='utf-8',
    )
    r = st.freshness(STYLE_REL)
    assert (r.state, r.reason) == ('stale', 'file_changed')
    with pytest.raises(ArtifactUnreadable):
        st.read(StyleGrounding, STYLE_REL)          # ← 同一份，过模型就是读不出来


def test_a_bad_key_in_current_provenance_does_not_blow_up_the_report():
    """当前产物里的来源键仍可能损坏。**别让一条坏键把整份报告干掉**——
    「有一条边指向已删文件」不该升级成「报告出不来」。"""
    base = _book()
    st = ArtifactStore(_ctx(base))
    (base / '书名_123' / 'stage3').mkdir()
    root_clean_sha1 = prov.content_sha1(
        (base / '书名_123' / 'stage0' / 'clean_text.txt').read_bytes())
    malformed = {
        '_provenance': {
            'root_clean_sha1': root_clean_sha1,
            'inputs': {'../../别的书/x.json': {
                'file': 'ffffffffff', 'consumed': None,
            }},
        },
    }
    (base / '书名_123' / 'stage3' / 'style_grounding.json').write_text(
        json.dumps(encode_artifact(STYLE_REL, malformed)), encoding='utf-8')
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
def _NO_CORPUS(test):
    test = pytest.mark.corpus(test)
    return pytest.mark.skipif(
        not _CORPUS,
        reason=(f'未设置当前产物语料目录 `{corpus.CORPUS_ENV}`'
                f'——**这一条现在没在查，不是「查过没事」**'))(test)


@pytest.fixture
def only_the_unknown_half_of_the_freshness_postcondition_is_waived(monkeypatch):
    """⚠⚠ **这不是把写盘闸关掉，是把那一条后置条件收窄成这个空沙箱答得出的那一半。**

    `_assert_self_fresh`（`novel/store.py:1360-1372`）要 `state == 'fresh'` 才放行，
    而往返测试刻意只搬全链根（形状往返不需要上游）⇒ 每条 `inputs` 边都是 `ghost`
    ⇒ 恒 `unknown`。而这道闸真正防的那件事——「写盘方 ctx 与真正产地不一致」——
    在 `novel/prov.py` 的口径里**恒落在 `stale`**（`consumed_changed` / `file_changed`）：
    它是 `be3e6da` 为 `stage4.plan` 借自己的 ctx 去写 `stage4/writer_context.json`
    那个 P0 加的，现场是一份刚组好的产物当场 `stale/consumed_changed`。
    ⇒ 这里只放行 `unknown` 家族，`stale` **原样抛、原样回滚、原样用生产的措辞**。

    ⚠⚠ **`unknown` 那一半在生产里仍然是被拒的，别照抄这段到别处。**
    它抓得住 `consumed_undeclared`（漏登记投影）——`novel/consumption.py:352-356`
    那条注释就是它抓到过一次的现场（`STAGE2_GLOBAL_EDIT` 那行投影是被它逼出来的），
    而读侧 `_refuse_stale_upstream` 对 `unknown` 是放行的，写侧一放宽就没人守了。
    ⇒ 豁免的作用域上界就是本文件；它由
    `test_the_waiver_switches_off_nothing_but_the_unknown_half` 钉着。

    ⚠ **先调生产那一个落点再判**，不复制那条「没有 `_provenance` 且不在
    `SELF_FRESH_DETERMINISTIC` 里就早退」的判断。复制一份的代价是具体的：
    `stage0/removal_report.json` 走 `_stage0_freshness()`，它会去 resolve 真实源文件
    （reject-new-source 那条路会合法地把新 source 显示为 stale），而生产**根本不检查它**
    ——自己算就会造出一条生产没有的红。
    """
    original = ArtifactStore._assert_self_fresh

    def narrowed(self, rel, artifact):
        try:
            original(self, rel, artifact)
        except ProvenanceRefused:
            if self.freshness(rel).state == 'stale':
                raise

    monkeypatch.setattr(ArtifactStore, '_assert_self_fresh', narrowed)


def test_the_waiver_lets_unknown_through_and_still_refuses_stale(
        only_the_unknown_half_of_the_freshness_postcondition_is_waived):
    """⭐ **豁免的两侧各钉一次 —— 这条是上面那个 fixture 的守卫本体。**

    ⚠⚠ 旁边那几条（`test_global_review_source_drift_refuses_write_and_restores_previous_bytes`
    等）守的是**没有豁免时**整条后置条件；它们不吃这个 fixture，所以
    **把 `narrowed` 改宽成 `lambda *_: None` 它们一条都不会红**，而那样 127 条往返会在
    写盘口后置条件全线放行的情况下照样全绿。⇒ 这条专门吃着 fixture 判两侧：

    ① `unknown`（`ghost`：grounding 不在盘上）⇒ **放行**，这是豁免的全部作用面；
    ② `stale`（`file_changed`：grounding 变了）⇒ **照旧拒，且正式路径逐字节回滚**。

    走 `stage2/global_review.json` 是因为它在 `SELF_FRESH_DETERMINISTIC` 里
    （无 `_provenance` 字段也照查），三态由 `_global_review_freshness` 现算
    ——不用手搓 provenance 就能把两侧都造出来。
    """
    grounding = {'entities': {'characters': {}},
                 'canon': {'world_rules': [], 'secrets': []}}

    # ① unknown/ghost：grounding 不在盘上 ⇒ 算不出来 ⇒ 豁免放行
    ghost_base = _book()
    ghost_store = ArtifactStore(_ctx(ghost_base, node='stage2.global', paid=False))
    landed = ghost_store.write(STAGE2_GLOBAL_REVIEW, build_review(grounding))
    assert landed.is_file(), '豁免没生效——本条测试自己的前提就不成立了'
    assert ghost_store.freshness(STAGE2_GLOBAL_REVIEW).state == 'unknown'

    # ② stale/file_changed：写完之后动上游 ⇒ 必须照旧拒，且回滚
    base = _book()
    _write_payload(base / '书名_123', STAGE2_GLOBAL_GROUNDING, grounding)
    store = ArtifactStore(_ctx(base, node='stage2.global', paid=False))
    review = build_review(grounding)
    official = store.write(STAGE2_GLOBAL_REVIEW, review)
    before = official.read_bytes()

    changed = copy.deepcopy(grounding)
    changed['canon']['world_rules'].append({'rule_id': 'r1', 'kind': 'hard'})
    _write_payload(base / '书名_123', STAGE2_GLOBAL_GROUNDING, changed)
    assert store.freshness(STAGE2_GLOBAL_REVIEW).state == 'stale'

    with pytest.raises(ProvenanceRefused, match='freshness 后置条件'):
        store.write(STAGE2_GLOBAL_REVIEW, review)
    assert official.read_bytes() == before, \
        '豁免把回滚也一起吞了——`stale` 那一支必须原样走完生产的事务'


@_NO_CORPUS
@pytest.mark.parametrize('a', _CORPUS, ids=[f'{x.source_id[-4:]}/{x.rel}' for x in _CORPUS])
def test_every_corpus_artifact_survives_a_write_read_round_trip(
        a, only_the_unknown_half_of_the_freshness_postcondition_is_waived):
    """模型 → 盘上 → 模型，验证显式配置的每份当前产物。

    ⚠ A3 的语料体检做的是**单向**的（盘上 → 模型）。单向永远发现不了这一类：
    `Provenance.to_disk()` 曾经用 `model_dump(exclude_none=True)`，
    而 `exclude_none` 是**全局**的 ⇒ 它连着剥掉两处**必填但可为 `null`** 的键
    （`inputs[].consumed` 与 `usage.*_tokens`）⇒ **落下去的产物读不回来**。
    加载体检 92 份全绿，而第一份真落盘就会炸。

    当前语料只读；往返副本写进 `mkdtemp()`，跑完即删。

    ⚠ **落点是 `a.formal_rel` 不是 `a.rel`**：语料表里有 `_rounds/<stem>.rN.json`
    与 `<stem>.INVALID.json` 两类**旁路历史**，它们分别由 `archive_round` 与
    `write_invalid` 产出，`store.write` 见到 `.INVALID` 会当场 `PathRefused`
    （那道拒绝是对的，见 `test_the_official_write_path_refuses_an_invalid_name`）。
    本条测的是**形状**：这个模型落得下去、读得回来。拿归档路径去写，
    测到的只是「一条没人会走的入口拒收了」。
    """
    try:
        obj = a.model.model_validate(corpus.load_current(a))
    except ArtifactVersionError as error:
        pytest.fail(f'当前产物语料中混入了非当前版本：{error}')

    # ⚠ 写盘口那道闸要比对**全链根**（`_refuse_bad_provenance` → `root_clean_sha1`）：
    #   一个空目录里落任何带来源账的产物都会当场 `ProvenanceRefused`，
    #   而本条要测的是**形状往返**，不是那道闸。⇒ 走 `sandbox.Sandbox`，它无条件带全链根。
    #   ⚠⚠ **别把这段拷贝回来自己 mkdtemp**：`Sandbox` 的 docstring 记着这正是它存在的理由
    #   （「两处手抄了拷贝代码，其余七个文件漏掉，17 条红在同一句话上」），
    #   而这条测试曾经就是那两处手抄之一——它当时还带一个 `if root.is_file():` 的静默分支，
    #   于是「这本书缺根」会伪装成一条形状回归。`Sandbox` 那里是会点名的 assert。
    box = sandbox.Sandbox(a.book_dir, ())
    try:
        ctx = RunContext.open(box.root, box.sid, 'stage3.style',
                              paid=True, write_ledger=False)
        st = ArtifactStore(ctx)

        p = st.write(a.formal_rel, obj)
        assert b'\r' not in p.read_bytes()
        written = json.loads(p.read_text(encoding='utf-8'))
        logical = decode_artifact(a.formal_rel, written).payload
        assert a.model.model_validate(logical) == obj, \
            '读回来的模型与写出去的那一份不等——某个键在落盘形态里被抹掉或改了形状'
    finally:
        box.close()


@_NO_CORPUS
def test_the_round_trip_landing_is_a_writable_path_for_every_corpus_row():
    """⭐ 上一条的**前提守卫**：`formal_rel` 必须真的把两类旁路历史剥回本体。

    没有这一条，`formal_rel` 少剥一种形态（例如 `.INVALID.rN.json` 只剥了 `.rN`）
    就会让往返测试重新变成「一条没人会走的入口拒收了」——而那次红**看起来**
    与真正的形状回归一模一样。⇒ 落点自己也要被查一次：
    既不带 `_rounds/`，也不带 `.INVALID`，且 `store.write` 的路径闸放行。
    """
    for a in _CORPUS:
        formal = a.formal_rel
        assert '_rounds/' not in formal, f'{a.rel} 的落点仍在归档目录里：{formal}'
        assert not paths.is_invalid(formal), f'{a.rel} 的落点仍是 `.INVALID` 形态：{formal}'
        assert paths.round_no_of(formal) is None, f'{a.rel} 的落点仍带轮次号：{formal}'
        # `check_rel_deep` 是 `store.write` 自己那道路径闸；对不上会当场 `PathRefused`。
        assert paths.check_rel_deep(formal) == formal

    # ⚠ **落点的另一半前提：沙箱建得起来。** 往返走 `sandbox.Sandbox`，它无条件搬全链根；
    #   缺根时 `store.write` 会在 `_refuse_bad_provenance` 那道闸拒收，而那条红
    #   **与真正的形状回归长得一模一样**。⇒ 把「这是环境缺料」摘出来单独点名，
    #   否则一本书缺一个文件会表现成它名下几十条各自的形状红。
    for book in sorted({a.book_dir for a in _CORPUS}):
        root = corpus.corpus_root() / book / sandbox.CHAIN_ROOT
        assert root.is_file(), (
            f'{book} 缺 {sandbox.CHAIN_ROOT}——往返测的就不是形状了，'
            f'而是「写盘口拒了一份没有根的写」')
        assert book.rsplit('_', 1)[-1].isdigit(), \
            f'{book} 的目录名解析不出 sid，`Sandbox` 建不出可用的 `RunContext`'


@_NO_CORPUS
def test_the_waiver_switches_off_nothing_but_the_unknown_half(
        only_the_unknown_half_of_the_freshness_postcondition_is_waived):
    """⭐ **前提守卫③：证明上面那条豁免的边界。**

    `only_the_unknown_half_…` 读起来像「写盘闸关了」，而它只关了一条后置条件的一半。
    没有本条，往后任何一次「顺手把它扩成 `lambda *_: None`」都不会有人出声，
    而那会让 127 条往返在**写盘口全线放行**的情况下照样全绿。
    ⇒ 在**同一个豁免生效的进程里**把写盘口另外两道闸各撞一次，两条都必须照旧拒绝。

    ⚠ 这不是重复已有的守卫：`.INVALID` 路径闸与全链根比对各自都有非语料测试，
    本条测的是**「豁免的作用域」**这条性质——全仓没有第二处守它。
    """
    a = next((x for x in _CORPUS if x.formal_rel == STYLE_REL), None)
    corpus.declared_dependency(
        a is not None, needs=f'任意一本书的 {STYLE_REL}',
        produced_by='stage3.style', cost='付费')
    obj = a.model.model_validate(corpus.load_current(a))

    box = sandbox.Sandbox(a.book_dir, ())
    try:
        ctx = RunContext.open(box.root, box.sid, 'stage3.style',
                              paid=True, write_ledger=False)
        # ① `.INVALID` 路径闸（`novel/store.py:548-551`）：豁免不该让它放行。
        with pytest.raises(PathRefused):
            ArtifactStore(ctx).write(paths.invalid_of(a.formal_rel), obj)

        # ② 全链根比对（`novel/store.py:1426-1429`）：把根改一个字节，写盘口必须当场拒。
        #    ⚠ 要重开 `RunContext`——`root_clean_sha1` 是 run 级固化的，沿用旧 ctx
        #    测到的是「它记住了改之前那份」，不是这道闸。
        root = box.dir / sandbox.CHAIN_ROOT
        root.write_bytes(root.read_bytes() + '　'.encode('utf-8'))
        moved = RunContext.open(box.root, box.sid, 'stage3.style',
                                paid=True, write_ledger=False)
        with pytest.raises(ProvenanceRefused, match='拒绝混根'):
            ArtifactStore(moved).write(a.formal_rel, obj)
    finally:
        box.close()
