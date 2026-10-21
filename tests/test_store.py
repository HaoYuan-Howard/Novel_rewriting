"""Stage0/1合法载体与通用持久化反例；三层故事来源与发布见test_story_store。"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest
from pydantic import RootModel

from contracts.artifacts import STAGE0_CLEAN_TEXT, STAGE0_REVISIONS, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT
from contracts.schema import InputEdge, LlmProvenance, Usage, artifact_spec, decode_artifact, encode_artifact
from contracts.schema.stage1 import MainText
from novel import paths, prov, store as store_module
from novel.consumption import project_consumed
from novel.errors import ArtifactUnreadable, ByteFormRefused, PathRefused, ProvenanceRefused, UpstreamMissing
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, ledger_path
from novel.orchestrator import state as orchestrator_state
from novel.orchestrator.uploads import save_upload
from novel.run import RunContext
from novel.store import ArtifactStore

MT_REL, MT_NODE = STAGE1_MAIN_TEXT, 'str06.stage2.book'
UPSTREAM, PLAN_NODE = STAGE1_HOOK_COPY, 'str06.stage2.book'
_SOURCE_BYTES = '开篇。\n\n1\n第一章。\n\n2\n第二章。\n'.encode('utf-8')
_ROWS = [{'chapter_ordinal': 1, 'text': '第一章正文。'}, {'chapter_ordinal': 2, 'text': '第二章正文。'}]

class Loose(RootModel[Any]):
    """测试唯一读取、memo与消费边；业务schema另走MainText合法载体。"""


def _write_payload(book, rel, payload):
    path = book / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(encode_artifact(rel, payload), ensure_ascii=False).encode('utf-8'))


def _book(sid='123', title='书名', payload=None, *, with_clean=True, root=None):
    base = root if root is not None else Path(tempfile.mkdtemp())
    from novel.work_units import initialize_workflow
    initialize_workflow(base)
    book = base / f'{title}_{sid}'
    (book / 'stage1').mkdir(parents=True)
    if with_clean:
        (book / 'stage0').mkdir()
        (book / STAGE0_CLEAN_TEXT).write_bytes(b'fixture clean root\n')
    _write_payload(book, MT_REL, _ROWS)
    if payload is None:
        _write_payload(book, UPSTREAM, {'text': '开篇文案。'})
    else:
        try:
            logical = json.loads(payload.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            (book / UPSTREAM).write_bytes(payload)
        else:
            _write_payload(book, UPSTREAM, logical)
    return base


def _ctx(base, sid='123', node='str06.stage4.compile', *, paid=False, write_ledger=False):
    return RunContext.open(base, sid, node, paid=paid, write_ledger=write_ledger)


def _plan_ctx(base, sid='123', *, paid=True):
    return _ctx(base, sid, PLAN_NODE, paid=paid)


def _record_upstream_edge(ctx, *, rel=UPSTREAM):
    raw = (ctx.novel_dir / rel).read_bytes()
    value = decode_artifact(rel, json.loads(raw.decode('utf-8'))).payload
    resolved, projected = project_consumed(ctx.node, rel, value, related={})
    assert resolved
    ctx.record_edge(rel, InputEdge(file=prov.fingerprint_bytes(raw),
                                  consumed=prov.projection_fingerprint(projected)))


def _stage0_book(recorded_source, base=None):
    from contracts.schema.stage0 import Stage0Source
    from novel.core.source import source_ref_for
    from novel.stages.stage0.revisions import make_transform
    base = _book(with_clean=False, root=base)
    book = base / '书名_123'
    (book / 'stage0').mkdir()
    text = _SOURCE_BYTES.decode('utf-8')
    transform = make_transform(source=Stage0Source(source_ref=source_ref_for(recorded_source),
        raw_sha256=hashlib.sha256(_SOURCE_BYTES).hexdigest(), encoding='utf-8',
        normalized_source_sha1=prov.content_sha1(_SOURCE_BYTES), total_code_points=len(text)),
        code='test-store', clean_text=text, removed=[])
    _write_payload(book, STAGE0_REVISIONS, transform.model_dump(mode='json'))
    (book / STAGE0_CLEAN_TEXT).write_bytes(_SOURCE_BYTES)
    return base, ArtifactStore(_ctx(base))


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
        assert second.root == _ROWS
        assert second_payload == _ROWS


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
        assert second.root == _ROWS
        assert calls == 1

    # A public invocation gets a new byte/scalar memo and recomputes against its
    # own sealed surface; no consumed result crosses the invocation boundary.
    with store_module.readonly_snapshot_memo(book, code='code-a'):
        ArtifactStore(ctx).read(Loose, MT_REL)
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
            ctx.record_artifact_root('stage4/drafts/book.json', '7' * 40)
            raise RuntimeError('guard stopped')

    assert ctx.edges == {'stage1/hook_copy.json': original_edge}
    assert ctx.judged == {'stage5/body/ch01.txt': '3' * 10}
    assert ctx._observed_roots == {
        'stage2/observations/book.json': ctx.root_clean_sha1,
    }


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


def test_a_free_run_cannot_mint_a_provenance_that_names_a_model():
    """⚠ 「0 成本不许记 `model`」这道闸开在**铸造口**，不开在写盘口。

    理由是实测的：写盘口拦不住它，因为若干 0 成本路径本来就要写回一份带 `model` 的
    来源账（`--recheck` / `stage6.approve` /
    `stage7.approve` 改写的都是**付费节点产的**那份产物，它记的 model 属于当初那次付费产出）。
    ⇒ 真正该拦的是「0 成本的跑**现造**一份声称模型产了它的账」，而现造只有一个入口。
    """
    ctx = _ctx(_book(), paid=False)
    with pytest.raises(ProvenanceRefused, match='不花钱'):
        ctx.llm_provenance(LlmProvenance, prompts={'TEST_SYS': 'x'}, model='m')
    # 0 成本的那一份**结构上**没有 model 这个字段
    free = ctx.free_provenance(prompts={'RULES': '判据表全文'})
    assert 'model' not in free.to_disk() and 'model' not in type(free).model_fields


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


def _stage1_store(tmp_path):
    base = _book(root=tmp_path)
    return ArtifactStore(_ctx(base, node='stage1.partition'))


def _main(marker='第一章正文。'):
    return MainText.model_validate([{'chapter_ordinal': 1, 'text': marker}, _ROWS[1]])


def _tree(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob('*') if path.is_file()}


def test_stage1_real_write_roundtrip_and_exact_read_own_hash_do_not_record_self_edge(tmp_path):
    store = _stage1_store(tmp_path)
    path = store.write(MT_REL, _main())
    value, digest = store.read_own_with_sha256(MainText, MT_REL)
    assert value == _main() and store.ctx.edges == {}
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert b'\r' not in path.read_bytes()
    assert decode_artifact(MT_REL, json.loads(path.read_bytes())).payload == _main().model_dump(mode='json')


def test_stage1_tampered_schema_is_rejected_on_actual_read_path(tmp_path):
    store = _stage1_store(tmp_path)
    path = store.write(MT_REL, _main())
    payload = json.loads(path.read_bytes())
    payload['_schema_version'] = artifact_spec(MT_REL).current_version + 1
    path.write_bytes(json.dumps(payload).encode('utf-8'))
    with pytest.raises(ArtifactUnreadable, match='不是当前'):
        store.read_own(MainText, MT_REL)


def test_missing_json_encoding_json_shape_and_schema_failures_stay_distinct(tmp_path):
    store = _stage1_store(tmp_path)
    path = store.path(MT_REL)
    path.unlink()
    with pytest.raises(UpstreamMissing):
        store.read_own(MainText, MT_REL)
    for data, reason in ((b'\xff', 'UTF-8'), (b'{', '合法 JSON')):
        path.write_bytes(data)
        with pytest.raises(ArtifactUnreadable, match=reason):
            store.read_own(MainText, MT_REL)
    _write_payload(store.ctx.novel_dir, MT_REL, [{'chapter_ordinal': 8, 'text': '只有错序一章'}])
    with pytest.raises(ArtifactUnreadable):
        store.read_own(MainText, MT_REL)
    assert store.ctx.edges == {}


def test_stage1_failed_prevalidation_never_touches_official_or_creates_temp(tmp_path, monkeypatch):
    store = _stage1_store(tmp_path)
    path = store.write(MT_REL, _main())
    original = path.read_bytes()
    def fail(*_args):
        raise ProvenanceRefused('注入往返校验失败')
    monkeypatch.setattr(ArtifactStore, '_verify_round_trip', staticmethod(fail))
    with pytest.raises(ProvenanceRefused, match='往返校验失败'):
        store.write(MT_REL, _main('第二版'))
    assert path.read_bytes() == original and not list(path.parent.glob('*.tmp'))


def test_stage1_failed_postcondition_restores_official_and_evidence_bytes(tmp_path, monkeypatch):
    store = _stage1_store(tmp_path)
    path = store.write(MT_REL, _main())
    invalid = store.path(paths.invalid_of(MT_REL))
    invalid.write_bytes(b'preserved diagnostic bytes')
    before = _tree(path.parent)
    def fail(*_args):
        raise ProvenanceRefused('注入写后校验失败')
    monkeypatch.setattr(store, '_assert_self_fresh', fail)
    with pytest.raises(ProvenanceRefused, match='写后校验失败'):
        store.write(MT_REL, _main('不应保留的新内容'))
    assert _tree(path.parent) == before


def test_stage1_failed_invalid_retirement_restores_entire_transaction(tmp_path, monkeypatch):
    store = _stage1_store(tmp_path)
    path = store.write(MT_REL, _main())
    store.path(paths.invalid_of(MT_REL)).write_bytes(b'diagnostic')
    before = _tree(path.parent)
    def fail(*_args):
        raise OSError('注入证据归档失败')
    monkeypatch.setattr(store, 'retire_invalid', fail)
    with pytest.raises(OSError, match='证据归档失败'):
        store.write(MT_REL, _main('归档失败后必须还原'))
    assert _tree(path.parent) == before


def test_stage1_atomic_replace_failure_keeps_formal_and_diagnostic(tmp_path, monkeypatch):
    store = _stage1_store(tmp_path)
    path = store.write(MT_REL, _main())
    store.path(paths.invalid_of(MT_REL)).write_bytes(b'diagnostic')
    before = _tree(path.parent)
    original = ArtifactStore._replace_atomically
    def fail(target, data):
        if target == path and data != before[path.name]:
            raise OSError('注入原子替换失败')
        return original(target, data)
    monkeypatch.setattr(ArtifactStore, '_replace_atomically', staticmethod(fail))
    with pytest.raises(OSError, match='原子替换失败'):
        store.write(MT_REL, _main('不会写完'))
    assert _tree(path.parent) == before


def test_refresh_cannot_invent_generation_provenance_for_stage1_or_create_missing_formal(tmp_path):
    store = _stage1_store(tmp_path)
    path = store.write(MT_REL, _main())
    invalid = store.path(paths.invalid_of(MT_REL))
    invalid.write_bytes(b'diagnostic')
    before = path.read_bytes()
    with pytest.raises(ProvenanceRefused, match='逐字段继承'):
        store.refresh_formal(MT_REL, _main('不能伪造生成来源账'))
    assert invalid.read_bytes() == b'diagnostic'
    assert path.read_bytes() == before
    path.unlink()
    with pytest.raises(UpstreamMissing):
        store.refresh_formal(MT_REL, _main())


def test_write_round_rollback_restores_pre_archive_whole_byte_tree(tmp_path, monkeypatch):
    store = _stage1_store(tmp_path)
    path = store.write(MT_REL, _main())
    before = _tree(path.parent)
    def fail(*_args):
        raise ProvenanceRefused('注入新轮失败')
    monkeypatch.setattr(store, '_assert_self_fresh', fail)
    with pytest.raises(ProvenanceRefused, match='新轮失败'):
        store.write_round(MT_REL, _main('第二轮'))
    assert _tree(path.parent) == before


def test_archive_cannot_overwrite_rounds_counts_gaps_and_can_keep_live_original(tmp_path):
    store = _stage1_store(tmp_path)
    path = store.write(MT_REL, _main())
    store.archive_round(MT_REL, round_no=1, keep=True)
    assert path.exists()
    with pytest.raises(PathRefused, match='归档目标已存在'):
        store.archive_round(MT_REL, round_no=1)
    store.archive_round(MT_REL, round_no=3)
    assert not path.exists() and store.seen_rounds(MT_REL) == {1, 3}
    assert store.next_round(MT_REL) == 4


def test_generic_history_transaction_rolls_back_late_bound_byte_retirement(tmp_path):
    store = _stage1_store(tmp_path)
    rel = STAGE0_CLEAN_TEXT
    before = _tree(store.path(rel).parent)
    with pytest.raises(RuntimeError, match='后续失败'):
        with store.history_transaction(rel):
            store.retire_bytes(rel, 'stage0/_rounds/clean.archived.txt')
            raise RuntimeError('后续失败')
    assert _tree(store.path(rel).parent) == before


def test_formal_writer_refuses_invalid_name_and_evidence_only_does_not_need_fake_provenance(tmp_path):
    store = _stage1_store(tmp_path)
    with pytest.raises(PathRefused, match='write_invalid'):
        store.write(paths.invalid_of(MT_REL), _main())
    # Stage1没有付费来源账，不能为套用旧问题稿协议伪造一份。
    with pytest.raises(ProvenanceRefused):
        store.write_invalid(MT_REL, _main(), gates_skipped=[])
    assert store.retire_invalid(MT_REL) is None


def test_source_ledgers_are_per_run_snapshots_and_not_shared_between_books(tmp_path):
    first = _plan_ctx(_book(sid='111', root=tmp_path / 'a'), '111')
    second = _plan_ctx(_book(sid='222', root=tmp_path / 'b', payload=b'{"text":"different"}'), '222')
    _record_upstream_edge(first)
    _record_upstream_edge(second)
    assert first.edges[UPSTREAM].file != second.edges[UPSTREAM].file
    first.edges.clear()
    assert set(first.edges) == {UPSTREAM} and set(second.edges) == {UPSTREAM}


def test_input_edge_null_and_provenance_absence_are_not_serialization_defaults():
    edge = InputEdge(file='a' * 10, consumed=None)
    assert edge.model_dump(mode='json') == {'file': 'a' * 10, 'consumed': None}
    usage = Usage(calls_made=1, calls_cached=0, prompt_tokens=None, completion_tokens=None,
                  reasoning_tokens=None)
    assert usage.model_dump(mode='json')['prompt_tokens'] is None


def test_actual_stage0_and_stage1_deterministic_fingerprints_detect_tampering(tmp_path):
    from novel.core.partition import partition_text
    base = _book(root=tmp_path, with_clean=False)
    book = base / '书名_123'
    source = base / 'source.txt'
    source.write_bytes(_SOURCE_BYTES)
    _stage0_on_disk(book, source, _SOURCE_BYTES, _SOURCE_BYTES)
    split = partition_text(_SOURCE_BYTES.decode('utf-8'))
    _write_payload(book, STAGE1_MAIN_TEXT, split.main_text.model_dump(mode='json'))
    _write_payload(book, STAGE1_HOOK_COPY, split.hook_copy.model_dump(mode='json'))
    store = ArtifactStore(_ctx(base))
    for rel in (STAGE0_REVISIONS, STAGE0_CLEAN_TEXT, STAGE1_MAIN_TEXT, STAGE1_HOOK_COPY):
        assert store.freshness(rel).state == 'fresh'
    changed = split.main_text.model_dump(mode='json')
    changed[0]['text'] += '篡改'
    _write_payload(book, STAGE1_MAIN_TEXT, changed)
    assert store.freshness(STAGE1_MAIN_TEXT).state == 'stale'
    _write_payload(book, STAGE1_HOOK_COPY, {'text': split.hook_copy.text + '篡改'})
    assert store.freshness(STAGE1_HOOK_COPY).state == 'stale'


def test_run_context_refuses_mixed_observed_roots_without_equivalent_body_fallback(tmp_path):
    ctx = _ctx(_book(root=tmp_path))
    ctx.record_artifact_root(STAGE1_MAIN_TEXT, '0' * 40)
    with pytest.raises(ProvenanceRefused, match='不同 root_clean_sha1'):
        ctx.free_provenance()


def test_partial_rewrite_cannot_rebind_previous_provenance_to_a_different_source(tmp_path):
    ctx = _ctx(_book(root=tmp_path))
    previous = ctx.free_provenance().model_copy(update={'root_clean_sha1': '0' * 40})
    with pytest.raises(ProvenanceRefused, match='局部改写不能'):
        ctx.rewrite_provenance(previous, by='当前免费复验')
