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
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field, RootModel

from contracts.artifacts import (
    STAGE0_CLEAN_TEXT, STAGE0_REVISIONS, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
    STAGE2_BLUEPRINT_CHARACTER_PROJECTION, STAGE2_GLOBAL_REVIEW,
    STR06_BODY_DESIGN, STR06_DELIVERY_MANIFEST, STR06_EXECUTION_PLAN,
    STR06_HOOK_CONTEXT, STR06_NAME_MAP, STR06_PRESERVE_CHECKPOINT,
    STR06_PRESENTATION_GRAPH, STR06_RECONSTRUCTION_BRIEF,
    STR06_SEMANTIC_BINDING, STR06_STAGE2_BLUEPRINTS,
    STR06_STAGE2_CHARACTERS, STR06_STAGE2_OBS_BOOK, STR06_STAGE2_OBS_NAMES,
)
from contracts.schema import (
    ArtifactVersionError, InputEdge, LlmProvenance, Usage, artifact_spec,
    decode_artifact, encode_artifact,
)
from contracts.schema.grounding import (
    PreserveKind, SemanticBinding, SourceLineage, SourceObligation,
)
from contracts.schema.naming import NameAssignment, NameMapV5, TargetName
from contracts.schema.source import (
    CharacterEntry, SurfaceWindow,
)
from novel.consumption import (
    CONSUMED_PROJECTIONS, artifact_payload,
    producer_node, project_consumed, projection_for,
)
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, ByteFormRefused, PathRefused,
    ProvenanceRefused, UpstreamMissing,
)
from novel import paths, prov, store as store_module
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, ledger_path
from novel.orchestrator.uploads import save_upload
from novel.orchestrator import state as orchestrator_state
from novel.run import RunContext
from novel.snapshot_clone import readonly_snapshot_clone
from novel.store import (
    ArtifactStore, SELF_FRESH_DETERMINISTIC, _json_pointer_diff,
)
from novel.stages.stage1.partition import partition_text
from tests import corpus, sandbox

# B5 换轨（双真源发布后）：Characters/Blueprint 的唯一 writer 是免费的
# str06.stage2.compile，其投影面是四份观察——旧的「names 写 characters」采样面
# 失去执行对象。本文件通用事务夹具改取 str06.stage7.naming 的 NameMap v5
# （付费 producer，注册消费 canonical Characters 整份载荷）——store 事务机制
# 不挑载体，但来源账后置条件要求读边有注册投影。
PLAN_REL = STR06_NAME_MAP                    # 'stage7/naming/name_map.json'
PLAN_MODEL = NameMapV5
PLAN_NODE = 'str06.stage7.naming'
UPSTREAM = STR06_STAGE2_CHARACTERS          # 'stage2/characters/characters.json'
#: 两级只读 memo 用例的 (node, rel)：三级家族路径进不了 `store.read` 的两级
#: 输入键（`paths.check_rel`，见 `PathRefused`），记账边由 `_record_upstream_edge`
#: 显式记；只有 memo 用例仍走 `store.read`，故取两级组合。
MT_REL = STAGE1_MAIN_TEXT
MT_NODE = 'str06.stage2.book'
#: names 采样对（本文件「窄投影 × 整份观察」消费面用例的载体）。
NAMES_REL = STR06_STAGE2_OBS_NAMES
NAMES_NODE = 'str06.stage2.names'
REVALIDATION = {'actor': 'test-reviewer', 'reason': '逐项复核当前判据'}


def test_readonly_snapshot_memo_never_crosses_operator_invocations(
        tmp_path: Path) -> None:
    base = _book(root=tmp_path)
    book = paths.resolve_novel_dir(base, '123')
    target = book / 'stage0' / 'clean_text.txt'

    with store_module.readonly_snapshot_memo(book):
        assert store_module._read_file_bytes(target) == b'fixture clean root\n'
        target.write_bytes(b'changed during the same sealed snapshot\n')
        assert store_module._read_file_bytes(target) == b'fixture clean root\n'

    with store_module.readonly_snapshot_memo(book):
        assert store_module._read_file_bytes(target) == b'changed during the same sealed snapshot\n'


def test_readonly_snapshot_memo_returns_independent_validated_objects(
        tmp_path: Path) -> None:
    base = _book(root=tmp_path)
    book = paths.resolve_novel_dir(base, '123')
    ctx = orchestrator_state.readonly_ctx(book, MT_NODE, 'code-a')

    with store_module.readonly_snapshot_memo(book, code='code-a'):
        store = ArtifactStore(ctx)
        first, first_payload, _raw = store._validated_read(Loose, MT_REL)
        first.root.append({'mutated': True})
        first_payload.append({'mutated': True})

        second, second_payload, _raw = store._validated_read(Loose, MT_REL)
        assert second.root == []
        assert second_payload == []


def test_readonly_snapshot_memo_reuses_only_scalar_consumed_fingerprint(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _book(root=tmp_path)
    book = paths.resolve_novel_dir(base, '123')
    calls = 0

    def project_once(node, key, data, *, related):
        nonlocal calls
        calls += 1
        assert node == MT_NODE
        assert key == MT_REL
        assert related == {}
        return True, {'chapter_count': len(data)}

    monkeypatch.setattr(store_module, 'project_consumed', project_once)
    ctx = orchestrator_state.readonly_ctx(book, MT_NODE, 'code-a')
    with store_module.readonly_snapshot_memo(book, code='code-a'):
        store = ArtifactStore(ctx)
        first = store.read(Loose, MT_REL)
        first.root.append({'mutated': True})
        second = store.read(Loose, MT_REL)
        assert second.root == []
        assert calls == 1

    # A public invocation gets a new byte/scalar memo and recomputes against its
    # own sealed surface; no consumed result crosses the invocation boundary.
    with store_module.readonly_snapshot_memo(book, code='code-a'):
        ArtifactStore(ctx).read(Loose, MT_REL)
    assert calls == 2


def test_readonly_snapshot_clone_returns_independent_objects_and_stays_local(
        tmp_path: Path) -> None:
    base = _book(root=tmp_path)
    book = paths.resolve_novel_dir(base, '123')
    ctx = orchestrator_state.readonly_ctx(book, 'str06.stage4.compile', 'code-a')
    calls = 0

    def factory() -> dict[str, list[str]]:
        nonlocal calls
        calls += 1
        return {'rows': ['sealed']}

    with store_module.readonly_snapshot_memo(book, code='code-a'):
        first = readonly_snapshot_clone(ctx, ('heavy-proof',), factory)
        first['rows'].append('mutated')
        second = readonly_snapshot_clone(ctx, ('heavy-proof',), factory)
        assert second == {'rows': ['sealed']}
        assert calls == 1

    with store_module.readonly_snapshot_memo(book, code='code-a'):
        assert readonly_snapshot_clone(
            ctx, ('heavy-proof',), factory) == {'rows': ['sealed']}
    assert calls == 2


def test_readonly_snapshot_memo_rejects_cross_book_nested_and_writable_use(
        tmp_path: Path) -> None:
    base_a = _book(sid='a', root=tmp_path / 'a')
    base_b = _book(sid='b', root=tmp_path / 'b')
    book_a = paths.resolve_novel_dir(base_a, 'a')
    book_b = paths.resolve_novel_dir(base_b, 'b')
    readonly_b = orchestrator_state.readonly_ctx(
        book_b, 'str06.stage4.compile', 'code-a')
    writable_a = _ctx(base_a, sid='a')

    with store_module.readonly_snapshot_memo(book_a, code='code-a'):
        with pytest.raises(ValueError, match='belongs to .* not '):
            ArtifactStore(readonly_b)
        with pytest.raises(ValueError, match='writable ArtifactStore'):
            ArtifactStore(writable_a)
        with pytest.raises(ValueError, match='already active'):
            with store_module.readonly_snapshot_memo(book_a, code='code-a'):
                raise AssertionError('nested memo body must not run')


class Loose(RootModel[Any]):
    """只用来测记账的最小上游载体——本文件不测 Characters/MainText 的契约。

    B5 换轨后通用夹具的上游格是 canonical Characters（dict 载荷，三级家族路径
    经显式记账边）；两级只读 memo 用例改走 `MT_REL`（stage1/main_text.json，
    `str06.stage2.book` 的窄投影）。业务 schema 另有自己的测试。
    """


# ─────────────────────────────────────────────────────────────────────────────
# 夹具：一本书 + 一次跑
# ─────────────────────────────────────────────────────────────────────────────

def _book(sid: str = '123', title: str = '书名',
          payload: bytes = b'{}',
          *, with_clean: bool = True, root: Path | None = None) -> Path:
    base = root if root is not None else Path(tempfile.mkdtemp())
    from novel.work_units import initialize_workflow
    initialize_workflow(base)
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
    upstream_path = book / UPSTREAM
    upstream_path.parent.mkdir(parents=True, exist_ok=True)
    upstream_path.write_bytes(disk)
    # 两级 memo 用例的载体（`MT_REL`）：一份最小空章数组。
    mt_path = book / MT_REL
    mt_path.write_bytes(json.dumps(
        encode_artifact(MT_REL, []), ensure_ascii=False).encode('utf-8'))
    return base

def _record_upstream_edge(ctx: RunContext, *, rel: str = UPSTREAM) -> None:
    """按 `PLAN_NODE` 的注册投影给 `rel` 显式记一条消费边（`store.read` 的等价物）。

    三级家族路径进不了 `ArtifactStore.read` 的两级输入键（`paths.check_rel`，
    见 `PathRefused` 的三级塌键说明）；现役 Stage4+ 产地的上游全是三级 canonical，
    与 B5 前用 `store.read` 记账的形状等价：file 指纹＝盘上整份字节，consumed＝
    注册投影指纹（`store._projection_payload` 在 freshness 侧重算时用同一解码）。
    """
    path = ctx.novel_dir / rel
    raw = path.read_bytes()
    logical = decode_artifact(rel, json.loads(raw.decode('utf-8'))).payload
    resolved, projected = project_consumed(ctx.node, rel, logical, related={})
    assert resolved, (ctx.node, rel)
    ctx.record_edge(rel, InputEdge(
        file=prov.fingerprint_bytes(raw),
        consumed=prov.projection_fingerprint(projected)))


def _ctx(base: Path, sid: str = '123', node: str = 'str06.stage4.compile', *,
         paid: bool = False, write_ledger: bool = False) -> RunContext:
    return RunContext.open(base, sid, node, paid=paid, write_ledger=write_ledger)


def _plan_ctx(base: Path, sid: str = '123', *, paid: bool = True) -> RunContext:
    """通用写盘/提升测试使用的现役 Stage2 names 产地上下文（付费 producer）。"""
    return _ctx(base, sid, node=PLAN_NODE, paid=paid)


def _plan(ctx: RunContext, *, marker: str = '计划一',
          inputs: dict | None = None, origin: str | None = None,
          model: type[BaseModel] | None = None) -> NameMapV5:
    """一份最小合法 NameMap v5；``marker`` 是各事务测试的内容哨兵。

    ``inputs`` 显式钉来源边（现役 producer 的账由真实运行记录，测试按需复刻）；
    ``origin`` 用于三态键落盘形态的用例（非 None 三态键必须出现在盘上）。
    """
    provenance = ctx.free_provenance(**({'origin': origin} if origin is not None else {}))
    if inputs is not None:
        provenance = provenance.model_copy(update={
            'inputs': {key: InputEdge.model_validate(row) for key, row in inputs.items()}})
    return (model or NameMapV5)(
        entries=(NameAssignment(char_ref='char_001',
                                name=TargetName(family=marker)),),
        provenance=provenance)


def _binding(ctx: RunContext, *, checkpoint_sha: str, marker: str = '义务原稿',
             inputs: dict | None = None) -> SemanticBinding:
    """一份最小合法 SemanticBinding（str06.stage4.bind 的现役产地）。

    ``checkpoint_sha`` 是对 `runtime/preserve_checkpoint.json` 完整字节的绑定，
    对应旧链 compiled 计划对 A/B 两相的完整字节绑定。
    """
    provenance = ctx.free_provenance()
    if inputs is not None:
        provenance = provenance.model_copy(update={
            'inputs': {key: InputEdge.model_validate(row) for key, row in inputs.items()}})
    return SemanticBinding(
        binding_id='bd-fixture',
        preserve_checkpoint_sha256=checkpoint_sha,
        obligations=(SourceObligation(
            obligation_id='ob_01', kind=PreserveKind.EVENT_CORE, statement=marker,
            lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=()),
            must_preserve=True, satisfaction='明确呈现'),),
        entries=(),
        unbound=(),
        provenance=provenance,
    )


def _seed_preserve_checkpoint(base: Path) -> str:
    """落一份 Preserve checkpoint（bind 的付费上游），返回它的完整字节 SHA。

    新链的 compiled 字节绑定只剩这一处：SemanticBinding.preserve_checkpoint_sha256
    绑定 `runtime/preserve_checkpoint.json` 的完整字节（旧链是 A/B 两相两把 SHA）。
    """
    from contracts.schema.grounding import PreserveCheckpoint

    store = ArtifactStore(_ctx(base, node='str06.stage4.preserve', paid=False))
    checkpoint = PreserveCheckpoint(
        checkpoint_id='ckpt-fixture', policy_id='policy-fixture', policy_version=1,
        characters_sha256='a' * 64, blueprints_sha256='b' * 64,
        provider='fixture-provider', model='fixture-model', request_sha256='c' * 64,
        obligations=(SourceObligation(
            obligation_id='ob_01', kind=PreserveKind.EVENT_CORE, statement='义务',
            lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=()),
            must_preserve=True, satisfaction='明确呈现'),),
        checkpoint_sha256='d' * 64,
    )
    _write_payload(base / '书名_123', STR06_PRESERVE_CHECKPOINT,
                   checkpoint.model_dump(by_alias=True, mode='json'))
    raw = store.path(STR06_PRESERVE_CHECKPOINT).read_bytes()
    return hashlib.sha256(raw).hexdigest()


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
    base_a, base_b = (_book('111', 'A', b'{"characters": {}}'),
                      _book('222', 'B',
                            '{"characters": {"char_002": {"canonical": "乙"}}}'.encode('utf-8')))
    ctx_a, ctx_b = _plan_ctx(base_a, '111'), _plan_ctx(base_b, '222')
    _record_upstream_edge(ctx_a)
    _record_upstream_edge(ctx_b)

    assert set(ctx_a.edges) == set(ctx_b.edges) == {UPSTREAM}, '两级键本来就该撞——作用域靠实例给'
    assert ctx_a.edges[UPSTREAM].file != ctx_b.edges[UPSTREAM].file, \
        'A 书的指纹与 B 书的一样 —— 两个实例串了，那正是原型那条假匹配边'

    p = ArtifactStore(ctx_a).write(PLAN_REL, _plan(ctx_a))
    assert list(_prov_on_disk(p)['inputs']) == [UPSTREAM]
    assert _prov_on_disk(p)['inputs'][UPSTREAM]['file'] == ctx_a.edges[UPSTREAM].file


def test_edges_are_a_snapshot_not_the_live_dict():
    """外面改 `ctx.edges` 不该影响这一次跑——来源账是账，不是可编辑的草稿。"""
    ctx = _plan_ctx(_book())
    _record_upstream_edge(ctx)
    ctx.edges.clear()
    assert set(ctx.edges) == {UPSTREAM}


def test_read_own_with_sha256_returns_the_same_raw_bytes_without_an_input_edge():
    """本节点 hash-bound 复核要拿精确字节，但绝不能把自己记成自己的上游。"""
    base = _book()
    writer = _plan_ctx(base)
    writer_store = ArtifactStore(writer)
    _record_upstream_edge(writer)
    target = writer_store.write(PLAN_REL, _plan(writer))

    reader = _plan_ctx(base)
    artifact, digest = ArtifactStore(reader).read_own_with_sha256(
        PLAN_MODEL, PLAN_REL, require_current=True)

    assert artifact.entries[0].name.family == '计划一'
    assert digest == hashlib.sha256(target.read_bytes()).hexdigest()
    assert reader.edges == {}, '本节点自己的 hash-bound readback 泄漏进了 inputs'


def test_validation_reads_restore_every_source_ledger_even_when_the_guard_raises():
    """前置闸可以深读上游，但不得把校验材料写进后续 producer 的来源账。

    真实故障形状是旧链 Stage6 的 accepted-Review 闸深读了不属于 producer 的验证
    材料，``--recheck`` 随后把它们铸进 hook provenance；注册表正确地不知道这两条
    假依赖，于是正式写回被 ``consumed_undeclared`` 回滚。这里直接守 RunContext 那一个
    生产作用域，并覆盖异常退出——关闭式闸抛错也不能留下半张账。
    （键选现役新链产物相对路径；record_* 只认字符串键，不作注册表校验。）
    """
    ctx = _ctx(_book(), node='str06.stage5.hook')
    original_edge = InputEdge(file='1' * 10, consumed='2' * 10)
    ctx.record_edge('stage1/hook_copy.json', original_edge)
    ctx.record_judged('stage5/body/ch01.txt', '3' * 10)
    ctx.record_artifact_root('stage2/observations/book.json', ctx.root_clean_sha1)

    with pytest.raises(RuntimeError, match='guard stopped'):
        with ctx.validation_reads():
            ctx.record_edge('stage1/main_text.json', InputEdge(file='4' * 10, consumed=None))
            ctx.record_edge('stage5/hook/receipt.json', InputEdge(file='5' * 10, consumed=None))
            ctx.record_judged('stage5/body/ch02.txt', '6' * 10)
            ctx.record_artifact_root('stage4/design/body_design.json', '7' * 40)
            raise RuntimeError('guard stopped')

    assert ctx.edges == {'stage1/hook_copy.json': original_edge}
    assert ctx.judged == {'stage5/body/ch01.txt': '3' * 10}
    assert ctx._observed_roots == {
        'stage2/observations/book.json': ctx.root_clean_sha1,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 二、读：记账在结构上不可能漏
# ─────────────────────────────────────────────────────────────────────────────

def test_reading_records_the_edge_without_anyone_listing_it():
    """⚠ **不在每个落盘点手工列举 `inputs`**：那份清单会烂，而且是**静默**地烂。

    某个节点以后多吃一份上游、却忘了加进清单时，`_provenance` 会继续显示「匹配」。
    改成「谁读了就记谁」之后，**结构上不可能漏**——拿不到数据就等于没读。
    """
    ctx = _ctx(_book(), node='stage0.remove', paid=False)
    raw = (ctx.novel_dir / UPSTREAM).read_bytes()
    logical = decode_artifact(UPSTREAM, json.loads(raw.decode('utf-8'))).payload
    ctx.record_edge(UPSTREAM, InputEdge(
        file=prov.fingerprint_bytes(raw),
        consumed=ArtifactStore(ctx)._consumed_fingerprint(ctx.node, UPSTREAM, logical)))
    assert ctx.edges[UPSTREAM].consumed is None, '没声明投影 ⇒ 这条边诚实地是「不知道」'


def test_reading_with_a_projection_fingerprints_the_projection_not_the_whole_file():
    """**建槽位，不填槽位**——但槽位一旦填了，新鲜度才可能判成 `fresh`。

    ⚠⚠ **必须断言到值，不能只断言「非 None」。**
    把 `projection_fingerprint(project(data))` 写成 `projection_fingerprint(data)`
    ——只错一个函数调用——`consumed` 照样非空、照样是 10 位十六进制，
    而这条边从此指的是**整份文件**：`Q-4` 那一格（「改一个不相干的字段不该让我的结论作废」）
    **静默失效**，`fresh` 再也不会因为「我依赖的那部分没变」而出现。
    一条只查「非 None」的断言在这里等于没查。

    现役投影取 `str06.stage2.book` 对 `stage1/main_text.json` 的 `stage2_main_text`：
    只消费（章号, 正文）对，诊断展示字段不进消费身份。
    """
    payload = [{'chapter_ordinal': 1, 'text': '真正读的正文', 'diagnostic_note': '不消费'}]
    base = _book()
    book = base / '书名_123' / MT_REL
    book.write_text(
        json.dumps(encode_artifact(MT_REL, payload), ensure_ascii=False),
        encoding='utf-8', newline='')
    ctx = _ctx(base, node=MT_NODE, paid=True)
    _record_upstream_edge(ctx, rel=MT_REL)

    from novel.prov import projection_fingerprint
    assert ctx.edges[MT_REL].consumed == projection_fingerprint(
        [(1, '真正读的正文')])
    disk = decode_artifact(MT_REL, json.loads(book.read_text('utf-8'))).payload
    assert ctx.edges[MT_REL].consumed != projection_fingerprint(disk),         '投影指纹等于整份文件的指纹 —— `project()` 多半没被调用'


def test_freshness_reuses_the_registered_projection_that_wrote_the_edge():
    """freshness 重算用**写边时的同一注册投影**：来源账自身不进消费身份（fresh），
    业务字段变化照旧 stale。B5 换轨：窄投影版的「诊断字段不消费」断言已前移到
    `test_reading_with_a_projection_fingerprints_the_projection_not_the_whole_file`
    （edge 指纹层）；本条改用现役 (naming × canonical Characters) 的整份载荷对——
    来源账之外任何字段变化都改变投影，这正是新链「整份载荷」的口径。"""
    view = _extraction_payload()
    base = _book()
    ctx = _plan_ctx(base)
    st = ArtifactStore(ctx)
    _write_payload(base / '书名_123', UPSTREAM, view)
    _record_upstream_edge(ctx)
    st.write(PLAN_REL, _plan(ctx))
    assert st.freshness(PLAN_REL).state == 'fresh'

    upstream = base / '书名_123' / UPSTREAM
    view['_provenance']['code'] = 'source-v2'          # 来源账不进消费身份
    upstream.write_text(
        json.dumps(encode_artifact(UPSTREAM, view), ensure_ascii=False),
        encoding='utf-8', newline='')
    same = st.freshness(PLAN_REL)
    assert (same.state, same.reason) == ('fresh', 'match')

    view['characters']['char_001']['canonical'] = '乙'   # 业务字段变化 ⇒ stale
    upstream.write_text(
        json.dumps(encode_artifact(UPSTREAM, view), ensure_ascii=False),
        encoding='utf-8', newline='')
    changed = st.freshness(PLAN_REL)
    assert (changed.state, changed.reason) == ('stale', 'consumed_changed')


def test_stage4_blueprint_projection_tracks_exact_material_and_ignores_provenance():
    """新链投影统一整份载荷（剥上游 `_provenance`）：剧情、证据一并进消费身份。

    旧链 blueprint 投影按节点裁剪语义子集（content/evidence/source_context 各有
    忽略规则）；F8a 之后裁剪投影随旧链退役——「只改证据不重跑」的豁免格一并消失，
    上游任何业务字段变化都改变投影，只有来源账本身不进消费身份。
    """
    project = projection_for('str06.stage4.preserve', STR06_STAGE2_BLUEPRINTS)
    assert project is artifact_payload
    base = {
        'chapters': [{'chapter': 1, 'beats': [
            {'beat_id': 'b01_01', 'content': '甲收到一封信。',
             'source_refs': ['stage1/ch01']},
        ]}],
        '_provenance': {'code': 'old'},
    }
    changed_provenance = {**base, '_provenance': {'code': 'new'}}
    assert project(base) == project(changed_provenance)

    changed_content = copy.deepcopy(base)
    changed_content['chapters'][0]['beats'][0]['content'] = '甲撤回了那封信。'
    assert project(base) != project(changed_content)

    changed_source = copy.deepcopy(base)
    changed_source['chapters'][0]['beats'][0]['source_refs'] = ['stage1/ch02']
    assert project(base) != project(changed_source), \
        '证据定位也进消费身份：新链没有「只改证据不重跑」的豁免格'


def _extraction_payload() -> dict:
    """`stage2/compiled/book_extraction.json`（CharactersCore）合成样例。

    `stage2/characters/characters.json` 的 core view 与它同形（characters +
    initial_state）；`_provenance` 不进消费投影，其内字段取值不影响 freshness。
    """
    return {
        'characters': {
            'char_001': {
                'canonical': '甲',
                'surfaces': [
                    {'surface': '甲', 'from_chapter': 1,
                     'source_refs': ['stage1/ch01']},
                    {'surface': '掌柜', 'from_chapter': 1,
                     'source_refs': ['stage1/ch01']},
                ],
            },
        },
        'initial_state': [],
        '_provenance': {
            'code': 'source-v1',
            'root_clean_sha1': '0' * 40,
            'inputs': {},
            'model': 'fixture-model',
        },
    }


def _edit_extraction(view: dict, edited: str) -> None:
    """对 extraction/characters 载荷做一次业务字段编辑（freshness 用例共用）。"""
    char = view['characters']['char_001']
    if edited == 'canonical':
        char['canonical'] = '乙'
    elif edited == 'surface':
        char['surfaces'][1]['surface'] = '东家'
    elif edited == 'from_chapter':
        char['surfaces'][0]['from_chapter'] = 2
    elif edited == 'until_chapter':
        char['surfaces'][0]['until_chapter'] = 3
    elif edited == 'source_refs':
        char['surfaces'][0]['source_refs'] = ['stage1/ch09']
    elif edited == 'char_id':
        view['characters']['char_002'] = view['characters'].pop('char_001')
    elif edited == 'extra_character':
        view['characters']['char_002'] = {
            'canonical': '乙',
            'surfaces': [{'surface': '乙', 'from_chapter': 1,
                          'source_refs': ['stage1/ch01']}],
        }
    elif edited == 'drop_character':
        view['characters'].pop('char_001')
    elif edited == 'extra_surface':
        char['surfaces'].append({'surface': '老掌柜', 'from_chapter': 2,
                                 'source_refs': ['stage1/ch02']})
    elif edited == 'initial_state':
        view['initial_state'].append({
            'kind': 'character', 'char_id': 'char_001', 'claim': '身负重债'})
    elif edited == 'extra_key':
        view['derived_note'] = '书级补充事实'
    else:
        raise AssertionError(f'未知编辑：{edited}')


#: 新链下游消费家族：(节点, 下游产物, 上游产物)。投影统一 `artifact_payload`。
#: （B2 五节点重建起 preserve 的 canonical 上游是 Characters；旧抽取派生输入
#: `stage2/compiled/book_extraction.json` 已退役，不再作上游格。）
DOWNSTREAM_FAMILY = (
    ('str06.stage4.preserve', STR06_RECONSTRUCTION_BRIEF, STR06_STAGE2_CHARACTERS),
    ('str06.stage4.body_design', STR06_BODY_DESIGN, STR06_STAGE2_CHARACTERS),
    ('str06.stage4.hook_design', STR06_PRESENTATION_GRAPH, STR06_STAGE2_CHARACTERS),
    ('str06.stage7.naming', STR06_NAME_MAP, STR06_STAGE2_CHARACTERS),
    ('str06.stage7.assemble', STR06_DELIVERY_MANIFEST, STR06_STAGE2_CHARACTERS),
)

#: Stage4 产地的上游格（canonical Characters；载荷形状同 CharactersCore core view）。
STAGE4_EXTRACTION_FAMILY = DOWNSTREAM_FAMILY[:2]


def _write_consumed_subject(
    base: Path, *, node: str, rel: str, upstream: str,
    upstream_payload: dict | None = None,
    downstream_payload: dict | None = None,
) -> ArtifactStore:
    """把上游载荷落盘，再按 `node` **注册的那一个**投影给 `rel` 记来源边。

    新链投影统一整份载荷（剥上游 `_provenance`）；本助手只登记单条注册边，
    完整 producer 链另有集成测试。下游载荷形状不参与 freshness 判定，只给
    一个与产物语义相称的占位键。
    """
    book = base / '书名_123'
    view = upstream_payload if upstream_payload is not None else _extraction_payload()
    _write_payload(book, upstream, view)
    resolved, projected = project_consumed(node, upstream, view, related={})
    assert resolved, (node, upstream)
    inputs = {upstream: {
        'file': prov.fingerprint_bytes((book / upstream).read_bytes()),
        'consumed': prov.projection_fingerprint(projected),
    }}
    payload = {**({'chapters': []} if downstream_payload is None else downstream_payload),
               '_provenance': {
                   'root_clean_sha1': prov.content_sha1(b'fixture clean root\n'),
                   'inputs': inputs,
               }}
    _write_payload(book, rel, payload)
    return ArtifactStore(_ctx(base, node=node, paid=False))


@pytest.mark.parametrize(('node', 'rel', 'upstream'), STAGE4_EXTRACTION_FAMILY)
@pytest.mark.parametrize('edited_field', (
    'canonical', 'surface', 'from_chapter', 'initial_state',
))
def test_stage2_extraction_edits_invalidate_real_store_freshness(
    node: str, rel: str, upstream: str, edited_field: str,
):
    base = _book()
    store = _write_consumed_subject(base, node=node, rel=rel, upstream=upstream)
    assert store.freshness(rel).state == 'fresh'

    view = _extraction_payload()
    _edit_extraction(view, edited_field)
    _write_payload(base / '书名_123', upstream, view)

    report = store.freshness(rel)
    assert (report.state, report.reason) == ('stale', 'consumed_changed')
    edge = next(edge for edge in report.edges if edge.key == upstream)
    assert edge.recorded_consumed != edge.now_consumed


@pytest.mark.parametrize(('node', 'rel', 'upstream'), STAGE4_EXTRACTION_FAMILY)
def test_downstream_projection_ignores_unconsumed_source_provenance(
    node: str, rel: str, upstream: str,
):
    base = _book()
    store = _write_consumed_subject(base, node=node, rel=rel, upstream=upstream)

    view = _extraction_payload()
    view['_provenance']['code'] = 'source-v2'
    _write_payload(base / '书名_123', upstream, view)

    report = store.freshness(rel)
    assert (report.state, report.reason) == ('fresh', 'match')
    edge = next(edge for edge in report.edges if edge.key == upstream)
    assert edge.recorded_file != edge.now_file
    assert edge.recorded_consumed == edge.now_consumed


@pytest.mark.parametrize(('node', 'rel', 'upstream'), DOWNSTREAM_FAMILY)
def test_downstream_freshness_tracks_every_source_reference_edit(
    node: str, rel: str, upstream: str,
):
    """五格下游家族对上游任何业务字段（含来源引用）都整份消费。

    旧链五类节点对「仅证据锚点被改」判 fresh；F8a 把裁剪投影退役成整份载荷后，
    证据/来源引用就是业务内容本身——改动必须 stale，不许再拿投影裁剪把真变化
    洗成「没变」。
    """
    base = _book()
    store = _write_consumed_subject(base, node=node, rel=rel, upstream=upstream)
    assert store.freshness(rel).state == 'fresh'

    view = _extraction_payload()
    _edit_extraction(view, 'source_refs')
    _write_payload(base / '书名_123', upstream, view)

    report = store.freshness(rel)
    assert (report.state, report.reason) == ('stale', 'consumed_changed')
    edge = next(edge for edge in report.edges if edge.key == upstream)
    assert edge.recorded_file != edge.now_file, '证据改动必须真实落到上游字节'
    assert edge.recorded_consumed != edge.now_consumed


def _book_observation_payload() -> dict:
    """`stage2/observations/book.json` 的合成样例（B2 观察 wire；窗口坐标不在
    wire——from_chapter/until_chapter 由 compiler 派生，I-21）。"""
    return {
        'protocol_version': 'stage2-observation-v1',
        'characters': {
            'char_001': {
                'canonical': '甲',
                'surfaces': [
                    {'surface': '甲', 'source_refs': ['e01_01']},
                    {'surface': '掌柜', 'source_refs': ['e01_01']},
                ],
            },
        },
        'initial_state': [
            {'target': {'kind': 'character', 'char_id': 'char_001'},
             'key': 'debt', 'value': '身负重债', 'persistence': 'stable',
             'knowledge': None, 'source_refs': ['e01_01']},
        ],
        'world_constraints': [],
        '_provenance': {
            'code': 'source-v1',
            'root_clean_sha1': '0' * 40,
            'inputs': {},
            'model': 'fixture-model',
        },
    }


def _edit_book_observation(view: dict, edited: str) -> None:
    """对 book 观察载荷做一次业务字段编辑（freshness 用例共用）。"""
    char = view['characters']['char_001']
    if edited == 'canonical':
        char['canonical'] = '乙'
    elif edited == 'surface':
        char['surfaces'][1]['surface'] = '东家'
    elif edited == 'source_refs':
        char['surfaces'][0]['source_refs'] = ['e09_09']
    elif edited == 'char_id':
        view['characters']['char_002'] = view['characters'].pop('char_001')
    elif edited == 'extra_character':
        view['characters']['char_002'] = {
            'canonical': '乙',
            'surfaces': [{'surface': '乙', 'source_refs': ['e01_01']}],
        }
    elif edited == 'drop_character':
        view['characters'].pop('char_001')
    elif edited == 'initial_state':
        view['initial_state'][0]['value'] = '债已还清'
    else:
        raise AssertionError(f'未知编辑：{edited}')


@pytest.mark.parametrize('edited', (
    'char_id', 'canonical', 'surface', 'from_chapter',
))
def test_characters_whole_payload_projection_tracks_every_name_field(edited: str):
    """人物登记整份消费：称名面与窗口时点任一变化都改变下游投影。

    旧链 stage5.review 只绑四类称名字段、其余元数据忽略；新链没有第二套
    「名字基线」投影，`artifact_payload` 就是唯一口径。
    """
    project = projection_for('str06.stage4.body_design', STR06_STAGE2_CHARACTERS)
    assert project is not None
    view = _extraction_payload()
    before = project(view)

    changed = copy.deepcopy(view)
    _edit_extraction(changed, edited)
    assert project(changed) != before, edited


def test_characters_whole_payload_projection_ignores_only_source_provenance():
    """整份消费唯一忽略的是上游自己的来源账。"""
    project = projection_for('str06.stage4.body_design', STR06_STAGE2_CHARACTERS)
    assert project is not None
    view = _extraction_payload()
    expected = project(view)

    changed = copy.deepcopy(view)
    changed['_provenance']['code'] = 'source-v2'
    changed['_provenance']['model'] = 'fixture-model-2'
    assert project(changed) == expected


@pytest.mark.parametrize('edited', (
    # stage1/main_text.json 的窄投影（stage2_main_text）：只消费（章号, 正文）。
    'mt_diagnostic_note', 'mt_chapter_ordinal', 'mt_text',
    # stage2/observations/book.json 的整份投影：业务字段全消费。
    'canonical', 'surface', 'char_id', 'source_refs', 'initial_state',
    'extra_character', 'drop_character',
    # 来源账不进消费身份，两类来源编辑都保持 fresh。
    'provenance_code', 'provenance_model',
))
def test_names_consumes_whole_observation_and_narrow_main_text(edited: str):
    """names 节点的消费面 = MainText 窄投影 + book 观察整份投影。

    ⭐ 旧链 blueprint 靠裁剪投影把非称名字段挡在重跑之外；B2 五节点后 names
    吃 MainText 章面（窄）＋ book 观察（整份），观察的业务字段一变就必须
    stale——收窄不许收成「什么都不看」。B5：退役的 book_extraction 上游格
    换成 `stage2/observations/book.json`（称谓窗口坐标已由 compiler 派生，
    wire 不再有 from_chapter/until_chapter 可编辑，对应参数删除）。
    """
    main_text = [
        {'chapter_ordinal': 1, 'text': '正文不变', 'diagnostic_note': '原值'},
    ]
    observation = _book_observation_payload()
    base = _book()
    book = base / '书名_123'
    (book / MT_REL).write_text(
        json.dumps(encode_artifact(MT_REL, main_text), ensure_ascii=False),
        encoding='utf-8', newline='')
    _write_payload(book, STR06_STAGE2_OBS_BOOK, observation)
    inputs = {}
    for upstream, view in ((MT_REL, main_text),
                           (STR06_STAGE2_OBS_BOOK, observation)):
        resolved, projected = project_consumed(
            NAMES_NODE, upstream, view, related={})
        assert resolved, upstream
        inputs[upstream] = {
            'file': prov.fingerprint_bytes((book / upstream).read_bytes()),
            'consumed': prov.projection_fingerprint(projected),
        }
    # names 观察不带 provenance 字段，来源账由写路注入盘上载荷——测试按同一
    # 盘面形状手写 `_provenance`（candidate_freshness 只读盘上这一份账）。
    _write_payload(book, NAMES_REL, {
        'protocol_version': 'stage2-observation-v1',
        'structure': {'families': {}, 'people': {}},
        '_provenance': {
            'root_clean_sha1': prov.content_sha1(b'fixture clean root\n'),
            'inputs': inputs,
        },
    })
    store = ArtifactStore(_ctx(base, node=NAMES_NODE, paid=False))
    assert store.freshness(NAMES_REL).state == 'fresh'

    if edited == 'mt_diagnostic_note':
        main_text[0]['diagnostic_note'] = '新值'
        expected = ('fresh', 'match')
    elif edited == 'mt_chapter_ordinal':
        main_text[0]['chapter_ordinal'] = 2
        expected = ('stale', 'consumed_changed')
    elif edited == 'mt_text':
        main_text[0]['text'] = '真正消费的正文改了'
        expected = ('stale', 'consumed_changed')
    elif edited == 'provenance_code':
        observation['_provenance']['code'] = 'source-v2'
        expected = ('fresh', 'match')
    elif edited == 'provenance_model':
        observation['_provenance']['model'] = 'fixture-model-2'
        expected = ('fresh', 'match')
    else:
        _edit_book_observation(observation, edited)
        expected = ('stale', 'consumed_changed')

    if edited.startswith('mt_'):
        (book / MT_REL).write_text(
            json.dumps(encode_artifact(MT_REL, main_text), ensure_ascii=False),
            encoding='utf-8', newline='')
    else:
        _write_payload(book, STR06_STAGE2_OBS_BOOK, observation)

    report = store.freshness(NAMES_REL)
    assert (report.state, report.reason) == expected, edited


def test_characters_timing_edit_stales_whole_payload_consumer():
    """同一个人挂多个称呼时，窗口时点属于消费身份：改了就 stale。"""
    base = _book()
    store = _write_consumed_subject(
        base, node='str06.stage4.body_design', rel=STR06_BODY_DESIGN,
        upstream=STR06_STAGE2_CHARACTERS)
    assert store.freshness(STR06_BODY_DESIGN).state == 'fresh'

    view = _extraction_payload()
    view['characters']['char_001']['surfaces'][1]['from_chapter'] = 2
    _write_payload(base / '书名_123', STR06_STAGE2_CHARACTERS, view)

    report = store.freshness(STR06_BODY_DESIGN)
    assert (report.state, report.reason) == ('stale', 'consumed_changed')
    edge = next(edge for edge in report.edges
                if edge.key == STR06_STAGE2_CHARACTERS)
    assert edge.recorded_consumed != edge.now_consumed


def test_characters_timing_edit_stales_even_without_attestation():
    """时点不再随「字面是否真的出现」分叉：整份载荷消费没有这层豁免。

    旧链投影只跟踪真实出现的字面（从未出现的歧义称呼改时点判 fresh）；
    F8a 之后称名窗口的任何时点变化都是上游业务变化。
    """
    base = _book()
    view = _extraction_payload()
    view['characters']['char_002'] = {
        'canonical': '乙',
        'surfaces': [{'surface': '乙', 'from_chapter': 1,
                      'source_refs': ['stage1/ch01']}],
    }
    store = _write_consumed_subject(
        base, node='str06.stage4.body_design', rel=STR06_BODY_DESIGN,
        upstream=STR06_STAGE2_CHARACTERS, upstream_payload=view)
    assert store.freshness(STR06_BODY_DESIGN).state == 'fresh'

    view['characters']['char_002']['surfaces'][0]['from_chapter'] = 9
    _write_payload(base / '书名_123', STR06_STAGE2_CHARACTERS, view)

    report = store.freshness(STR06_BODY_DESIGN)
    assert (report.state, report.reason) == ('stale', 'consumed_changed')


@pytest.mark.parametrize('touched', ['unattested', 'attested'])
def test_characters_timing_edits_stale_whatever_the_attestation_clock(touched: str):
    """时点只随真实出现进入旧链最终候选；新链整份消费不区分这两格。"""
    base = _book()
    store = _write_consumed_subject(
        base, node='str06.stage4.body_design', rel=STR06_BODY_DESIGN,
        upstream=STR06_STAGE2_CHARACTERS)
    assert store.freshness(STR06_BODY_DESIGN).state == 'fresh'

    view = _extraction_payload()
    index = {'unattested': 0, 'attested': 1}[touched]
    view['characters']['char_001']['surfaces'][index]['from_chapter'] = 3
    _write_payload(base / '书名_123', STR06_STAGE2_CHARACTERS, view)

    report = store.freshness(STR06_BODY_DESIGN)
    assert (report.state, report.reason) == ('stale', 'consumed_changed')


def test_characters_window_close_edit_stales_whole_payload_consumer():
    """旧链「被归一字面遮蔽的时点不参与消费」的豁免格随裁剪投影一并退役：
    称谓窗口关闭（until_chapter）同样是业务变化。"""
    base = _book()
    store = _write_consumed_subject(
        base, node='str06.stage4.body_design', rel=STR06_BODY_DESIGN,
        upstream=STR06_STAGE2_CHARACTERS)
    assert store.freshness(STR06_BODY_DESIGN).state == 'fresh'

    view = _extraction_payload()
    view['characters']['char_001']['surfaces'][1]['until_chapter'] = 4
    _write_payload(base / '书名_123', STR06_STAGE2_CHARACTERS, view)

    report = store.freshness(STR06_BODY_DESIGN)
    assert (report.state, report.reason) == ('stale', 'consumed_changed')
    edge = next(edge for edge in report.edges
                if edge.key == STR06_STAGE2_CHARACTERS)
    assert edge.recorded_consumed != edge.now_consumed


@pytest.mark.parametrize('edited', (
    'canonical', 'surface', 'extra_surface', 'drop_character', 'extra_character',
))
def test_characters_payload_tracks_every_name_change(edited: str):
    """称名一变就必须 stale——整份消费不许收成「什么都不看」。

    与窄投影退役是同一枚硬币的两面：旧链少看一个非称名字段是省重跑，
    新链把「该看的」扩成整份，代价是任何业务变化都诚实过期。
    """
    base = _book()
    view = _extraction_payload()
    if edited == 'drop_character':
        view['characters']['char_002'] = {
            'canonical': '乙',
            'surfaces': [{'surface': '乙', 'from_chapter': 1,
                          'source_refs': ['stage1/ch01']}],
        }
    store = _write_consumed_subject(
        base, node='str06.stage4.body_design', rel=STR06_BODY_DESIGN,
        upstream=STR06_STAGE2_CHARACTERS, upstream_payload=view)
    assert store.freshness(STR06_BODY_DESIGN).state == 'fresh'

    changed = copy.deepcopy(view)
    _edit_extraction(changed, edited)
    _write_payload(base / '书名_123', STR06_STAGE2_CHARACTERS, changed)

    report = store.freshness(STR06_BODY_DESIGN)
    assert (report.state, report.reason) == ('stale', 'consumed_changed')
    edge = next(edge for edge in report.edges
                if edge.key == STR06_STAGE2_CHARACTERS)
    assert edge.recorded_consumed != edge.now_consumed


def test_consumer_closes_when_upstream_characters_changed_after_the_fact():
    """下游写盘之后上游被改写 ⇒ 下游 freshness 必须闭合，不许拿旧账装新鲜。

    旧链这一格由「普通 freshness 不递归特殊投影」的补闸承担；新链没有特殊
    投影，普通 freshness 直接对上游现值复算，同一条需求由本层原生满足。
    """
    base = _book()
    store = _write_consumed_subject(
        base, node='str06.stage4.body_design', rel=STR06_BODY_DESIGN,
        upstream=STR06_STAGE2_CHARACTERS)
    original = (base / '书名_123' / STR06_STAGE2_CHARACTERS).read_bytes()
    assert store.freshness(STR06_BODY_DESIGN).state == 'fresh'

    view = _extraction_payload()
    _edit_extraction(view, 'canonical')
    _write_payload(base / '书名_123', STR06_STAGE2_CHARACTERS, view)
    assert store.freshness(STR06_BODY_DESIGN).state == 'stale', \
        '上游变了而下游还 fresh，就是拿着过期名册继续产出'

    (base / '书名_123' / STR06_STAGE2_CHARACTERS).write_bytes(original)
    assert store.freshness(STR06_BODY_DESIGN).state == 'fresh', \
        '上游恢复到记账字节后，下游结论随之恢复'


def test_consumer_freshness_ignores_the_invalid_twin_while_current_intact():
    """`.INVALID` 孪生不是 freshness 的输入：普通 consumer 的闸在 queue/resume 面。

    旧链把「最新 INVALID 不得冒充可用底座」塞进投影 freshness；新链的
    freshness 只对 current 载荷复算，INVALID 闸由恢复/续跑判定单独把守。
    """
    base = _book()
    store = _write_consumed_subject(
        base, node='str06.stage4.body_design', rel=STR06_BODY_DESIGN,
        upstream=STR06_STAGE2_CHARACTERS)

    broken = _extraction_payload()
    broken['characters'] = '这不是人物表'
    _write_payload(base / '书名_123', paths.invalid_of(STR06_STAGE2_CHARACTERS), broken)

    report = store.freshness(STR06_BODY_DESIGN)
    assert (report.state, report.reason) == ('fresh', 'match'), \
        'current 未变时，INVALID 孪生不得把下游拖进 unknown/stale'


def test_consumer_freshness_never_calls_damaged_upstream_fresh():
    """current 上游坏字节 ⇒ 指纹变化（file_changed），消费投影也复算不出：
    无论落到 stale/unknown 哪一格，都不许洗成 fresh。"""
    base = _book()
    store = _write_consumed_subject(
        base, node='str06.stage4.body_design', rel=STR06_BODY_DESIGN,
        upstream=STR06_STAGE2_CHARACTERS)
    (base / '书名_123' / STR06_STAGE2_CHARACTERS).write_bytes(b'{broken')

    report = store.freshness(STR06_BODY_DESIGN)
    assert (report.state, report.reason) == ('stale', 'file_changed'), \
        '上游坏字节先落在文件指纹上；两种结果都不是 fresh，读侧照样被闸'


def test_blueprint_projection_does_not_invent_a_character_id_format_gate():
    """Global 人物表键只作成员闭集；投影不能偷偷加一条不存在的格式判据。"""
    from contracts.schema.stage2 import BlueprintCharacterProjection

    artifact = BlueprintCharacterProjection.model_validate({
        'chapters': [{
            'chapter_ordinal': 1,
            'past_attested_surfaces': ['甲'],
            'visible_character_refs': {'protagonist': ['甲']},
            'character_bindings': {'protagonist': [
                {'surface': '甲', 'identity_revealed_from': 1},
            ]},
        }],
    })
    assert artifact.chapters[0].visible_character_refs == {
        'protagonist': ['甲']}


@pytest.mark.parametrize('malformed', (
    None, 42, 'x', [], {}, {'entities': None}, {'entities': []}, {'entities': 'x'},
    {'entities': {'characters': None}}, {'entities': {'characters': []}},
    {'entities': {'characters': {'char_001': None}}},
    {'entities': {'characters': {'char_001': {'address': 'x'}}}},
    {'entities': {'characters': {'char_001': {'address': [None]}}}},
))
def test_registered_projections_survive_malformed_upstream(malformed: object):
    """畸形上游只许算不出投影，不许抛。

    投影吃的是**未经模型校验**的 JSON：一份陈旧上游可能已经不合当前 schema，
    而 freshness 恰恰必须在这种情况下仍能回答（`novel/consumption.py` 文件头）。
    抛出去会让 `state` 查询整个崩掉，那比 `unknown` 坏得多。

    （F8b-3：旧链 `novel.stages.stage2.shared.character_refs::character_ref_inventory`
    随 Stage2 整层删除；同族不变量改扫**现役注册投影**全集——每个
    `CONSUMED_PROJECTIONS` 里的投影函数吃这份畸形矩阵都必须只回答
    「算得出／算不出」，不许把 `state` 查询拖崩。）
    """
    from novel.consumption import CONSUMED_PROJECTIONS

    projections = {project.__name__: project
                   for table in CONSUMED_PROJECTIONS.values()
                   for project in table.values()}
    assert projections, '注册投影是空的——本条在扫一个空表'
    for name, project in sorted(projections.items()):
        result = project(malformed)     # 不许抛；抛出去比算不出坏得多
        assert result is None or isinstance(
            result, (dict, list, tuple, str, int, bool)), (name, type(result))


@pytest.mark.parametrize('malformed', (
    {},
    {'entities': None},
    {'entities': {'characters': None}},
))
def test_projection_marks_missing_character_maps_empty(malformed: object):
    """缺地图与空地图分得开：前者算不出（None），后者是合法空值——都不许抛。

    （F8b-3 首登记；收尾批复核：`character_knowledge_reference` 随 B2 五节点
    重建失去全部注册位并删除，`str06.stage2.source` 亦退役。现役同域裁剪投影
    是 `novel.consumption.stage2_main_text`——`str06.stage2.book/chapters/names`
    三节点的注册投影：整份载荷不是数组 ⇒ None；空数组 ⇒ 空清单。）
    """
    from novel.consumption import stage2_main_text

    # 畸形一律算不出，不许抛：`None` 是「算不出」，`[]` 是「空的」。
    for payload in (malformed, {'characters': malformed}, {'root': [malformed]}):
        assert stage2_main_text(payload) is None, payload
    # 合法空值不是错误：空章数组该给出空清单（与「缺地图」分得开）。
    assert stage2_main_text([]) == []


def test_stage7_naming_projection_tracks_role_sent_to_rename_prompt():
    """改名消费人物登记整份载荷；身份与证据引用一并进 NameMap 的过期判定。"""
    project = projection_for('str06.stage7.naming', STR06_STAGE2_CHARACTERS)
    assert project is artifact_payload
    view = _extraction_payload()
    before = project(view)

    renamed = copy.deepcopy(view)
    renamed['characters']['char_001']['canonical'] = '乙'
    assert project(renamed) != before, '角色身份进入改名 prompt，变了必须 stale'

    evidence_only = copy.deepcopy(view)
    evidence_only['characters']['char_001']['surfaces'][0][
        'source_refs'] = ['stage1/ch09']
    assert project(evidence_only) != before, \
        '证据引用同样进整份消费身份，旧链「仅证据不重跑」的豁免已退役'

    provenance_only = copy.deepcopy(view)
    provenance_only['_provenance']['code'] = 'source-v2'
    assert project(provenance_only) == before


def test_projection_registry_resolves_dynamic_and_invalid_artifact_paths():
    assert producer_node('stage5/body/ch01.txt') == 'str06.stage5.body'
    assert producer_node('stage5/deep/ch01.json') is None, \
        '退役双臂路径只作 legacy 证据，不能冒充当前 producer 产物'
    assert producer_node('stage5/acceptance/body/ch01.INVALID.json') == 'str06.stage5.body'
    assert set(CONSUMED_PROJECTIONS['str06.stage4.compile']) == {
        'stage2/characters/characters.json',
        'stage4/design/presentation_graph.json',
        'stage4/grounding/semantic_binding.json',
    }
    assert projection_for('stage3.style', UPSTREAM) is None
    assert projection_for('stage3.style', 'stage1/main_text.INVALID.json') is None
    assert producer_node('stage3/style_grounding.json') is None


def test_stage2_review_projection_tracks_generation_semantics_not_reason_wording():
    # Stage2 语义审阅退役：global_review 不再是 stage4.context 的消费投影，
    # 旧产物也不得借投影通道回到消费面。
    assert projection_for('stage4.context', STAGE2_GLOBAL_REVIEW) is None, (
        'global_review 已随语义审阅退役，不能再回到 stage4.context 消费面')
    assert projection_for('stage4.context', 'stage2/knowledge_links.json') is None


def test_stage5_review_tracks_context_code_without_overinvalidating_writing():
    """Hook 写手消费 HookContext 整份载荷（剥上游来源账）：业务变了才 stale。

    旧链 write/review 双投影（review 额外绑 code 版本）已随 stage5.review 退役；
    代码版本属于产物自己的来源账，不再混进上游消费身份。
    """
    hook_projection = projection_for('str06.stage5.hook', STR06_HOOK_CONTEXT)
    assert hook_projection is artifact_payload
    before = {
        'chapters': [{'output_ordinal': 1}],
        'design_brief': '压住悬念，先给后果。',
        '_provenance': {'code': 'old-code'},
    }
    after = {**before, '_provenance': {'code': 'new-code'}}
    assert hook_projection(before) == hook_projection(after)

    rewritten = copy.deepcopy(before)
    rewritten['design_brief'] = '改从收信人视角切入。'
    assert hook_projection(before) != hook_projection(rewritten)

def test_a_draft_cannot_be_read_through_the_edge_path():
    """⚠⚠ 成稿走 `read_judged`，**不走 `read`**（`_common.md` §3.2）。

    这里保留旧三级路径作反例：它在两级 inputs 键下会塌成一个反查不到的键。
    现役 canonical 虽只有两级也仍走 judged，因为成稿是被审判主体，且要另记正文消费投影。
    """
    base = _book()
    (base / '书名_123' / 'stage5' / 'deep').mkdir(parents=True)
    (base / '书名_123' / 'stage5' / 'deep' / 'ch01.json').write_text(
        json.dumps(encode_artifact('stage5/deep/ch01.json', [])),
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
        st.read(Loose, 'stage2/local_notes.json')

    (base / '书名_123' / 'stage2').mkdir(exist_ok=True)
    (base / '书名_123' / 'stage2' / 'local_notes.json').write_bytes(b'{"sections": [1')
    with pytest.raises(ArtifactUnreadable, match='合法 JSON'):
        st.read(Loose, 'stage2/local_notes.json')


# ─────────────────────────────────────────────────────────────────────────────
# 三、写：往返 · 原子 · 两道闸
# ─────────────────────────────────────────────────────────────────────────────

def test_what_we_write_is_what_we_can_read_back():
    """⭐ **落盘 → 加载的往返**，A2/A3 从来没跑过一次（它们只做过「盘上 → 模型」）。

    ⚠ 这条抓到过一个真 bug：`Provenance.to_disk()` 原来是 `model_dump(exclude_none=True)`，
    而 `exclude_none` 是**全局**的 ⇒ 它连着剥掉两处**必填但可为 `null`** 的键
    （`inputs[].consumed` 与 `usage.*_tokens`），于是**落下去的产物读不回来**。
    """
    ctx = _plan_ctx(_book())
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    ctx.record_edge(UPSTREAM, InputEdge(
        file=ctx.edges[UPSTREAM].file, consumed=None))
    ctx.ledger.record(node=ctx.node, sid=ctx.sid, model='m', code=ctx.code,
                      cached=False, cache_policy='default')     # 响应没给 usage ⇒ token 三项 null
    artifact = _plan(ctx)
    # consumed=null 会让正式稿的自新鲜度只能是 unknown；正式盘口现在必须拒绝。
    with pytest.raises(ProvenanceRefused, match='freshness 后置条件'):
        st.write(PLAN_REL, artifact)
    assert not st.exists(PLAN_REL), '后置条件失败后新正式稿没有回滚'
    p = st.write_invalid(PLAN_REL, artifact, gates_skipped=[])

    raw = json.loads(p.read_text(encoding='utf-8'))
    assert raw['_provenance']['inputs'][UPSTREAM]['consumed'] is None, \
        '`consumed` 的键被抹掉了——「必填但可为 null」与「三态字段」语义相反，不许一个开关同管'
    assert raw['_provenance']['usage']['prompt_tokens'] is None, \
        '`null` = 这次响应没给 usage，**不是 0**；抹掉这个键，产物就再也加载不回来'
    assert raw['_schema_version'] == artifact_spec(PLAN_REL).current_version
    PLAN_MODEL.model_validate(decode_artifact(PLAN_REL, raw).payload)


def test_a_tampered_schema_version_is_refused_on_the_real_read_path():
    """⭐⭐ 版本闸在**真 store 路径**上也拦得住，不只在合成 dict 上。

    ⚠ 这条曾叫 `test_v2_provenance_artifact_remains_readable_and_fresh_after_v3_upgrade`，
    断言的正好相反：把版本号改成 2 之后**照样读得回来、照样 fresh**——那是迁移梯子的
    配套用例。梯子拆掉之后（`contracts/schema/__init__.py` 模块头【版本】），
    同一个场景的正确结论是**拒读**。

    ⚠ 与 `tests/test_artifact_versions.py` 那两条参数化的分界线：那边拿合成 dict 扫
    注册表全部产物的分母，这边走 `ArtifactStore.write` → 真文件 → `read_own` 的整条路，
    查的是「闸真的装在读盘口上」而不是只装在解码函数里。

    ⚠ 版本号**从注册表现算**：本条查的是「篡改版本号就读不回来」，与当前是第几版无关。
    写死 `1`/`2` 时，将来一次合法的 `current_version` +1 会让本条一起红——而那一天真正
    该红的只有 `tests/test_artifact_versions.py` 里那条「全表恒 v1」的反向守卫。
    """
    base = _book()
    ctx = _plan_ctx(base)
    store = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    official = store.write(PLAN_REL, _plan(ctx))
    current = artifact_spec(PLAN_REL).current_version
    encoded = json.loads(official.read_text(encoding='utf-8'))
    assert encoded['_schema_version'] == current, '写盘口吐的不是当前版本'

    # 先证明原样能读回来——否则下面那条拒读证明不了是版本号造成的。
    assert ArtifactStore(_plan_ctx(base)).read_own(
        PLAN_MODEL, PLAN_REL)[0].entries[0].name.family == '计划一'

    encoded['_schema_version'] = current + 1
    official.write_text(
        json.dumps(encoded, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='',
    )
    with pytest.raises(ArtifactUnreadable,
                       match=f'schema v{current + 1} 不是当前 v{current}'):
        ArtifactStore(_plan_ctx(base)).read_own(PLAN_MODEL, PLAN_REL)


def _spend(ctx: RunContext, **tokens) -> None:
    """记一次真调用。`tokens` 里没给的那几项 = 这次响应没报它们。"""
    ctx.ledger.record(node=ctx.node, sid=ctx.sid, model='m', code=ctx.code,
                      cached=False, cache_policy='default', **tokens)


def _mint(ctx: RunContext) -> LlmProvenance:
    """铸一份付费来源账。**只为了拿它的 `usage`**，别的字段取最小合法值。"""
    return ctx.llm_provenance(
        LlmProvenance, prompts={'TEST_SYS': 'x'}, model='m', adopted_models=['m'])


def test_paid_provenance_refuses_to_guess_the_actual_model_from_primary_config():
    ctx = _plan_ctx(_book())
    with pytest.raises(ProvenanceRefused, match='没有 accepted model'):
        ctx.llm_provenance(
            LlmProvenance, prompts={'TEST_SYS': 'x'}, model='deepseek-flash')


def test_each_artifact_gets_its_own_usage_not_the_whole_runs_total():
    """⭐⭐ `_provenance.usage` 答的是「**这一份**花了多少」，不是「这次跑花了多少」。

    ⚠ 这里原来直接写 `ledger.usage()`，而那是 **run 累计**。一次跑产 N 份产物
    （stage5 逐章正是这个形状）时，第 2 份带着第 1 份的账、第 3 份带着前两份的，
    一路加上去 —— 而**返工回路的边际成本**正是要从这一格里读的，从此读不出来，
    账面还看着都对。两份账答的是两个问题（`_common.md` §八）：
    账本答「这周/这本书花了多少」，产物里那一份答「这一份花了多少」。
    """
    ctx = _plan_ctx(_book())
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


def test_durable_aggregate_can_override_usage_without_reusing_current_mark():
    """跨 CLI 聚合按 adopted attempts 重建 usage，但仍须结算本进程刻度。"""
    ctx = _plan_ctx(_book())
    _spend(ctx, prompt_tokens=10, completion_tokens=5)
    aggregate = Usage(
        calls_made=3, calls_cached=0,
        prompt_tokens=33, completion_tokens=12, reasoning_tokens=None)

    first = ctx.llm_provenance(
        LlmProvenance, prompts={'TEST_SYS': 'x'}, model='m',
        adopted_models=['m'], usage_override=aggregate)
    _spend(ctx, prompt_tokens=7, completion_tokens=2)
    second = _mint(ctx)

    assert first.usage == aggregate
    assert second.usage is not None
    assert (second.usage.calls_made, second.usage.prompt_tokens) == (1, 7), \
        'usage override 没有推进当前 mark，下一份又认领了同一次调用'


def test_every_artifacts_usage_sums_back_to_the_run_total():
    """守卫的守卫：按份切开之后，**各份之和必须等于账本的总量**。

    差一分钱就说明有一段窗口的账**谁都没认领**（或者被认领了两次）。
    """
    ctx = _plan_ctx(_book())
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
    ctx = _plan_ctx(_book())
    _spend(ctx, prompt_tokens=10)              # ← 只报了这一项
    u = _mint(ctx).usage
    assert u.prompt_tokens == 10
    assert u.completion_tokens is None and u.reasoning_tokens is None, \
        '没报过的那两项被写成了 0——「没给」与「真的没花」混掉了'


def test_the_same_usage_mark_can_never_be_committed_twice():
    """同一把刻度提交两次 = 两份产物各自认领同一批 token。**显式终态，不是一笔悄悄重复的账。**"""
    led = CostLedger()
    mark = led.snapshot()
    led.record(node=PLAN_NODE, sid='1', model='m', code='c',
               cached=False, cache_policy='default', prompt_tokens=3)
    assert led.usage_since(mark).prompt_tokens == 3
    led.commit(mark)
    with pytest.raises(ProvenanceRefused, match='结算了两次'):
        led.commit(mark)


def test_a_failed_mint_does_not_push_the_usage_mark():
    """⭐ **推刻度必须在构造成功之后。**

    来源账是 strict schema；传入退役的 ``attempts`` 字段时构造会抛。
    抛了还把刻度推走，那段窗口的账就再也没有产物认领它——
    Σ（每份）== run 总量 当场破，而破得**一声不响**。
    """
    ctx = _plan_ctx(_book())
    _spend(ctx, prompt_tokens=10)
    with pytest.raises(Exception):
        ctx.llm_provenance(
            LlmProvenance, prompts={'S': 'x'}, model='m',
            adopted_models=['m'], attempts=[])
    # 刻度没动 ⇒ 下一份产物照样认领得到这 10 个 token
    assert _mint(ctx).usage.prompt_tokens == 10


def test_a_run_with_no_calls_still_has_no_usage_key():
    """0 成本节点是**键不在**，不是一份全 0 的 usage——按份结算之后这条仍然成立。"""
    ctx = _ctx(_book(), paid=False)
    assert ctx.free_provenance().usage is None


def test_three_state_keys_are_absent_on_disk_not_null():
    """三态那四个键：`None` ⇒ **键不在**。`revalidated: null` 与「没有这个键」是两份字节。"""
    ctx = _plan_ctx(_book())
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    d = _prov_on_disk(st.write(PLAN_REL, _plan(ctx, origin='human')))
    for k in ('revalidated', 'gates_skipped', 'input_basis', 'usage'):
        assert k not in d, f'{k} 是 None，不该出现在盘上'
    # 非 None 的三态键必须原样落盘（`origin='human'` 是显式人工轮次的声明）。
    assert d['origin'] == 'human'


def test_the_write_gate_refuses_prompts_without_inputs():
    """⚠⚠ **原型这里是 `print` 一句警告然后照写。**

    代价：`chapter_blueprints` 漏记 global · `style_grounding` 与 `fidelity_reference`
    的 `inputs` 干脆是空的。一份自称能证明来源的记录悄悄漏掉一个来源，
    **比没有这个记录更坏**——下游会拿它当证据。
    """
    ctx = _plan_ctx(_book())                    # ← 一次 read 都没有 ⇒ inputs 为空
    with pytest.raises(ProvenanceRefused, match='inputs'):
        ArtifactStore(ctx).write(PLAN_REL, _plan(ctx))


def test_a_free_run_cannot_mint_a_provenance_that_names_a_model():
    """⚠ 「0 成本不许记 `model`」这道闸开在**铸造口**，不开在写盘口。

    理由是实测的：写盘口拦不住它，因为若干 0 成本路径本来就要写回一份带 `model` 的
    来源账（`--revalidate` / `--recheck` / `stage6.approve` /
    `stage7.approve` 改写的都是**付费节点产的**那份产物，它记的 model 属于当初那次付费产出）。
    ⇒ 真正该拦的是「0 成本的跑**现造**一份声称模型产了它的账」，而现造只有一个入口。
    """
    ctx = _ctx(_book(), paid=False)
    with pytest.raises(ProvenanceRefused, match='不花钱'):
        ctx.llm_provenance(LlmProvenance, prompts={'TEST_SYS': 'x'}, model='m')
    # 0 成本的那一份**结构上**没有 model 这个字段
    free = ctx.free_provenance(prompts={'RULES': '判据表全文'})
    assert 'model' not in free.to_disk() and 'model' not in type(free).model_fields


def test_a_failed_write_never_touches_the_existing_artifact(monkeypatch):
    """**先验证再 replace**：往返校验没过 ⇒ 一个字节都不落盘。

    ⚠ `open(p,'w')` 是「先截断再写」：在它写完之前，盘上那份是**半截 JSON**。
    原型并发落盘时踩过——半截 JSON 的坏处不是读失败，是**读失败会被当成「上游还没产出」**。
    """
    ctx = _plan_ctx(_book())
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    p = st.write(PLAN_REL, _plan(ctx, marker='第一版'))
    before = p.read_bytes()

    def boom(*_a, **_k):
        raise ProvenanceRefused('故意让往返校验失败')

    monkeypatch.setattr(ArtifactStore, '_verify_round_trip', staticmethod(boom))
    with pytest.raises(ProvenanceRefused):
        st.write(PLAN_REL, _plan(ctx, marker='第二版'))

    assert p.read_bytes() == before, '旧产物被截断/覆盖了——那正是原子写要防的那一瞬间'
    assert not list(p.parent.glob('*.tmp')), '临时文件没清掉'


def test_self_freshness_failure_restores_official_and_keeps_invalid():
    base = _book()
    ctx = _plan_ctx(base)
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    official = st.write(PLAN_REL, _plan(ctx, marker='原正式稿'))
    before = official.read_bytes()
    invalid = st.write_invalid(
        PLAN_REL, _plan(ctx, marker='待修问题稿'), gates_skipped=[])
    invalid_before = invalid.read_bytes()

    edge = ctx.edges[UPSTREAM]
    ctx.record_edge(UPSTREAM, InputEdge(file=edge.file, consumed=None))
    with pytest.raises(ProvenanceRefused, match='freshness 后置条件'):
        st.write(PLAN_REL, _plan(ctx, marker='来源账写错的新稿'))

    assert official.read_bytes() == before, '后置条件失败没有逐字节恢复旧正式稿'
    assert invalid.read_bytes() == invalid_before, '正式稿还没自证 fresh 就退休了 INVALID'


def test_retiring_invalid_failure_rolls_the_formal_write_back(monkeypatch):
    base = _book()
    ctx = _plan_ctx(base)
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    official = st.write(PLAN_REL, _plan(ctx, marker='原正式稿'))
    before = official.read_bytes()
    invalid = st.write_invalid(
        PLAN_REL, _plan(ctx, marker='问题稿'), gates_skipped=[])
    invalid_before = invalid.read_bytes()

    def fail_retirement(_rel):
        raise RuntimeError('simulated retirement failure')

    monkeypatch.setattr(st, 'retire_invalid', fail_retirement)
    with pytest.raises(RuntimeError, match='retirement failure'):
        st.write(PLAN_REL, _plan(ctx, marker='本来合格的新稿'))
    assert official.read_bytes() == before
    assert invalid.read_bytes() == invalid_before


def test_deterministic_self_fresh_policy_only_guards_artifacts_that_can_self_prove_now():
    assert STAGE2_BLUEPRINT_CHARACTER_PROJECTION in SELF_FRESH_DETERMINISTIC, (
        '称名投影是现役唯一的写后自证产物')
    assert STAGE1_MAIN_TEXT not in SELF_FRESH_DETERMINISTIC
    assert STAGE1_HOOK_COPY not in SELF_FRESH_DETERMINISTIC
    assert STAGE0_REVISIONS not in SELF_FRESH_DETERMINISTIC, \
        'revision 链只按根 source 身份判 freshness，不用写后自证策略'
    assert STAGE2_GLOBAL_REVIEW not in SELF_FRESH_DETERMINISTIC, \
        'global_review 已随语义审阅退役，不再有自证 freshness'


def test_json_pointer_diff_uses_rfc6901_escaping_and_stable_order():
    before = {'z': 1, 'a/b': {'~key': ['same', 'old']}}
    after = {'z': 2, 'a/b': {'~key': ['same', 'new']}}
    assert _json_pointer_diff(before, after) == ('/a~1b/~0key/1', '/z')


def test_json_artifacts_are_written_without_cr():
    ctx = _plan_ctx(_book())
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    assert b'\r' not in st.write(PLAN_REL, _plan(ctx)).read_bytes()


# ─────────────────────────────────────────────────────────────────────────────
# 四、字节形态产物：成书无 `\r`、无 BOM
# ─────────────────────────────────────────────────────────────────────────────

def _stage0_on_disk(book: Path, source: Path, source_bytes: bytes, clean_bytes: bytes) -> None:
    """stage0 的权威 transform + 唯一 clean 投影。"""
    import hashlib
    (book / 'stage0').mkdir(exist_ok=True)
    (book / 'stage0' / 'clean_text.txt').write_bytes(clean_bytes)
    from contracts.schema.stage0 import ExternalFileRef, Stage0Source
    from novel.stages.stage0.revisions import make_transform
    text = source_bytes.decode('utf-8')
    clean = clean_bytes.decode('utf-8')
    transform = make_transform(
        source=Stage0Source(
            source_ref=ExternalFileRef(
                kind='external_file', absolute_path=str(source.resolve())),
            raw_sha256=hashlib.sha256(source_bytes).hexdigest(),
            encoding='utf-8',
            normalized_source_sha1=prov.content_sha1(source_bytes),
            total_code_points=len(text),
        ),
        code='test-store',
        clean_text=clean,
        removed=[],
    )
    _write_payload(book, 'stage0/revisions.json', transform.model_dump(mode='json'))


def test_deterministic_artifacts_use_their_existing_content_fingerprints_for_freshness():
    """stage0×2、Stage1 两份材料与成书不再结构性 `unknown`。

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

    partitioned = partition_text(clean_bytes.decode('utf-8'))
    _write_payload(
        book, 'stage1/main_text.json', partitioned.main_text.model_dump(mode='json'))
    _write_payload(
        book, 'stage1/hook_copy.json', partitioned.hook_copy.model_dump(mode='json'))

    book_bytes = '1\n第一章。\n\n2\n第二章。\n'.encode('utf-8')
    # 成书按书名命名 ⇒ 取生产那一个落点。⚠ 手写 `stage7/book.txt` 的后果**不是红**：
    #   那条 rel 不再是任何产物，确定性 freshness 桥够不着它，于是它会被当成一份普通
    #   JSON 去解码 ⇒ 报「不是合法 JSON」，把人指向错的层。
    book_rel = paths.book_txt_rel(book)
    (book / 'stage7' / 'final').mkdir(parents=True)
    (book / book_rel).write_bytes(book_bytes)
    # F8b-3：新链成书身份由 assembly 交付清单承载（`final_text_sha256`，
    # manifest 最后提交；旧 name_map.book_txt 桥随旧链退役）。
    (book / STR06_DELIVERY_MANIFEST).write_text(json.dumps({
        'delivery_identity': 'd' * 64,
        'final_text_sha256': hashlib.sha256(book_bytes).hexdigest(),
        'chapters': [],
    }, ensure_ascii=False), encoding='utf-8')

    store = ArtifactStore(_ctx(base, paid=False))
    formerly_unknown = (
        'stage0/revisions.json',
        'stage0/clean_text.txt',
        'stage1/main_text.json', 'stage1/hook_copy.json',
        book_rel,
    )
    assert {rel: store.freshness(rel).state for rel in formerly_unknown} == {
        rel: 'fresh' for rel in formerly_unknown}

    _write_payload(book, 'stage1/hook_copy.json', {'text': partitioned.hook_copy.text + '篡改'})
    assert store.freshness('stage1/hook_copy.json').state == 'stale'
    _write_payload(
        book, 'stage1/hook_copy.json', partitioned.hook_copy.model_dump(mode='json'))
    changed_main = partitioned.main_text.model_dump(mode='json')
    changed_main[0]['text'] += '篡改'
    _write_payload(book, 'stage1/main_text.json', changed_main)
    assert store.freshness('stage1/main_text.json').state == 'stale'
    _write_payload(
        book, 'stage1/main_text.json', partitioned.main_text.model_dump(mode='json'))

    (book / 'stage0' / 'clean_text.txt').write_bytes(clean_bytes + b'changed\n')
    for rel in formerly_unknown[1:4]:
        assert store.freshness(rel).state == 'stale', rel
    assert store.freshness(book_rel).state == 'fresh'
    # ⚠ **权威格不跟着投影动。** `clean_text.txt` 是 `revisions.json` 的物化投影，
    #   在它**下游**：投影被人动了不代表这条链不是照盘上那份原文建的。
    #   「clean live 还是不是 `current` 的投影」由判据 `s0_revision_chain` 回答；
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


def test_the_chain_identity_comes_from_the_single_root_source():
    """源身份只在链根保存一次，不随 revision 复制或漂移。"""
    base = _book(with_clean=False)
    book = base / '书名_123'
    source_bytes = '开篇。\n\n1\n第一章。\n'.encode('utf-8')
    live_source = base / 'source.txt'
    live_source.write_bytes(source_bytes)
    source_identity = {
        'source_ref': {'kind': 'external_file',
                       'absolute_path': str(live_source.resolve())},
        'normalized_source_sha1': prov.content_sha1(source_bytes),
        'total_code_points': len(source_bytes.decode('utf-8')),
    }
    (book / 'stage0').mkdir()
    (book / 'stage0' / 'clean_text.txt').write_bytes(source_bytes)
    _write_payload(book, 'stage0/revisions.json', {
        'source': source_identity,
        'revisions': [
            {'revision_id': 'b' * 64, 'parent_revision_id': None, 'origin': 'machine',
             'content': {'clean_text': source_bytes.decode('utf-8'), 'removed': []}},
            {'revision_id': 'c' * 64, 'parent_revision_id': 'b' * 64,
              'origin': 'human_edit',
              'content': {'clean_text': source_bytes.decode('utf-8'), 'removed': []}},
        ],
        'current': {'revision_id': 'c' * 64, 'approval': {'status': 'pending'}},
        'candidate_revision_id': None,
        'dismissed': [],
    })
    store = ArtifactStore(_ctx(base, paid=False))
    report = store.freshness('stage0/revisions.json')
    assert (report.state, report.reason) == ('fresh', 'match')
    assert [edge.key for edge in report.edges] == [str(live_source.resolve())]


def test_every_produced_artifact_without_provenance_reaches_the_deterministic_bridge():
    """⭐ **本条守的就是 `stage0/revisions.json` 那次漏接本身。**

    没有通用 `_provenance` 槽位的产物只能靠 `_deterministic_freshness` 那座桥回答
    新鲜度。漏接一条的后果**不是报错**，是它在完成度视图里恒
    `unknown/no_provenance`——一份判得出新鲜度的产物被显示成「没有来源账」，
    而这正是新增产物时最容易忘的一步（阶段 C 就忘了）。

    ⚠ 作用域是「节点 `produces` 的、且真会落进完成度 `artifacts[]` 的那些」：
      · `*_edit.json` / `wontfix.json` 这类**人写的输入**不是任何节点的产出，
        它们没有来源账是本分，不该被这条逼着去造一个；
      · 新链 str06 家族的 JSON 产物由 `Str06Repository.write` 落盘即注入
        `_provenance`（不管 schema 槽位），freshness 走普通来源账路；
      · 正文类 text 产物（`stage5/body/ch{nn}.txt` 是逐章 pattern，不进本条
        分母）之外，落进完成度的 text 产物必须自己接桥。
    """
    from contracts.invariants import NODES
    from contracts.schema import ARTIFACT_SPECS
    from novel.store import candidate_artifact
    from novel.orchestrator import state as state_view
    from novel.store import PROVENANCE_KEY

    base = _book()
    book = base / '书名_123'
    store = ArtifactStore(_ctx(base, paid=False))

    def carries_provenance(rel: str) -> bool:
        candidate = candidate_artifact(rel)
        if candidate is not None:
            # repository.write 落盘即注入来源账；正文（text）的账在绑定它的回执上。
            return not candidate.endswith('.txt')
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


# ─────────────────────────────────────────────────────────────────────────────
# 四之二、stage0 源身份那条边：**记录的位置优先，base 重锚只是回落**
# ─────────────────────────────────────────────────────────────────────────────

_SOURCE_BYTES = '开篇。\n\n1\n第一章。\n\n2\n第二章。\n'.encode('utf-8')


def _stage0_book(recorded_source: Path,
                  base: Path | None = None) -> tuple[Path, ArtifactStore]:
    """一本只有 stage0 的书，revision 根的 `source_ref` 按 `recorded_source` 的形状写。

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
    _write_payload(book, STAGE0_REVISIONS, {
        'source': {
            'source_ref': source_ref_for(recorded_source).model_dump(mode='json'),
            'normalized_source_sha1': decode_source_bytes(_SOURCE_BYTES).normalized_source_sha1,
            'total_code_points': len(_SOURCE_BYTES.decode('utf-8')),
        },
        'revisions': [{
            'revision_id': 'a' * 64,
            'parent_revision_id': None,
            'origin': 'machine',
            'content': {'clean_text': clean_bytes.decode('utf-8'), 'removed': []},
        }],
        'current': {'revision_id': 'a' * 64, 'approval': {'status': 'pending'}},
        'candidate_revision_id': None,
        'dismissed': [],
    })
    return base, ArtifactStore(_ctx(base, paid=False))


def _source_edge(store: ArtifactStore):
    """revision 权威链唯一的原文上游边。"""
    edges = list(store.freshness(STAGE0_REVISIONS).edges)
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
    assert store.freshness(STAGE0_REVISIONS).state == 'fresh'


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
    assert store.freshness(STAGE0_REVISIONS).state == 'unknown'


def test_stage0_source_edge_still_goes_ghost_for_an_external_corpus_that_is_gone():
    """外部语料被删就是**世界真的变了**：`ghost` 是正确答案，不许"救"。

    ⚠ 契约有意接受这个代价（`docs/schema/stage0.md`：文件被移走时是「不知道」，
      不是「不匹配」）。被剔正文仍由外部原始 txt 保存，Stage0 不复制第二份。
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
    """⚠ 两份字节形态产物（`clean_text.txt` / 成书）盘上**全是 LF-only**，
    而 Windows 上 `open(p, 'w')` 默认吐 CRLF ⇒ 同一份代码在两台机器上产出两种字节。
    """
    ctx = _ctx(_book())
    p = ArtifactStore(ctx).write_text(STAGE0_CLEAN_TEXT, '第一章\n正文\n')
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
        ArtifactStore(ctx).write_text(STAGE0_CLEAN_TEXT, '第一章\r\n正文\r\n')


def test_read_text_does_not_translate_newlines_either():
    """两侧口径必须一致：写的一侧拒 `\\r`，读的一侧就不许把 `\\r\\n` 悄悄翻成 `\\n`
    ——否则「盘上那份带 CRLF」这件事在读的一侧看不见。"""
    base = _book()
    (base / '书名_123' / STAGE0_CLEAN_TEXT).write_bytes(b'a\r\nb\n')
    assert ArtifactStore(_ctx(base)).read_text(STAGE0_CLEAN_TEXT) == 'a\r\nb\n'


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
    (base / '书名_123' / STAGE0_CLEAN_TEXT).write_bytes(b'\xff\xfe\x00\x41')
    ctx = _ctx(base)
    with pytest.raises(ArtifactUnreadable, match='UTF-8'):
        ArtifactStore(ctx).read_text(STAGE0_CLEAN_TEXT)
    assert ctx.edges == {}, '一份一个字都没读到的文件，在来源账里留下了一条边'


def test_a_non_utf8_json_artifact_still_says_it_is_not_utf8_not_bad_json():
    """两条报错必须分得开：**不是 UTF-8** 与 **不是合法 JSON** 是两种不同的坏法。

    合成一句之后，「盘上这份编码错了」会被当成「模型吐了段坏 JSON」去查。
    """
    base = _book(payload=b'\xff\xfe\x00\x41')
    with pytest.raises(ArtifactUnreadable, match='UTF-8'):
        ArtifactStore(_ctx(base))._validated_read(Loose, UPSTREAM)
    base2 = _book(payload=b'{"sections": [')
    with pytest.raises(ArtifactUnreadable, match='合法 JSON'):
        ArtifactStore(_ctx(base2))._validated_read(Loose, UPSTREAM)


# ─────────────────────────────────────────────────────────────────────────────
# 五、`.INVALID`、`gates_skipped`、提升路
# ─────────────────────────────────────────────────────────────────────────────

def _write_invalid(base: Path, gates: list[str], *, marker: str = '计划一') -> tuple[RunContext, Path]:
    ctx = _plan_ctx(base)
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    return ctx, st.write_invalid(
        PLAN_REL, _plan(ctx, marker=marker), gates_skipped=gates)


def test_gates_skipped_is_written_even_when_empty():
    """⚠ **空列表也要写。**

    空列表 = 这次没有闸门缺料；键不在 = 这份产物早于这套机制。
    它是判据 36 那整套设计的**唯一痕迹**，而「闸门没跑成」与「闸门跑过没事」
    正是 `Q-2` 要求必须能区分的两件事。
    """
    _ctx_, inv = _write_invalid(_book(), [])
    assert inv.name == 'name_map.INVALID.json'
    assert _prov_on_disk(inv)['gates_skipped'] == []


def test_invalid_does_not_overwrite_the_official_artifact():
    """`Q-5` 先校验后落盘：阻断级错误**不覆盖已有产物**。"""
    base = _book()
    ctx = _plan_ctx(base)
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    good = st.write(PLAN_REL, _plan(ctx, marker='合格的那一版'))
    before = good.read_bytes()
    st.write_invalid(PLAN_REL, _plan(ctx, marker='被拦下的那一版'), gates_skipped=['name_leak'])
    assert good.read_bytes() == before


def _byte_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob('*') if path.is_file()
    }


def test_write_invalid_rolls_back_live_rounds_and_baselines_together(monkeypatch):
    """A replacement INVALID must not pre-archive its predecessor on IO failure."""
    base = _book()
    ctx = _plan_ctx(base)
    store = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    store.write_invalid(
        PLAN_REL, _plan(ctx, marker='第一份问题稿'), gates_skipped=['name_leak'])
    stage = store.path(PLAN_REL).parent
    before = _byte_tree(stage)

    def fail_baseline(_self, _destination, _data):
        raise OSError('模拟新基线写入失败')

    monkeypatch.setattr(ArtifactStore, '_write_immutable', fail_baseline)
    with pytest.raises(OSError, match='基线写入失败'):
        store.write_invalid(
            PLAN_REL, _plan(ctx, marker='第二份问题稿'), gates_skipped=['name_leak'])

    assert _byte_tree(stage) == before


def test_write_round_rolls_back_formal_invalid_and_round_history(monkeypatch):
    """Post-write freshness failure restores the whole pre-archive byte set."""
    base = _book()
    ctx = _plan_ctx(base)
    store = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    store.write(PLAN_REL, _plan(ctx, marker='原正式稿'))
    store.write_invalid(
        PLAN_REL, _plan(ctx, marker='原问题稿'), gates_skipped=['name_leak'])
    stage = store.path(PLAN_REL).parent
    before = _byte_tree(stage)
    original = ArtifactStore._assert_self_fresh

    def fail_new_round(self, rel, artifact):
        if rel == PLAN_REL:
            raise ProvenanceRefused('模拟后置 freshness 失败')
        return original(self, rel, artifact)

    monkeypatch.setattr(ArtifactStore, '_assert_self_fresh', fail_new_round)
    with pytest.raises(ProvenanceRefused, match='freshness 失败'):
        store.write_round(PLAN_REL, _plan(ctx, marker='新正式稿'))

    assert _byte_tree(stage) == before


def test_history_transaction_restores_a_late_bound_delivery_retirement():
    """Content-addressed byte archives must join the caller's transaction journal."""
    base = _book()
    store = ArtifactStore(_plan_ctx(base))
    book_rel = paths.book_txt_rel(store.ctx.novel_dir)
    live = store.path(book_rel)
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_bytes('旧交付物\n'.encode('utf-8'))
    before = _byte_tree(live.parent)
    digest = prov.content_sha1(live.read_bytes())

    with pytest.raises(RuntimeError, match='companion map failed'):
        with store.history_transaction(book_rel):
            store.retire_bytes(
                book_rel, f'{paths.rounds_dir_of(book_rel)}/delivery.{digest}.txt')
            raise RuntimeError('companion map failed')

    assert _byte_tree(live.parent) == before


def test_the_official_write_path_refuses_an_invalid_name():
    ctx = _plan_ctx(_book())
    with pytest.raises(PathRefused, match='write_invalid'):
        ArtifactStore(ctx).write(paths.invalid_of(PLAN_REL), _plan(ctx))


def test_a_new_official_draft_retires_the_old_invalid_sibling():
    """⭐ **回归**：合格的新稿落盘 ⇒ 同名 `.INVALID` 进归档，不留在盘上。

    ⚠⚠ 留下来的后果不是「多一个文件」，是**四个只看兄弟文件在不在的消费方一起说假话**：
    语料库把一本已经出书的书报成 `broken` · 续跑判定去**重跑并重新付费**一个
    已经有新鲜正式稿的节点（实测发生过，stage6.hook 被多发了一次）·
    人定稿盖不上审批章 · 下游整条链被 `inbox:invalid` 挡住。
    ⚠ **归档不是删除**（`Q-8`）：那份稿花过钱，而且可能还被别人的 `inputs` 指着。
    """
    base = _book()
    ctx = _plan_ctx(base)
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    inv = st.write_invalid(PLAN_REL, _plan(ctx, marker='被拦下的那一版'),
                           gates_skipped=['name_leak'])
    assert inv.is_file()

    good = st.write(PLAN_REL, _plan(ctx, marker='修好之后的那一版'))

    assert good.is_file(), '正式稿要落盘'
    assert not inv.exists(), '.INVALID 兄弟必须让位——留着它，四个消费方一起说假话'
    archived = good.parent / paths.ROUNDS_DIRNAME
    kept = sorted(p.name for p in archived.iterdir())
    assert kept and all('INVALID' in name for name in kept), \
        f'被拦下的那一份要留在 _rounds/ 里，不是删掉：{kept}'


def test_refreshing_formal_derived_fields_preserves_the_live_invalid_sibling():
    """recheck 刷正式稿指标不等于采用/取代一份尚待 revalidate 的问题稿。"""
    base = _book()
    ctx = _plan_ctx(base)
    store = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    formal = store.write(PLAN_REL, _plan(ctx, marker='旧派生读数'))
    invalid = store.write_invalid(
        PLAN_REL, _plan(ctx, marker='尚待重验证的付费候选'),
        gates_skipped=['name_leak'],
    )
    invalid_before = invalid.read_bytes()
    stage_before = _byte_tree(formal.parent)

    refreshed = store.refresh_formal(
        PLAN_REL, _plan(ctx, marker='按当前判据刷新的派生读数'))

    assert refreshed == formal
    assert invalid.is_file(), 'refresh 后 live INVALID 必须仍在普通 revalidate 入口'
    assert invalid.read_bytes() == invalid_before, 'refresh 不得改写问题稿的任何字节'
    stage_after = _byte_tree(formal.parent)
    changed = {
        rel for rel in set(stage_before) | set(stage_after)
        if stage_before.get(rel) != stage_after.get(rel)
    }
    assert changed == {formal.name}, (
        f'refresh 只能改正式文件，不得归档 INVALID 或改写其审计历史：{changed}')
    current, _raw = store.read_own(PLAN_MODEL, PLAN_REL)
    assert current.entries[0].name.family == '按当前判据刷新的派生读数'


def test_refresh_formal_refuses_to_create_a_missing_official():
    """refresh 不是首写旁路；没有 formal 时不得制造 formal + INVALID 新状态。"""
    base = _book()
    ctx = _plan_ctx(base)
    store = ArtifactStore(ctx)
    _record_upstream_edge(ctx)

    with pytest.raises(UpstreamMissing, match='name_map.json'):
        store.refresh_formal(PLAN_REL, _plan(ctx, marker='不应落盘'))

    assert not store.path(PLAN_REL).exists()


def test_refresh_formal_refuses_to_rewrite_generation_provenance():
    """确定性复检只能继承来源账，不能借 refresh 冒充一次新生成。"""
    base = _book()
    ctx = _plan_ctx(base)
    store = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    store.write(PLAN_REL, _plan(ctx, marker='原正式稿'))
    store.write_invalid(
        PLAN_REL, _plan(ctx, marker='尚待重验证的问题稿'), gates_skipped=[])
    before = _byte_tree(store.path(PLAN_REL).parent)
    candidate = _plan(ctx, marker='伪造来源账的刷新')
    changed_src = candidate.provenance.model_copy(update={'code': 'other-code'})
    candidate = candidate.model_copy(update={'provenance': changed_src})

    with pytest.raises(ProvenanceRefused, match='逐字段继承'):
        store.refresh_formal(PLAN_REL, candidate)

    assert _byte_tree(store.path(PLAN_REL).parent) == before


def test_refresh_formal_freshness_failure_restores_the_whole_byte_tree(monkeypatch):
    """refresh 复用正式写盘口的 freshness 后置条件与逐字节回滚。"""
    base = _book()
    ctx = _plan_ctx(base)
    store = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    store.write(PLAN_REL, _plan(ctx, marker='原正式稿'))
    store.write_invalid(
        PLAN_REL, _plan(ctx, marker='仍在 live 的问题稿'), gates_skipped=[])
    stage = store.path(PLAN_REL).parent
    before = _byte_tree(stage)

    def fail_freshness(_rel, _artifact):
        raise ProvenanceRefused('模拟 refresh freshness 失败')

    monkeypatch.setattr(store, '_assert_self_fresh', fail_freshness)
    with pytest.raises(ProvenanceRefused, match='refresh freshness 失败'):
        store.refresh_formal(PLAN_REL, _plan(ctx, marker='不会留下的刷新'))

    assert _byte_tree(stage) == before


def test_a_rejected_official_write_does_not_retire_the_invalid_sibling(monkeypatch):
    """新内容连完整预校验都过不了时，旧问题稿必须仍留在原位。"""
    base = _book()
    ctx = _plan_ctx(base)
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    inv = st.write_invalid(
        PLAN_REL, _plan(ctx, marker='仍待修的问题稿'), gates_skipped=['name_leak'])
    before = inv.read_bytes()

    def boom(*_args, **_kwargs):
        raise ProvenanceRefused('故意让新正式稿预校验失败')

    monkeypatch.setattr(ArtifactStore, '_verify_round_trip', staticmethod(boom))
    with pytest.raises(ProvenanceRefused, match='预校验失败'):
        st.write(PLAN_REL, _plan(ctx, marker='结构坏掉的新稿'))

    assert inv.read_bytes() == before
    assert not (inv.parent / paths.ROUNDS_DIRNAME).exists()


def test_an_atomic_replace_failure_restores_the_just_retired_invalid(monkeypatch):
    base = _book()
    ctx = _plan_ctx(base)
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    inv = st.write_invalid(
        PLAN_REL, _plan(ctx, marker='仍待修的问题稿'), gates_skipped=['name_leak'])
    before = inv.read_bytes()
    original = ArtifactStore._replace_atomically

    def fail_official(path, data):
        if path == st.path(PLAN_REL):
            raise OSError('模拟正式稿原子替换失败')
        return original(path, data)

    monkeypatch.setattr(ArtifactStore, '_replace_atomically', staticmethod(fail_official))
    with pytest.raises(OSError, match='原子替换失败'):
        st.write(PLAN_REL, _plan(ctx, marker='已预校验的新稿'))

    assert inv.read_bytes() == before
    rounds = inv.parent / paths.ROUNDS_DIRNAME
    assert not rounds.exists() or not any(rounds.iterdir())


def test_retiring_the_invalid_sibling_is_a_no_op_when_there_is_none():
    """没有问题稿时 `None` 表示「本来就没有」，不是「归档失败」。"""
    base = _book()
    ctx = _plan_ctx(base)
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    assert st.retire_invalid(PLAN_REL) is None
    st.write(PLAN_REL, _plan(ctx))
    assert not (st.path(PLAN_REL).parent / paths.ROUNDS_DIRNAME).exists(), \
        '没有问题稿就不该凭空造一个归档目录'


def test_promotion_keeps_the_original_phase_bindings_and_archives_the_invalid():
    """⭐⭐ 提升路两件事，各对应一次实测损失。

    ① **不许伪造**：`--revalidate` 不得改写对付费 checkpoint 的完整字节绑定或当前 inputs，
       `round` 也不该凭空 +1，因为**没有任何一次新规划发生过**。
    ② **不许删**（`Q-8`）：原型「读完问题稿就把它删了」造出的正是盘上那条幽灵边
       ——另一份产物的 `inputs` 还指着它。**删一个还被别人指着的文件，会同时制造两个问题。**
    """
    base = _book()
    # bind 的付费上游：Preserve checkpoint 先落盘，binding 以完整字节 SHA 绑定它
    #（新链唯一等价于旧链 compiled 计划 A/B 完整字节绑定的身份字段）。
    checkpoint_sha = _seed_preserve_checkpoint(base)
    checkpoint_rel = STR06_PRESERVE_CHECKPOINT
    ctx = _ctx(base, node='str06.stage4.bind')
    st = ArtifactStore(ctx)
    raw = (base / '书名_123' / checkpoint_rel).read_bytes()
    binding = _binding(ctx, checkpoint_sha=checkpoint_sha, inputs={
        checkpoint_rel: {
            'file': prov.fingerprint_bytes(raw),
            'consumed': prov.projection_fingerprint(artifact_payload(
                decode_artifact(checkpoint_rel, json.loads(raw.decode('utf-8'))).payload)),
        }})
    inv = st.write_invalid(
        'stage4/grounding/semantic_binding.json', binding, gates_skipped=['name_leak'])
    was = _prov_on_disk(inv)
    was_payload = json.loads(inv.read_text(encoding='utf-8'))
    invalid_bytes = inv.read_bytes()
    locator = was['invalid_baseline_sha256']
    baseline = next((inv.parent / '_revalidation').glob(
        f'*.{locator}.snapshot'))

    # 提升走的是一次 **0 成本**的跑（`--revalidate` 在 `FREE_ENTRIES` 里）
    ctx2 = _ctx(base, node='str06.stage4.bind', paid=False)
    out = ArtifactStore(ctx2).promote_invalid(
        'stage4/grounding/semantic_binding.json', SemanticBinding, **REVALIDATION)
    now = _prov_on_disk(out)
    now_payload = json.loads(out.read_text(encoding='utf-8'))

    assert now.get('prompts') == was.get('prompts')
    assert 'model' not in now and 'model' not in was, '确定性编译结果不得冒充第三次模型调用'
    assert (now_payload['preserve_checkpoint_sha256']
            == was_payload['preserve_checkpoint_sha256']), \
        '提升不得改写对付费 checkpoint 的完整字节绑定'
    assert now['inputs'] == was['inputs']
    assert now['revalidated'] is True
    assert now['revalidation_audit']['actor'] == REVALIDATION['actor']
    assert now['revalidation_audit']['raw_changed'] is False
    assert now['revalidation_audit']['content_changed'] is False
    assert now['revalidation_audit']['edited_by_human'] is False
    assert now['revalidation_audit']['changed_json_pointers'] == []
    assert 'gates_skipped' not in now, '提升上来的是正式产物，不该再挂缺料记录'
    assert now['code'] == ctx2.code, 'code 必须记本次重判真正使用的代码版本'

    assert not inv.exists()
    archived = sorted(p.name for p in
                      (base / '书名_123' / 'stage4' / 'grounding' / '_rounds').iterdir())
    assert archived == ['semantic_binding.INVALID.r1.json'], \
        '⚠ 原型归档叫 `*.INVALID.superseded.json`（**没有编号**），第二次会静默覆盖第一次'
    numbered = inv.parent / '_rounds' / 'semantic_binding.INVALID.r1.json'
    assert numbered.read_bytes() == invalid_bytes, '正式 numbered 归档的读取语义不得改变'
    reference = json.loads(baseline.read_text(encoding='utf-8'))
    assert reference == {
        '_snapshot_ref_version': 1,
        'raw_sha256': hashlib.sha256(invalid_bytes).hexdigest(),
        'round_rel': 'stage4/grounding/_rounds/semantic_binding.INVALID.r1.json',
    }
    assert baseline.stat().st_size < len(invalid_bytes), \
        '未编辑 INVALID 应只留可验证内容引用，不再复制整份 baseline'


def test_promotion_rebuilds_a_registered_unreadable_formal_without_losing_history():
    """盘上正式稿字节不可读（外来 payload）时，提升不得崩溃、不得销毁历史。

    旧链的 shape-break 重建分支依赖版本阶梯（v1 formal < v7 current）；新链
    现役 str06 产物全部 v1，不可读正式稿按「当前业务轮次」先归档再原位重建，
    提升照样收敛，两份历史都保得住。
    """
    base = _book()
    _ctx_, inv = _write_invalid(base, [], marker='当前 v1 问题稿')
    formal = inv.with_name('name_map.json')
    formal.write_bytes(b'{"_schema_version":1,"legacy":"retired plan"}\n')

    out = ArtifactStore(_plan_ctx(base, paid=False)).promote_invalid(
        PLAN_REL, PLAN_MODEL, **REVALIDATION)

    assert json.loads(out.read_text(encoding='utf-8'))['_schema_version'] == \
        artifact_spec(PLAN_REL).current_version
    assert not inv.exists()
    archived = sorted(p.name for p in (formal.parent / paths.ROUNDS_DIRNAME).iterdir())
    assert archived == ['name_map.INVALID.r1.json', 'name_map.r1.json'], \
        '不可读的旧正式稿也是花过钱的盘上历史，归档后重建，不许静默覆盖'


def test_revalidation_records_immutable_baseline_hashes_and_json_pointer_diff():
    base = _book()
    _ctx_, inv = _write_invalid(base, [], marker='机器原稿')
    provenance = _prov_on_disk(inv)
    locator = provenance['invalid_baseline_sha256']
    baselines = list((inv.parent / '_revalidation').glob(f'*.{locator}.snapshot'))
    assert len(baselines) == 1
    baseline = baselines[0]
    baseline_before = baseline.read_bytes()
    assert baseline_before == inv.read_bytes(), '基线不是 INVALID 创建时的完整原始字节'

    edited = json.loads(inv.read_text(encoding='utf-8'))
    edited['entries'][0]['name']['family'] = '人工修正'
    inv.write_text(json.dumps(edited, ensure_ascii=False, indent=4) + '\n', encoding='utf-8')

    store = ArtifactStore(_plan_ctx(base, paid=False))
    audit = store.revalidation_audit(
        PLAN_REL, PLAN_MODEL, **REVALIDATION)
    assert audit.raw_changed is True and audit.content_changed is True
    assert audit.edited_by_human is True
    assert audit.changed_json_pointers == ('/entries/0/name/family',)
    assert audit.baseline_raw_sha256 != audit.candidate_raw_sha256
    assert audit.baseline_canonical_sha256 != audit.candidate_canonical_sha256

    out = store.promote_invalid(PLAN_REL, PLAN_MODEL, **REVALIDATION)
    disk_audit = _prov_on_disk(out)['revalidation_audit']
    assert disk_audit == audit.model_dump(mode='json')
    assert baseline.read_bytes() == baseline_before, '提升过程改写了不可变基线'
    archived = inv.parent / '_rounds' / 'name_map.INVALID.r1.json'
    assert archived.read_bytes() != baseline_before, \
        '人工编辑后的 numbered 轮次与创建基线不是重复内容，两份证据都必须保留'


def test_revalidation_distinguishes_raw_only_edits_from_logical_edits():
    base = _book()
    _ctx_, inv = _write_invalid(base, [])
    payload = json.loads(inv.read_text(encoding='utf-8'))
    payload['_provenance']['code'] = 'manually-touched-provenance-only'
    inv.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                   encoding='utf-8')

    audit = ArtifactStore(_plan_ctx(base, paid=False)).revalidation_audit(
        PLAN_REL, PLAN_MODEL, **REVALIDATION)
    assert audit.raw_changed is True
    assert audit.content_changed is False
    assert audit.edited_by_human is False
    assert audit.changed_json_pointers == ()


def test_promotion_override_rebuilds_derived_fields_but_cannot_replace_live_audit_or_source():
    base = _book()
    _ctx_, inv = _write_invalid(base, [], marker='机器原稿')
    baseline_source = _prov_on_disk(inv)
    current = json.loads(inv.read_text(encoding='utf-8'))
    current['entries'][0]['name']['family'] = '人工提交'
    current['entries'][0]['aliases'] = {'alias_01': '人工正文修订'}
    current_source = current['_provenance']
    current_source['code'] = 'live-invalid-forged-code'
    current_source['root_clean_sha1'] = '0' * 40
    current_source['inputs'] = {
        UPSTREAM: {'file': '0' * 10, 'consumed': None},
    }
    current_source['prompts'] = {'RULES': '1' * 10}
    current_source['input_basis'] = 'live-invalid-forged-input-basis'
    inv.write_text(json.dumps(current, ensure_ascii=False, indent=2) + '\n',
                   encoding='utf-8')
    live = PLAN_MODEL.model_validate(decode_artifact(PLAN_REL, current).payload)
    fake_source = live.provenance.model_copy(update={'code': 'fake-override-code', 'inputs': {}})
    rebuilt_payload = live.model_dump(by_alias=True, mode='json')
    rebuilt_payload['entries'][0]['name'][
        'family'] = '确定性派生字段已重建'
    rebuilt_payload['_provenance'] = fake_source.model_dump(by_alias=True, mode='json')
    rebuilt = PLAN_MODEL.model_validate(rebuilt_payload)

    store = ArtifactStore(_plan_ctx(base, paid=False))
    live_audit = store.revalidation_audit(
        PLAN_REL, PLAN_MODEL, **REVALIDATION)
    out = store.promote_invalid(
        PLAN_REL, PLAN_MODEL, artifact_override=rebuilt, **REVALIDATION)
    disk = json.loads(out.read_text(encoding='utf-8'))
    source = disk['_provenance']

    assert disk['entries'][0]['name'][
        'family'] == '确定性派生字段已重建'
    assert disk['entries'][0]['aliases'] == {'alias_01': '人工正文修订'}
    assert source['revalidation_audit'] == live_audit.model_dump(mode='json')
    assert source['revalidation_audit']['changed_json_pointers'] == [
        '/entries/0/aliases/alias_01',
        '/entries/0/name/family',
    ]
    promotion_owned = {
        'code', 'revalidated', 'gates_skipped', 'invalid_baseline_sha256',
        'revalidation_audit',
    }
    assert {
        key: value for key, value in source.items() if key not in promotion_owned
    } == {
        key: value for key, value in baseline_source.items() if key not in promotion_owned
    }, '正式产物的原始来源账没有完整继承不可变创建基线'
    assert source['code'] not in {'live-invalid-forged-code', 'fake-override-code'}


@pytest.mark.parametrize(('actor', 'reason'), [('', '理由'), ('人', '  ')])
def test_revalidation_rejects_blank_human_credentials_without_touching_files(actor, reason):
    base = _book()
    _ctx_, inv = _write_invalid(base, [])
    before = inv.read_bytes()
    store = ArtifactStore(_plan_ctx(base, paid=False))
    with pytest.raises(ApprovalRequired, match='actor.*reason'):
        store.promote_invalid(
            PLAN_REL, PLAN_MODEL, actor=actor, reason=reason)
    assert inv.read_bytes() == before
    assert not store.exists(PLAN_REL)


def test_revalidation_refuses_a_tampered_immutable_baseline_before_writing():
    base = _book()
    _ctx_, inv = _write_invalid(base, [])
    locator = _prov_on_disk(inv)['invalid_baseline_sha256']
    baseline = next((inv.parent / '_revalidation').glob(f'*.{locator}.snapshot'))
    damaged = json.loads(baseline.read_text(encoding='utf-8'))
    damaged['entries'][0]['name']['family'] = '基线被改'
    baseline.write_text(json.dumps(damaged, ensure_ascii=False, indent=2) + '\n',
                        encoding='utf-8')

    store = ArtifactStore(_plan_ctx(base, paid=False))
    with pytest.raises(ProvenanceRefused, match='内容校验失败'):
        store.promote_invalid(PLAN_REL, PLAN_MODEL, **REVALIDATION)
    assert inv.is_file() and not store.exists(PLAN_REL)


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

    store = ArtifactStore(_plan_ctx(base, paid=False))
    with pytest.raises(ProvenanceRefused, match='没有 write_invalid 记录的不可变基线定位键'):
        store.promote_invalid(PLAN_REL, PLAN_MODEL, **REVALIDATION)

    assert inv.read_bytes() == before
    assert not store.exists(PLAN_REL)


def test_first_write_invalid_creates_a_nested_snapshot_near_windows_max_path():
    root = Path(tempfile.mkdtemp())
    locator = '0' * 64
    empty_old = (root / '_123' / 'stage7' / 'naming' / '_revalidation' /
                 f'name_map.INVALID.{locator}.snapshot')
    # Reproduce the acceptance budget: the old target-derived temporary name
    # and even the long legacy target reach MAX_PATH, while every ordinary
    # input/output directory remains comfortably shorter.
    title = '长' * max(8, 264 - len(str(empty_old)))
    base = _book(title=title, root=root)
    ctx = _plan_ctx(base)
    store = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    revalidation = store.path(PLAN_REL).parent / '_revalidation'
    assert not revalidation.exists()

    inv = store.write_invalid(PLAN_REL, _plan(ctx), gates_skipped=[])
    locator = _prov_on_disk(inv)['invalid_baseline_sha256']
    snapshots = list(revalidation.glob(f'*.{locator}.snapshot'))
    assert len(snapshots) == 1
    assert snapshots[0].name == f'b.{locator}.snapshot'
    assert snapshots[0].read_bytes() == inv.read_bytes()

    legacy = revalidation / f'name_map.INVALID.{locator}.snapshot'
    legacy_tmp = legacy.with_name(f'{legacy.name}.{os.getpid()}.tmp')
    assert len(str(legacy)) >= 260, '夹具没有复现真实验收的 Windows 路径预算'
    assert len(str(snapshots[0])) + 20 < len(str(legacy_tmp))


def test_failed_first_snapshot_write_leaves_no_partial_or_live_invalid(monkeypatch):
    base = _book()
    ctx = _plan_ctx(base)
    store = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    invalid = store.path(paths.invalid_of(PLAN_REL))
    revalidation = invalid.parent / '_revalidation'
    original = os.replace

    def fail_snapshot(source, destination):
        if '_revalidation' in os.fspath(destination):
            raise OSError('模拟不可变快照原子替换失败')
        return original(source, destination)

    monkeypatch.setattr(os, 'replace', fail_snapshot)
    with pytest.raises(OSError, match='快照原子替换失败'):
        store.write_invalid(PLAN_REL, _plan(ctx), gates_skipped=[])

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
    artifact = store.read_own(PLAN_MODEL, paths.invalid_of(PLAN_REL))[0]
    locator = _prov_on_disk(inv)['invalid_baseline_sha256']
    baseline = next((inv.parent / '_revalidation').glob(f'*.{locator}.snapshot'))
    baseline.write_bytes(b'tampered immutable history')
    live_before = inv.read_bytes()

    with pytest.raises(ProvenanceRefused, match='拒绝覆盖审计历史'):
        store.write_invalid(PLAN_REL, artifact, gates_skipped=[])

    assert baseline.read_bytes() == b'tampered immutable history'
    assert inv.read_bytes() == live_before


def test_a_second_promotion_lands_in_the_next_round_not_on_top_of_the_first():
    """第二次被拦下的稿归档到 `r2`，**不覆盖 `r1`**；同时**上一轮正式稿也进 `_rounds`**。

    ⚠ 这一条挡的是原型那个 `*.INVALID.superseded.json`（无编号）：
    第二次会静默覆盖第一次。轮次由 `seen_rounds` 扫出来，缺号也不会退回去。

    ⚠ 两份 `.INVALID` **必须内容不同**（`marker`），否则第二次提升渲染出的字节
    与盘上正式稿逐字节相同、归档那一步按设计跳过，这条断言就变成靠巧合绿的。
    """
    base = _book()
    _write_invalid(base, [], marker='第一版')
    p = ArtifactStore(_plan_ctx(base, paid=False)).promote_invalid(
        PLAN_REL, PLAN_MODEL, **REVALIDATION)
    first = p.read_bytes()
    _write_invalid(base, ['name_leak'], marker='第二版')
    ArtifactStore(_plan_ctx(base, paid=False)).promote_invalid(
        PLAN_REL, PLAN_MODEL, **REVALIDATION)

    rounds = base / '书名_123' / 'stage7' / 'naming' / '_rounds'
    archived = sorted(q.name for q in rounds.iterdir())
    assert archived == ['name_map.INVALID.r1.json', 'name_map.INVALID.r2.json',
                        'name_map.r1.json']
    assert (rounds / 'name_map.r1.json').read_bytes() == first, \
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
    ctx0 = _plan_ctx(base)
    st0 = ArtifactStore(ctx0)
    _record_upstream_edge(ctx0)
    official = st0.write(PLAN_REL, _plan(ctx0, marker='付过钱的那一版'))
    was = official.read_bytes()

    _write_invalid(base, ['name_leak'], marker='被拦下的那一版')
    out = ArtifactStore(_plan_ctx(base, paid=False)).promote_invalid(
        PLAN_REL, PLAN_MODEL, **REVALIDATION)

    rounds = base / '书名_123' / 'stage7' / 'naming' / '_rounds'
    assert (rounds / 'name_map.r1.json').read_bytes() == was, \
        '旧正式稿被原子替换销毁了，而 `_rounds/` 里没有任何副本'
    assert (rounds / 'name_map.INVALID.r1.json').exists(), '`.INVALID` 那条历史也要归档'
    assert json.loads(out.read_text(encoding='utf-8'))[
        'entries'][0]['name']['family'] == '被拦下的那一版'
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
    _write_invalid(base, [], marker='唯一那一版')
    ArtifactStore(_plan_ctx(base, paid=False)).promote_invalid(
        PLAN_REL, PLAN_MODEL, **REVALIDATION)

    rounds = base / '书名_123' / 'stage7' / 'naming' / '_rounds'
    # 模拟「正式稿已落、`.INVALID` 还没挪走」那半个刻意选的窗口
    inv = base / '书名_123' / 'stage7' / 'naming' / 'name_map.INVALID.json'
    (rounds / 'name_map.INVALID.r1.json').rename(inv)
    ArtifactStore(_plan_ctx(base, paid=False)).promote_invalid(
        PLAN_REL, PLAN_MODEL, **REVALIDATION)

    archived = sorted(q.name for q in rounds.iterdir())
    assert archived == ['name_map.INVALID.r1.json'], \
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
    ctx = _plan_ctx(base, paid=False)
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    art = FreeArtifact(note='0 成本算出来的', _provenance=ctx.free_provenance(prompts={'RULES': '判据表'}))
    st.write_invalid(PLAN_REL, art, gates_skipped=['name_leak'])

    out = ArtifactStore(_plan_ctx(base, paid=False)).promote_invalid(
        PLAN_REL, FreeArtifact, **REVALIDATION)
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
    rounds = base / '书名_123' / 'stage7' / 'naming' / '_rounds'
    rounds.mkdir(parents=True)
    (rounds / 'name_map.INVALID.r1.json').write_bytes(b'{"taken": true}')

    st = ArtifactStore(_plan_ctx(base, paid=False))
    monkeypatch.setattr(ArtifactStore, '_round_on_disk', lambda self, rel: 1)
    with pytest.raises(PathRefused, match='一个字节都没动'):
        st.promote_invalid(PLAN_REL, PLAN_MODEL, **REVALIDATION)
    assert inv.exists(), '`.INVALID` 被挪走了'
    assert not st.exists(PLAN_REL), '正式稿落下去了，而调用方拿到的是一个异常'
    assert sorted(q.name for q in rounds.iterdir()) == ['name_map.INVALID.r1.json'], \
        '归档目录变了——有人把「归档旧正式稿」挪到了占位检查之前'


def test_promotion_without_an_invalid_is_a_terminal_state():
    with pytest.raises(UpstreamMissing):
        ArtifactStore(_plan_ctx(_book(), paid=False)).promote_invalid(
            PLAN_REL, PLAN_MODEL, **REVALIDATION)


# ─────────────────────────────────────────────────────────────────────────────
# 六、轮次与归档
# ─────────────────────────────────────────────────────────────────────────────

def test_archiving_never_overwrites_an_existing_round():
    """**归档是出事之后唯一能回去的那一份**，不许静默覆盖。

    轮次号算错（`len(glob)+1` 那个式子的典型症状）时，这条是唯一会出声的地方。
    """
    base = _book()
    ctx = _plan_ctx(base)
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    st.write(PLAN_REL, _plan(ctx, marker='第一轮'))
    st.archive_round(PLAN_REL)                       # → _rounds/name_map.r1.json
    st.write(PLAN_REL, _plan(ctx, marker='第二轮'))
    with pytest.raises(PathRefused, match='归档目标已存在'):
        st.archive_round(PLAN_REL, round_no=1)


def test_seen_rounds_counts_the_invalid_and_the_gaps():
    """`round` = **盘上见过的最大轮次 + 1**，含 `.INVALID` 与 `_rounds/`，缺号也算。"""
    base = _book()
    ctx = _plan_ctx(base)
    st = ArtifactStore(ctx)
    rounds = base / '书名_123' / 'stage7' / 'naming' / '_rounds'
    rounds.mkdir(parents=True)
    (rounds / 'name_map.r1.json').write_bytes(b'{}')
    (rounds / 'name_map.r3.json').write_bytes(b'{}')       # ← r2 被人挪走了
    assert st.seen_rounds(PLAN_REL) == {1, 3}
    assert st.next_round(PLAN_REL) == 4, 'len(glob)+1 会返回 3，与现存的 r3 撞号'


def test_archive_can_keep_the_original_when_the_layer_needs_it():
    """`keep=True` 与默认的「挪走」是**语义差别，不是风格差别**：
    逐章成稿归档之后当前文件必须消失（下一轮马上写一份新的到同一路径），
    而「先抄一份再原地改写」的那几层需要它留着。
    """
    base = _book()
    ctx = _plan_ctx(base)
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    p = st.write(PLAN_REL, _plan(ctx))
    st.archive_round(PLAN_REL, keep=True)
    assert p.exists()
    st.archive_round(PLAN_REL, round_no=2)
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
    ctx = _plan_ctx(base)
    st = ArtifactStore(ctx)
    _record_upstream_edge(ctx)
    st.write(PLAN_REL, _plan(ctx))
    assert st.freshness(PLAN_REL).state == 'fresh'

    (base / '书名_123' / UPSTREAM).unlink()
    r = st.freshness(PLAN_REL)
    assert (r.state, r.reason) == ('unknown', 'ghost')
    assert r.ghosts == (UPSTREAM,), '不点名 = 人无从判断这条边要不要重新挂钩'


def test_freshness_reads_raw_and_survives_an_artifact_that_no_longer_validates():
    """⚠ **判新鲜度不过模型。**

    一份陈旧产物很可能已经不合当前 schema（新契约裁了一批字段之后，
    原型产的 3/3 本全部加载失败），而「它是不是陈旧的」恰恰是这时候最该答得出来的问题。
    让新鲜度依赖校验通过，等于在最需要它的那一刻把它关掉。
    """
    base = _book()
    st = ArtifactStore(_plan_ctx(base))
    (base / '书名_123' / 'stage7' / 'naming').mkdir(parents=True, exist_ok=True)
    root_clean_sha1 = prov.content_sha1(
        (base / '书名_123' / 'stage0' / 'clean_text.txt').read_bytes())
    malformed = {
        '这份产物不合当前业务契约': True,
        '_provenance': {
            'root_clean_sha1': root_clean_sha1,
            'inputs': {UPSTREAM: {'file': 'ffffffffff', 'consumed': None}},
        },
    }
    (base / '书名_123' / PLAN_REL).write_text(
        json.dumps(encode_artifact(PLAN_REL, malformed), ensure_ascii=False),
        encoding='utf-8',
    )
    r = st.freshness(PLAN_REL)
    assert (r.state, r.reason) == ('stale', 'file_changed')
    with pytest.raises(ArtifactUnreadable):
        st.read_own(PLAN_MODEL, PLAN_REL)        # ← 同一份，过模型就是读不出来


def test_a_bad_key_in_current_provenance_does_not_blow_up_the_report():
    """当前产物里的来源键仍可能损坏。**别让一条坏键把整份报告干掉**——
    「有一条边指向已删文件」不该升级成「报告出不来」。"""
    base = _book()
    st = ArtifactStore(_plan_ctx(base))
    (base / '书名_123' / 'stage7' / 'naming').mkdir(parents=True, exist_ok=True)
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
    (base / '书名_123' / PLAN_REL).write_text(
        json.dumps(encode_artifact(PLAN_REL, malformed)), encoding='utf-8')
    r = st.freshness(PLAN_REL)
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
        led.record(node=PLAN_NODE, sid='1', model='m', code='c',
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
    led.record(node=PLAN_NODE, sid='1', model='m', code='c',
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
    assert ctx.stage == 'stage4'


def test_opening_a_book_that_is_not_there_is_a_terminal_state():
    with pytest.raises(UpstreamMissing, match='先跑 stage1'):
        RunContext.open(Path(tempfile.mkdtemp()), '没有这本书', 'str06.stage4.compile',
                        paid=False, write_ledger=False)


# ─────────────────────────────────────────────────────────────────────────────
# 十、⭐ 当前 manifest 全部真实产物走一遍**落盘往返** —— 七个现役层一次全过，0 成本
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
    它是 `be3e6da` 为旧链 `stage4.plan` 借自己的 ctx 去写 `stage4/writer_context.json`
    那个 P0 加的（现场是一份刚组好的产物当场 `stale/consumed_changed`）；新链
    依赖同一条闸守住「写盘方 ctx 与产地方一致」。
    ⇒ 这里只放行 `unknown` 家族，`stale` **原样抛、原样回滚、原样用生产的措辞**。

    ⚠⚠ **`unknown` 那一半在生产里仍然是被拒的，别照抄这段到别处。**
    它抓得住 `consumed_undeclared`（漏登记投影）——`novel/consumption.py:352-356`
    那条注释就是它抓到过一次的现场（`STAGE2_GLOBAL_EDIT` 那行投影是被它逼出来的），
    而读侧 `_refuse_stale_upstream` 对 `unknown` 是放行的，写侧一放宽就没人守了。
    ⇒ 豁免的作用域上界就是本文件；它由
    `test_the_waiver_switches_off_nothing_but_the_unknown_half` 钉着。

    ⚠ **先调生产那一个落点再判**，不复制那条「没有 `_provenance` 且不在
    `SELF_FRESH_DETERMINISTIC` 里就早退」的判断。复制一份的代价是具体的：
    `stage0/revisions.json` 走 `_stage0_revisions_freshness()`，它会 resolve 真实源文件
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
        only_the_unknown_half_of_the_freshness_postcondition_is_waived,
        tmp_path):
    """⭐ **豁免的两侧各钉一次 —— 这条是上面那个 fixture 的守卫本体。**

    用 names 产物（其唯一上游 `stage1/main_text.json` 有注册投影，边判得出来）
    造两侧：

    ① `unknown`（正文不在盘上，边 ghost）⇒ **放行**，这是豁免的全部作用面；
    ② `stale`（上游变了）⇒ **照旧拒，且正式路径逐字节回滚**。
    """
    rel = PLAN_REL
    main_text = [{'chapter_ordinal': 1, 'text': '甲乙掌柜都在。'}]

    def seeded(base):
        book = base / '书名_123'
        _write_payload(book, STAGE1_MAIN_TEXT, main_text)
        # names 产物按现役 producer 的形状钉住当前正文边；写盘后置条件才有 stale 可言。
        ctx = _ctx(base, node=PLAN_NODE, paid=True)
        store = ArtifactStore(ctx)
        plan = _plan(ctx, marker='计划', inputs={
            STAGE1_MAIN_TEXT: {
                'file': prov.fingerprint_bytes((book / STAGE1_MAIN_TEXT).read_bytes()),
                'consumed': store._consumed_fp_of_key(PLAN_NODE, STAGE1_MAIN_TEXT),
            }})
        written = store.write(rel, plan)
        return store, book, plan, written.read_bytes()

    # ① unknown：唯一上游（正文）被移走 ⇒ 下游边 ghost ⇒ 豁免放行
    base = _book()
    store, book, plan, plan_bytes = seeded(base)
    (book / STAGE1_MAIN_TEXT).unlink()
    assert store.freshness(rel).state == 'unknown'
    store.write(rel, plan)
    assert (book / rel).read_bytes() == plan_bytes, '豁免写完必须保持原字节'

    # ② stale：写完之后动上游（正文）⇒ 消费投影变化 ⇒ 照旧拒，且回滚
    base2 = _book()
    store2, book2, plan2, plan_bytes2 = seeded(base2)
    official = book2 / rel
    # 改掉正文唯一章的内容 ⇒ stage2_main_text 重算确实变化 ⇒ stale 家族照旧拒。
    changed = [
        {**row, 'text': '甲乙都在院里说话。'} if row['chapter_ordinal'] == 1
        else row for row in main_text]
    _write_payload(book2, STAGE1_MAIN_TEXT, changed)
    assert store2.freshness(rel).state == 'stale'

    with pytest.raises(ProvenanceRefused, match='freshness 后置条件'):
        store2.write(rel, plan2)
    assert official.read_bytes() == plan_bytes2, \
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
    加载体检全绿，而第一份真落盘就会炸。

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
        node = producer_node(a.formal_rel) or PLAN_NODE
        ctx = RunContext.open(box.root, box.sid, node,
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
    a = next((x for x in _CORPUS if x.formal_rel == PLAN_REL), None)
    corpus.declared_dependency(
        a is not None, needs=f'任意一本书的 {PLAN_REL}',
        produced_by=PLAN_NODE, cost='付费')
    obj = a.model.model_validate(corpus.load_current(a))

    box = sandbox.Sandbox(a.book_dir, ())
    try:
        ctx = RunContext.open(box.root, box.sid, PLAN_NODE,
                              paid=True, write_ledger=False)
        # ① `.INVALID` 路径闸（`novel/store.py:548-551`）：豁免不该让它放行。
        with pytest.raises(PathRefused):
            ArtifactStore(ctx).write(paths.invalid_of(a.formal_rel), obj)

        # ② 全链根比对（`novel/store.py:1426-1429`）：把根改一个字节，写盘口必须当场拒。
        #    ⚠ 要重开 `RunContext`——`root_clean_sha1` 是 run 级固化的，沿用旧 ctx
        #    测到的是「它记住了改之前那份」，不是这道闸。
        root = box.dir / sandbox.CHAIN_ROOT
        root.write_bytes(root.read_bytes() + '　'.encode('utf-8'))
        moved = RunContext.open(box.root, box.sid, PLAN_NODE,
                                paid=True, write_ledger=False)
        with pytest.raises(ProvenanceRefused, match='拒绝混根'):
            ArtifactStore(moved).write(a.formal_rel, obj)
    finally:
        box.close()
