#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 trim 批量销账（P1-2）：整批预检、按章幂等、漂移即整批不开始。

【角色边界】批量 trim 销账是现役人工入口（``--retain-pretrim-batch``，Stage 页面
在用）。旧 commander 载体的席位回执重放机器（``reviewer_tasks``/
``reviewer_contract`` 的 submission options、partial replay binding 与 typed
loader）随受托审阅退役删除；「前缀重放」改由批量写路径自身的幂等重交承接，
历史席位回执的只读解码归 ``reviewer_legacy`` 的消费方。
"""
from __future__ import annotations

import hashlib
import importlib
import json

import pytest

from contracts.schema.api import BatchTrimResolutionRequest
from contracts.schema.evaluation import TrimResolution
from contracts.schema.stage5 import Chapter
from novel.errors import ArtifactUnreadable, ProvenanceRefused, TerminalState
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger
from novel.run import RunContext
from novel.stages.stage5 import trim_resolution
from novel.store import ArtifactStore


def _chapter(ordinal: int, text: str, root_fp: str, root_sha1: str) -> Chapter:
    return Chapter.model_validate({'output_ordinal': ordinal, 'segments': [{'slot': 's01', 'text': text}], '_provenance': {'code': 'test-trim-batch', 'root_clean_sha1': root_sha1, 'inputs': {'stage0/clean_text.txt': {'file': root_fp, 'consumed': None}}, 'origin': 'human', 'round': 1, 'prompts': {'SYS': 'a' * 10}, 'model': 'test-model', 'adopted_models': ['test-model']}})


@pytest.fixture
def rig(tmp_path):
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    book_dir = tmp_path / '批量销账_001'
    (book_dir / 'stage5').mkdir(parents=True)
    (book_dir / 'stage0').mkdir(parents=True)
    (book_dir / 'stage0' / 'clean_text.txt').write_text(
        '固定测试根。\n', encoding='utf-8', newline='')
    from novel import prov
    root_fp = prov.fingerprint_bytes(
        (book_dir / 'stage0' / 'clean_text.txt').read_bytes())
    root_sha1 = prov.content_sha1(
        (book_dir / 'stage0' / 'clean_text.txt').read_bytes())
    ctx = RunContext(
        novel_dir=book_dir, node='stage5.review', paid=False,
        code='test-trim-batch',
        cache=CachePolicy.default(), ledger=CostLedger(),
        observer=lambda _event: None,
    )
    store = ArtifactStore(ctx)
    sha_by_ordinal: dict[int, str] = {}
    audit_sha: dict[int, str] = {}
    for ordinal, text in ((1, '第一章超限正文。' * 20), (2, '第二章超限正文。' * 20)):
        chapter = _chapter(ordinal, text, root_fp, root_sha1)
        raw = chapter.model_dump(by_alias=True, exclude_none=True)
        raw['_provenance'] = chapter.provenance.to_disk()
        from contracts.schema import encode_artifact
        rel = f'stage5/ch{ordinal:02d}.json'
        store.path(rel).write_bytes((
            json.dumps(encode_artifact(rel, raw), ensure_ascii=False, indent=2)
            + chr(10)).encode('utf-8'))
        sha_by_ordinal[ordinal] = hashlib.sha256(
            chapter.text.encode('utf-8')).hexdigest()
    for ordinal in sha_by_ordinal:
        from contracts.schema.evaluation import TrimAudit
        audit = TrimAudit(before_sha256=sha_by_ordinal[ordinal], reason='超上限，零调用落 identity audit')
        store.write(f'stage5/trim_audit_ch{ordinal:02d}.json', audit)
        audit_sha[ordinal] = store.read_with_sha256(
            TrimAudit, f'stage5/trim_audit_ch{ordinal:02d}.json')[1]
    return ctx, store, sha_by_ordinal, audit_sha


def _request(sha_by_ordinal, audit_sha, *, chapter_override=None):
    items = []
    for ordinal in sorted(sha_by_ordinal):
        items.append({
            'output_ordinal': ordinal,
            'audit_sha256': audit_sha[ordinal],
            'chapter_sha256': (chapter_override or {}).get(
                ordinal, sha_by_ordinal[ordinal]),
        })
    return BatchTrimResolutionRequest.model_validate({
        'actor': 'commander', 'reason': '两章一并确认保留 pretrim',
        'items': items,
    })


def _delegated_request(sha_by_ordinal, audit_sha):
    return BatchTrimResolutionRequest.model_validate({
        'actor': 'agent:novel-pipeline-operator',
        'reason': '跨章复核后确认两章均应保留',
        'items': [{
            'output_ordinal': ordinal,
            'audit_sha256': audit_sha[ordinal],
            'chapter_sha256': sha_by_ordinal[ordinal],
            'reason': f'第{ordinal}章已逐字核对，超限内容承载不可删的因果证据',
        } for ordinal in sorted(sha_by_ordinal)],
    })


def test_batch_writes_every_resolution_and_is_idempotent(rig):
    ctx, store, sha_by_ordinal, audit_sha = rig
    request = _request(sha_by_ordinal, audit_sha)
    written = trim_resolution.retain_pretrim_batch(ctx, request)
    assert [row.output_ordinal for row in written] == [1, 2]
    for ordinal in (1, 2):
        rel = trim_resolution.resolution_rel(ordinal)
        assert store.exists(rel)
        resolution, _raw = store.read_own(
            __import__('contracts.schema.evaluation', fromlist=['TrimResolution']).TrimResolution,
            rel)
        assert resolution.actor == 'commander'
        assert resolution.reason == '两章一并确认保留 pretrim'
    # 重交同一 manifest：已销账章幂等跳过，不写新轮。
    rounds_before = {
        ordinal: store.next_round(trim_resolution.resolution_rel(ordinal))
        for ordinal in (1, 2)}
    again = trim_resolution.retain_pretrim_batch(ctx, request)
    assert [row.output_ordinal for row in again] == [1, 2]
    for ordinal in (1, 2):
        rel = trim_resolution.resolution_rel(ordinal)
        assert store.next_round(rel) == rounds_before[ordinal]  # 轮数不变


def test_batch_kill_after_prefix_replays_original_manifest_across_new_epoch(
        rig, monkeypatch):
    """首章落地后 kill：同一份 manifest 零模型重交，旧 round/raw 不改，只补 suffix。"""
    ctx, store, sha_by_ordinal, audit_sha = rig
    request = _delegated_request(sha_by_ordinal, audit_sha)
    original_write_round = ArtifactStore.write_round
    writes = 0

    def kill_after_first(current_store, rel, value):
        nonlocal writes
        result = original_write_round(current_store, rel, value)
        if rel == trim_resolution.resolution_rel(1):
            writes += 1
            raise RuntimeError('模拟进程在第一章 committed 后被 kill')
        return result

    monkeypatch.setattr(ArtifactStore, 'write_round', kill_after_first)
    with pytest.raises(RuntimeError, match='模拟进程'):
        trim_resolution.retain_pretrim_batch(ctx, request)
    assert writes == 1
    first_rel = trim_resolution.resolution_rel(1)
    second_rel = trim_resolution.resolution_rel(2)
    first_raw = store.path(first_rel).read_bytes()
    first_round = store.next_round(first_rel)
    assert not store.exists(second_rel)

    monkeypatch.setattr(ArtifactStore, 'write_round', original_write_round)
    new_ctx = RunContext(
        novel_dir=ctx.novel_dir, node='stage5.review', paid=False,
        code='test-trim-batch-code-v2',
        cache=CachePolicy.default(), ledger=CostLedger(
            run_id='new-producer-run', parent_run_id='new-parent'),
        run_id='new-producer-run', parent_run_id='new-parent',
        observer=lambda _event: None,
    )
    trim_resolution.retain_pretrim_batch(new_ctx, request)
    assert store.path(first_rel).read_bytes() == first_raw
    assert store.next_round(first_rel) == first_round
    assert store.exists(second_rel)
    first, _ = store.read_own(TrimResolution, first_rel)
    second, _ = store.read_own(TrimResolution, second_rel)
    assert first.provenance.code == 'test-trim-batch'
    assert second.provenance.code == 'test-trim-batch-code-v2'


def test_any_drift_refuses_the_whole_batch(rig):
    ctx, store, sha_by_ordinal, audit_sha = rig
    request = _request(
        sha_by_ordinal, audit_sha,
        chapter_override={2: 'f' * 64})
    with pytest.raises(Exception, match='整批不开始'):
        trim_resolution.retain_pretrim_batch(ctx, request)
    for ordinal in (1, 2):
        assert not store.exists(trim_resolution.resolution_rel(ordinal)), (
            '整批预检失败后不得留下任何一章的 resolution')


def test_unreadable_existing_resolution_refuses_batch_before_any_write(rig):
    """现存坏回执不能当 absent；整批首写前必须关闭且保留原坏字节。"""
    ctx, store, sha_by_ordinal, audit_sha = rig
    request = _request(sha_by_ordinal, audit_sha)
    broken_rel = trim_resolution.resolution_rel(2)
    broken_raw = b'{broken-resolution\n'
    store.path(broken_rel).write_bytes(broken_raw)

    with pytest.raises(ArtifactUnreadable):
        trim_resolution.retain_pretrim_batch(ctx, request)

    assert not store.exists(trim_resolution.resolution_rel(1))
    assert store.path(broken_rel).read_bytes() == broken_raw


@pytest.mark.parametrize(
    'corruption', [
        'wrong-root', 'wrong-ordinal', 'wrong-chapter-rel', 'inconsistent-sha'])
def test_readable_corrupt_resolution_refuses_batch_before_any_write(
        rig, corruption):
    """混根/错章/内部矛盾回执均不能洗成旧轮并覆盖。"""
    ctx, store, sha_by_ordinal, audit_sha = rig
    request = _request(sha_by_ordinal, audit_sha)
    provenance = ctx.free_provenance().model_dump(mode='json')
    output_ordinal = 2
    chapter_rel = 'stage5/ch02.json'
    chapter_sha256 = sha_by_ordinal[2]
    if corruption == 'wrong-root':
        provenance['root_clean_sha1'] = '0' * 40
    elif corruption == 'wrong-ordinal':
        output_ordinal = 1
        chapter_rel = 'stage5/ch01.json'
    resolution = TrimResolution.model_validate({'output_ordinal': output_ordinal, 'action': 'retain_pretrim', 'actor': 'foreign-commander', 'reason': '盘外可读但身份损坏的回执', 'audit_sha256': audit_sha[2], '_provenance': provenance})
    rel = trim_resolution.resolution_rel(2)
    from contracts.schema import encode_artifact
    payload = resolution.model_dump(by_alias=True, exclude_none=True)
    payload['_provenance'] = resolution.provenance.to_disk()
    if corruption == 'wrong-chapter-rel':
        # schema 本身会拒；直接摆盘模拟损坏的现存 artifact envelope。
        payload['chapter_rel'] = 'stage5/ch01.json'
    elif corruption == 'inconsistent-sha':
        payload['chapter_sha256'] = 'e' * 64
    store.path(rel).write_bytes((json.dumps(
        encode_artifact(rel, payload), ensure_ascii=False, indent=2,
    ) + '\n').encode('utf-8'))
    original_raw = store.path(rel).read_bytes()

    with pytest.raises((ArtifactUnreadable, ProvenanceRefused)):
        trim_resolution.retain_pretrim_batch(ctx, request)

    assert not store.exists(trim_resolution.resolution_rel(1))
    assert store.path(rel).read_bytes() == original_raw


def test_batch_requires_the_full_current_unresolved_frontier(rig):
    ctx, store, sha_by_ordinal, audit_sha = rig
    request = _request(sha_by_ordinal, audit_sha).model_copy(update={
        'items': _request(sha_by_ordinal, audit_sha).items[:1],
    })

    with pytest.raises(Exception, match='当前待销账全集'):
        trim_resolution.retain_pretrim_batch(ctx, request)

    assert all(
        not store.exists(trim_resolution.resolution_rel(ordinal))
        for ordinal in (1, 2))


@pytest.mark.parametrize('foreign_kind', ['malformed', 'orphan-chapter'])
def test_partial_batch_rejects_foreign_or_unreadable_physical_audit_before_suffix(
        rig, foreign_kind):
    """matching glob 下的坏/孤立 audit 不得从 strict frontier 静默消失。"""
    ctx, store, sha_by_ordinal, audit_sha = rig
    request = _request(sha_by_ordinal, audit_sha)
    first_reason = request.items[0].reason.strip() or request.reason
    trim_resolution.retain_pretrim(
        ctx, 1, actor=request.actor, reason=first_reason)
    foreign = store.path('stage5/trim_audit_ch99.json')
    if foreign_kind == 'malformed':
        foreign.write_bytes(b'{not-json\n')
    else:
        foreign.write_bytes(store.path(
            'stage5/trim_audit_ch01.json').read_bytes())

    with pytest.raises(Exception, match='inventory'):
        trim_resolution.retain_pretrim_batch(ctx, request)

    assert not store.exists(trim_resolution.resolution_rel(2))


def test_new_audit_can_write_a_new_round_over_a_stale_old_resolution(rig):
    """不同 existing resolution 可能只是旧 audit 历史，不能永久锁死新销账。"""
    ctx, store, sha_by_ordinal, audit_sha = rig
    trim_resolution.retain_pretrim_batch(ctx, _request(sha_by_ordinal, audit_sha))
    rel = trim_resolution.resolution_rel(1)
    round_before = store.next_round(rel)
    from contracts.schema.evaluation import TrimAudit, TrimResolution
    new_audit = TrimAudit(before_sha256=sha_by_ordinal[1], reason='同一正文按新一轮审计重新登记超限事实')
    audit_rel = 'stage5/trim_audit_ch01.json'
    store.write_round(audit_rel, new_audit)
    new_audit_sha = store.read_with_sha256(TrimAudit, audit_rel)[1]
    request = BatchTrimResolutionRequest.model_validate({
        'actor': 'commander-v2', 'reason': '新 audit 人工复核',
        'items': [{
            'output_ordinal': 1,
            'audit_sha256': new_audit_sha,
            'chapter_sha256': sha_by_ordinal[1],
            'reason': '第1章新审计仍确认保留',
        }],
    })

    trim_resolution.retain_pretrim_batch(ctx, request)

    resolution, _ = store.read_own(TrimResolution, rel)
    assert resolution.audit_sha256 == new_audit_sha
    assert resolution.actor == 'commander-v2'
    assert store.next_round(rel) > round_before


def test_drift_between_preflight_and_first_write_leaves_zero_resolutions(
        rig, monkeypatch):
    """首轮采样与首写前复采之间漂移时，整批一个 resolution 都不能落。"""
    ctx, store, sha_by_ordinal, audit_sha = rig
    request = _request(sha_by_ordinal, audit_sha)
    original = trim_resolution._current_pretrim
    calls = 0

    def drift_on_second_collection(current_store, audit):
        nonlocal calls
        calls += 1
        current = original(current_store, audit)
        if calls == 3 and current is not None:
            return current[0], 'f' * 64
        return current

    monkeypatch.setattr(
        trim_resolution, '_current_pretrim', drift_on_second_collection)

    with pytest.raises(
            Exception, match='frontier 失败|首写前复核失败.*整批不开始'):
        trim_resolution.retain_pretrim_batch(ctx, request)
    assert calls >= 3
    assert all(
        not store.exists(trim_resolution.resolution_rel(ordinal))
        for ordinal in (1, 2)
    )


@pytest.mark.parametrize('drift', ['chapter', 'audit', 'foreign-resolution'])
def test_each_suffix_write_revalidates_frontier_after_landed_prefix(
        rig, monkeypatch, drift):
    """write1→write2 窗的盘外漂移不得被旧 facts 覆盖或继续写尾。"""
    ctx, store, sha_by_ordinal, audit_sha = rig
    request = _request(sha_by_ordinal, audit_sha)
    original_write_round = ArtifactStore.write_round
    first_rel = trim_resolution.resolution_rel(1)
    second_rel = trim_resolution.resolution_rel(2)
    foreign_raw = None

    def write_artifact_bytes(rel, artifact):
        from contracts.schema import encode_artifact
        payload = artifact.model_dump(by_alias=True, exclude_none=True)
        if getattr(artifact, 'provenance', None) is not None:
            payload['_provenance'] = artifact.provenance.to_disk()
        store.path(rel).write_bytes((json.dumps(
            encode_artifact(rel, payload), ensure_ascii=False, indent=2,
        ) + '\n').encode('utf-8'))

    def inject_after_first(current_store, rel, value):
        nonlocal foreign_raw
        result = original_write_round(current_store, rel, value)
        if rel != first_rel:
            return result
        if drift == 'chapter':
            chapter, _ = store.read_own(Chapter, 'stage5/ch02.json')
            changed_text = chapter.text + '盘外新增一句。'
            changed = Chapter.model_validate({**chapter.model_dump(by_alias=True, exclude={'segments', 'chars'}), 'segments': [{'slot': 's01', 'text': changed_text}]})
            write_artifact_bytes('stage5/ch02.json', changed)
        elif drift == 'audit':
            from contracts.schema.evaluation import TrimAudit
            original_write_round(current_store, 'stage5/trim_audit_ch02.json', TrimAudit(before_sha256=sha_by_ordinal[2], reason='首章落后出现一份新 current audit'))
        else:
            from contracts.schema.evaluation import TrimResolution
            foreign = TrimResolution(output_ordinal=2, action='retain_pretrim', actor='foreign-commander', reason='盘外另一份人工决定', audit_sha256=audit_sha[2], _provenance=ctx.free_provenance())
            write_artifact_bytes(second_rel, foreign)
            foreign_raw = store.path(second_rel).read_bytes()
        return result

    monkeypatch.setattr(ArtifactStore, 'write_round', inject_after_first)

    with pytest.raises(Exception, match='frontier 失败'):
        trim_resolution.retain_pretrim_batch(ctx, request)

    assert store.exists(first_rel)
    if drift == 'foreign-resolution':
        assert foreign_raw is not None
        assert store.path(second_rel).read_bytes() == foreign_raw
        foreign, _ = store.read_own(
            __import__('contracts.schema.evaluation',
                       fromlist=['TrimResolution']).TrimResolution,
            second_rel)
        assert foreign.actor == 'foreign-commander'
    else:
        assert not store.exists(second_rel)


@pytest.mark.parametrize('drift', ['delete', 'stale-old-round'])
def test_each_loop_index_requires_the_already_processed_prefix(
        rig, monkeypatch, drift):
    """write1 后 prefix 不得退回成较短但表面合法的 frontier。"""
    ctx, store, sha_by_ordinal, audit_sha = rig
    request = _request(sha_by_ordinal, audit_sha)
    original_write_round = ArtifactStore.write_round
    first_rel = trim_resolution.resolution_rel(1)
    second_rel = trim_resolution.resolution_rel(2)
    stale_raw = None

    def regress_prefix(current_store, rel, value):
        nonlocal stale_raw
        result = original_write_round(current_store, rel, value)
        if rel != first_rel:
            return result
        if drift == 'delete':
            store.path(first_rel).unlink()
        else:
            stale = TrimResolution(output_ordinal=1, action='retain_pretrim', actor=request.actor, reason=request.reason, audit_sha256='9' * 64, _provenance=ctx.free_provenance())
            store.write(first_rel, stale)
            stale_raw = store.path(first_rel).read_bytes()
        return result

    monkeypatch.setattr(ArtifactStore, 'write_round', regress_prefix)

    with pytest.raises(TerminalState, match='已遍历.*前缀.*回退'):
        trim_resolution.retain_pretrim_batch(ctx, request)

    assert not store.exists(second_rel), 'prefix 回退后不得继续写下一章'
    if drift == 'delete':
        assert not store.exists(first_rel)
    else:
        assert stale_raw is not None
        assert store.path(first_rel).read_bytes() == stale_raw


@pytest.mark.parametrize('drift', ['delete-prefix', 'foreign-final'])
def test_batch_revalidates_full_resolution_frontier_after_the_last_write(
        rig, monkeypatch, drift):
    """末笔返回后的删除/foreign 覆盖不能让 batch 以成功退出。"""
    ctx, store, sha_by_ordinal, audit_sha = rig
    request = _request(sha_by_ordinal, audit_sha)
    original_write_round = ArtifactStore.write_round
    first_rel = trim_resolution.resolution_rel(1)
    second_rel = trim_resolution.resolution_rel(2)
    foreign_raw = None

    def drift_after_last(current_store, rel, value):
        nonlocal foreign_raw
        result = original_write_round(current_store, rel, value)
        if rel != second_rel:
            return result
        if drift == 'delete-prefix':
            store.path(first_rel).unlink()
        else:
            foreign = TrimResolution(output_ordinal=2, action='retain_pretrim', actor='foreign-commander', reason='末笔后盘外覆盖的人审决定', audit_sha256=audit_sha[2], _provenance=ctx.free_provenance())
            store.write(second_rel, foreign)
            foreign_raw = store.path(second_rel).read_bytes()
        return result

    monkeypatch.setattr(ArtifactStore, 'write_round', drift_after_last)

    with pytest.raises(TerminalState, match='frontier 失败'):
        trim_resolution.retain_pretrim_batch(ctx, request)

    if drift == 'delete-prefix':
        assert not store.exists(first_rel)
        assert store.exists(second_rel)
    else:
        assert store.exists(first_rel)
        assert foreign_raw is not None
        assert store.path(second_rel).read_bytes() == foreign_raw


def test_single_item_override_reason(rig):
    ctx, store, sha_by_ordinal, audit_sha = rig
    from contracts.schema.api import BatchTrimResolutionRequest
    request = BatchTrimResolutionRequest.model_validate({
        'actor': 'commander', 'reason': '共享理由',
        'items': [
            {'output_ordinal': 1, 'audit_sha256': audit_sha[1],
             'chapter_sha256': sha_by_ordinal[1]},
            {'output_ordinal': 2, 'audit_sha256': audit_sha[2],
             'chapter_sha256': sha_by_ordinal[2],
             'reason': '第二章单独的理由'},
        ],
    })
    trim_resolution.retain_pretrim_batch(ctx, request)
    from contracts.schema.evaluation import TrimResolution
    first, _ = store.read_own(TrimResolution, 'stage5/trim_resolution_ch01.json')
    second, _ = store.read_own(TrimResolution, 'stage5/trim_resolution_ch02.json')
    assert first.reason == '共享理由'
    assert second.reason == '第二章单独的理由'


def test_cli_trim_batch_resolves_relative_to_book_and_keeps_absolute_path(
        rig, monkeypatch):
    """direct CLI 保持现役语义：相对 manifest 相对书目录解析，绝对路径原样受理。"""
    ctx, _store, sha_by_ordinal, audit_sha = rig
    cli = importlib.import_module('novel.cli.main')
    relative_request = _request(sha_by_ordinal, audit_sha)
    relative = ctx.novel_dir / 'relative-trim-batch.json'
    relative.write_text(relative_request.model_dump_json(), encoding='utf-8')
    absolute_request = relative_request.model_copy(
        update={'reason': '绝对路径 manifest'})
    absolute = ctx.novel_dir.parent / 'absolute-trim-batch.json'
    absolute.write_text(absolute_request.model_dump_json(), encoding='utf-8')
    seen = []
    monkeypatch.setattr(
        trim_resolution, 'retain_pretrim_batch',
        lambda _ctx, request: seen.append(request.reason) or [])

    assert cli._run_stage5(
        ctx, ['--retain-pretrim-batch', relative.name]) == 0
    assert cli._run_stage5(
        ctx, ['--retain-pretrim-batch', str(absolute.resolve())]) == 0
    assert seen == ['两章一并确认保留 pretrim', '绝对路径 manifest']
