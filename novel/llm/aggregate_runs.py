# -*- coding: utf-8 -*-
"""确定性聚合的提交账：提交身份独立于任何模型 attempt。

生产者先完整复验每份来源，再提供不可变来源清单及复验回调。本模块只拥有
prepared → committed、写前基线 CAS 与采用回执；不执行模型、不写正式产物，
也不把聚合结果冒充某一章的 provider response。外层必须持有书级提交锁。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any

from contracts.schema import artifact_spec, decode_artifact
from novel import prov
from novel.appendlog import process_append_lock
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.llm import run_journal as agent_runs
from novel.run_identity import check_run_id


KIND = 'deterministic-aggregate-commit/v1'
_FIELDS = {
    'version', 'kind', 'state', 'run_id', 'producer_parent_run_id', 'sid', 'node', 'code',
    'primary_artifact', 'primary_payload', 'primary_payload_sha256',
    'primary_file_text', 'primary_file_sha256',
    'primary_payload_fingerprint', 'finalization_payload', 'finalization_sha256',
    'sources', 'sources_sha256', 'touched_paths', 'baselines',
    'prepared_at', 'committed_at', 'recovery', 'landed_payload_sha256s',
    'post_file_sha256s', 'commit_sha256',
}


@dataclass(frozen=True)
class AggregateCommitSpec:
    node: str
    primary_artifact: str
    commit_path: Callable[[Any], Path]
    allowed_touched: tuple[tuple[str, ...], ...]
    nonformal_paths: tuple[str, ...]
    sources: Mapping[str, Any]
    primary_file_text: str
    validate_sources: Callable[[], None]
    committer: Any | None = None


def _hash(record: Mapping[str, Any]) -> str:
    return agent_runs.canonical_sha256({
        key: value for key, value in record.items()
        if key not in {'prepared_at', 'committed_at', 'commit_sha256'}
    })


def validate_envelope(path: Path, *, run_id: str, sid: str, node: str) -> dict[str, Any]:
    """只读 reservation 发现口；损坏不能被解释为没有提交。"""
    record = agent_runs._read_json(path, label='确定性聚合提交')
    if (set(record) != _FIELDS or record.get('version') != 1
            or record.get('kind') != KIND
            or record.get('state') not in {'prepared', 'committed'}
            or record.get('run_id') != check_run_id(run_id)
            or record.get('producer_parent_run_id') != check_run_id(
                str(record.get('producer_parent_run_id') or ''))
            or record.get('sid') != sid or record.get('node') != node
            or not isinstance(record.get('code'), str) or not record['code']
            or record.get('commit_sha256') != _hash(record)):
        raise ArtifactUnreadable(f'{path} 聚合提交外壳/身份/摘要不闭合')
    for payload, digest in (
        ('primary_payload', 'primary_payload_sha256'),
        ('finalization_payload', 'finalization_sha256'),
        ('sources', 'sources_sha256'),
    ):
        if (not isinstance(record[payload], dict)
                or record[digest] != agent_runs.canonical_sha256(record[payload])):
            raise ArtifactUnreadable(f'{path} 的 {payload} 摘要不闭合')
    if (not isinstance(record.get('primary_file_text'), str)
            or record['primary_file_sha256'] != agent_runs.canonical_sha256(
                record['primary_file_text'].encode('utf-8'))):
        raise ArtifactUnreadable(f'{path} 主产物原始字节摘要不闭合')
    try:
        definition = artifact_spec(record['primary_artifact'])
        if definition is None:
            raise ValueError('未登记的主产物')
        decoded = decode_artifact(record['primary_artifact'], json.loads(record['primary_file_text']))
        primary = definition.model.model_validate(decoded.payload)
        if primary.model_dump(by_alias=True, mode='json') != record['primary_payload']:
            raise ValueError('冻结字节与 typed 主体不一致')
    except (ValueError, TypeError, KeyError) as exc:
        raise ArtifactUnreadable(f'{path} 主产物冻结字节不可验证：{exc}') from exc
    if record['primary_payload_fingerprint'] != prov.projection_fingerprint(
            record['primary_payload']):
        raise ArtifactUnreadable(f'{path} 聚合主产物指纹不闭合')
    touched, baselines = record['touched_paths'], record['baselines']
    if (not isinstance(touched, list) or not touched
            or any(not isinstance(rel, str) or not rel for rel in touched)
            or touched != sorted(set(touched))
            or not isinstance(baselines, dict) or sorted(baselines) != touched
            or any(value is not None and not agent_runs.is_sha256(value)
                   for value in baselines.values())
            or not isinstance(record['prepared_at'], str) or not record['prepared_at']):
        raise ArtifactUnreadable(f'{path} 聚合写前基线不闭合')
    if record['state'] == 'prepared':
        if any(record[key] is not None for key in (
                'committed_at', 'recovery', 'landed_payload_sha256s', 'post_file_sha256s')):
            raise ArtifactUnreadable(f'{path} prepared 夹带完成状态')
    else:
        landed, post = record['landed_payload_sha256s'], record['post_file_sha256s']
        if (not isinstance(record['committed_at'], str) or not record['committed_at']
                or not isinstance(landed, dict) or not landed
                or record['primary_artifact'] not in landed
                or not set(landed).issubset(touched)
                or any(not agent_runs.is_sha256(value) for value in landed.values())
                or not isinstance(post, dict) or sorted(post) != touched
                or any(value is not None and not agent_runs.is_sha256(value)
                       for value in post.values())):
            raise ArtifactUnreadable(f'{path} 聚合完成证据不闭合')
    return record


def _touched(spec: AggregateCommitSpec, values: Sequence[str]) -> list[str]:
    values = sorted(values)
    if (values != sorted(set(values)) or tuple(values) not in {
            tuple(sorted(group)) for group in spec.allowed_touched}
            or spec.primary_artifact not in values):
        raise ProvenanceRefused('聚合提交路径不在 producer 登记的终态集合中')
    return values


def commit_state(ctx: Any, spec: AggregateCommitSpec) -> dict[str, Any] | None:
    path = spec.commit_path(ctx)
    if not path.is_file():
        return None
    record = validate_envelope(path, run_id=ctx.run_id, sid=ctx.sid, node=spec.node)
    if (record['code'] != ctx.code or record['producer_parent_run_id'] != ctx.parent_run_id
            or record['primary_artifact'] != spec.primary_artifact
            or record['sources'] != dict(spec.sources)
            or record['primary_file_text'] != spec.primary_file_text):
        raise ProvenanceRefused(f'{path} 聚合 code/主产物/来源清单漂移')
    _touched(spec, record['touched_paths'])
    spec.validate_sources()
    _verify_recovery_receipt(ctx, record)
    return record


def prepare_commit(ctx: Any, spec: AggregateCommitSpec, *,
                   primary_payload: Mapping[str, Any],
                   finalization_payload: Mapping[str, Any],
                   touched_paths: Sequence[str]) -> dict[str, Any]:
    spec.validate_sources()
    touched = _touched(spec, touched_paths)
    primary, finalization, sources = map(dict, (
        primary_payload, finalization_payload, spec.sources))
    record: dict[str, Any] = {
        'version': 1, 'kind': KIND, 'state': 'prepared',
        'run_id': check_run_id(ctx.run_id), 'producer_parent_run_id': check_run_id(ctx.parent_run_id),
        'sid': ctx.sid, 'node': spec.node,
        'code': ctx.code, 'primary_artifact': spec.primary_artifact,
        'primary_payload': primary,
        'primary_file_text': spec.primary_file_text,
        'primary_file_sha256': agent_runs.canonical_sha256(spec.primary_file_text.encode('utf-8')),
        'primary_payload_sha256': agent_runs.canonical_sha256(primary),
        'primary_payload_fingerprint': prov.projection_fingerprint(primary),
        'finalization_payload': finalization,
        'finalization_sha256': agent_runs.canonical_sha256(finalization),
        'sources': sources, 'sources_sha256': agent_runs.canonical_sha256(sources),
        'touched_paths': touched,
        'baselines': {rel: agent_runs.file_sha256(ctx, rel) for rel in touched},
        'prepared_at': agent_runs._now(), 'committed_at': None, 'recovery': None,
        'landed_payload_sha256s': None, 'post_file_sha256s': None,
    }
    record['commit_sha256'] = _hash(record)
    path = spec.commit_path(ctx)
    with process_append_lock(path):
        current = commit_state(ctx, spec)
        if current is not None:
            mutable = {'state', 'baselines', 'prepared_at', 'committed_at', 'recovery',
                       'landed_payload_sha256s', 'post_file_sha256s', 'commit_sha256'}
            if ({k: v for k, v in current.items() if k not in mutable}
                    != {k: v for k, v in record.items() if k not in mutable}):
                raise ProvenanceRefused(f'{path} 已冻结另一份聚合 intent')
            return current
        agent_runs._atomic_json(path, record)
    return record


def _current(ctx: Any, spec: AggregateCommitSpec,
             record: Mapping[str, Any]) -> dict[str, Any]:
    current = commit_state(ctx, spec)
    if current is None or current != dict(record):
        raise ProvenanceRefused('聚合提交记录不是当前盘上 journal')
    return current


def target_state(ctx: Any, spec: AggregateCommitSpec, record: Mapping[str, Any],
                 rel: str, *, expected_file_sha256: str | None = None,
                 expected_absent: bool = False) -> str:
    current = _current(ctx, spec, record)
    if (rel not in current['baselines'] or not isinstance(expected_absent, bool)
            or (expected_absent and expected_file_sha256 is not None)
            or (expected_file_sha256 is not None
                and not agent_runs.is_sha256(expected_file_sha256))):
        raise ProvenanceRefused('聚合目标路径/预期摘要不合法')
    actual = agent_runs.file_sha256(ctx, rel)
    if (expected_absent and actual is None) or (
            expected_file_sha256 is not None and actual == expected_file_sha256):
        return 'expected'
    if actual == current['baselines'][rel]:
        return 'baseline'
    raise ProvenanceRefused(f'{rel} 既不是聚合写前基线也不是冻结目标；拒绝第三方漂移')


def _adoption_payload(ctx: Any, record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'kind': 'deterministic_aggregate_adoption', 'run_id': ctx.run_id,
        'sid': ctx.sid, 'node': record['node'],
        'artifact': record['primary_artifact'],
        'artifact_fingerprint': record['primary_payload_fingerprint'],
        'sources_sha256': record['sources_sha256'],
        'finalization_sha256': record['finalization_sha256'],
    }


def _adoption_matches(ctx: Any, record: Mapping[str, Any]) -> list[dict[str, Any]]:
    expected = _adoption_payload(ctx, record)
    from novel.llm.ledger import read_strict_audit_events
    return [row for row in read_strict_audit_events(ctx.ledger)
            if all(row.get(key) == value for key, value in expected.items())]


def preclaim_adoption(ctx: Any, record: Mapping[str, Any]) -> None:
    """写盘前验证 0-or-exact 审计；损坏与第三种既有采用不能留到写盘后发现。"""
    from novel.llm.ledger import read_strict_audit_events
    expected = _adoption_payload(ctx, record)
    relevant = [row for row in read_strict_audit_events(ctx.ledger)
                if row.get('kind') == expected['kind'] and row.get('run_id') == ctx.run_id
                and row.get('sid') == ctx.sid and row.get('node') == record['node']]
    if len(relevant) > 1 or any(
            any(row.get(key) != value for key, value in expected.items()) for row in relevant):
        raise ProvenanceRefused('聚合已有不一致或重复采用审计；写盘前拒绝')


def _recovery_payload(ctx: Any, record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'kind': 'deterministic_aggregate_recovery', 'sid': ctx.sid, 'node': record['node'],
        'source_run_id': ctx.run_id, 'producer_parent_run_id': record['producer_parent_run_id'],
        'sources_sha256': record['sources_sha256'],
        'primary_payload_sha256': record['primary_payload_sha256'],
        'finalization_sha256': record['finalization_sha256'],
    }


def preclaim_recovery(ctx: Any, spec: AggregateCommitSpec, record: Mapping[str, Any]) -> dict[str, Any] | None:
    """调用方已持原 owner stopped lease；实际 committer 先留可幂等复验的独立凭证。"""
    return preclaim_recovery_actor(ctx, record, committer=spec.committer)


def preclaim_recovery_actor(ctx: Any, record: Mapping[str, Any], *,
                            committer: Any | None) -> dict[str, Any] | None:
    """A/B 聚合共用实际补提交者审计；不依赖单个模型 attempt。"""
    if committer is None:
        return None
    actor = committer
    if actor.paid or actor.run_id == ctx.run_id or actor.sid != ctx.sid or actor.code != ctx.code:
        raise ProvenanceRefused('聚合恢复 committer 必须是同书同代码的另一免费执行')
    from novel.llm.ledger import read_strict_audit_events
    read_strict_audit_events(actor.ledger)
    return actor.ledger.note_aggregate_recovery(_recovery_payload(ctx, record))


def _verify_recovery_receipt(ctx: Any, record: Mapping[str, Any]) -> None:
    recovery = record.get('recovery')
    if recovery is None:
        return
    if not isinstance(recovery, dict):
        raise ProvenanceRefused('聚合恢复身份不是对象')
    expected = _recovery_payload(ctx, record)
    if (set(recovery) != set(expected) | {'run_id', 'parent_run_id', 'aggregate_recovery_id'}
            or any(recovery.get(key) != value for key, value in expected.items())
            or check_run_id(str(recovery.get('run_id') or '')) == ctx.run_id):
        raise ProvenanceRefused('聚合恢复身份/来源关系损坏')
    check_run_id(str(recovery.get('parent_run_id') or ''))
    digest = agent_runs.canonical_sha256({k: v for k, v in recovery.items()
                                            if k != 'aggregate_recovery_id'})
    if digest != recovery['aggregate_recovery_id']:
        raise ProvenanceRefused('聚合恢复回执摘要损坏')
    from novel.llm.ledger import read_strict_audit_events
    rows = [row for row in read_strict_audit_events(ctx.ledger)
            if row.get('aggregate_recovery_id') == digest]
    if len(rows) != 1 or {k: v for k, v in rows[0].items() if k != 'at'} != recovery:
        raise ProvenanceRefused('聚合恢复缺实际 committer 的唯一完整审计')


def note_adoption(ctx: Any, spec: AggregateCommitSpec, record: Mapping[str, Any]) -> None:
    current = _current(ctx, spec, record)
    matches = _adoption_matches(ctx, current)
    if len(matches) > 1:
        raise ProvenanceRefused('聚合采用审计重复；不得继续提交')
    if not matches:
        payload = _adoption_payload(ctx, current)
        # 只写采用事实，不创造 ledger 请求或向历史 run 追加费用。
        payload.pop('kind')
        payload.pop('run_id')
        ctx.ledger.note_aggregate_adoption(**payload)


def _delivery(ctx: Any, spec: AggregateCommitSpec, record: Mapping[str, Any], *,
              primary_payload: Mapping[str, Any], finalization_payload: Mapping[str, Any],
              landed_payloads: Mapping[str, Mapping[str, Any]],
              ) -> tuple[dict[str, str], dict[str, str | None]]:
    if (record['primary_payload'] != dict(primary_payload)
            or record['finalization_payload'] != dict(finalization_payload)):
        raise ProvenanceRefused('聚合正式主体/终验与 prepared 漂移')
    formal = sorted(set(record['touched_paths']) - set(spec.nonformal_paths))
    if sorted(landed_payloads) != formal:
        raise ProvenanceRefused('聚合正式产物未精确覆盖提交集合')
    landed = {rel: agent_runs.canonical_sha256(dict(landed_payloads[rel])) for rel in formal}
    post = {rel: agent_runs.file_sha256(ctx, rel) for rel in record['touched_paths']}
    if any(post[rel] is None for rel in formal) or len(_adoption_matches(ctx, record)) != 1:
        raise ProvenanceRefused('聚合正式文件或唯一采用审计未闭合')
    return landed, post


def mark_committed(ctx: Any, spec: AggregateCommitSpec, *,
                   primary_payload: Mapping[str, Any], finalization_payload: Mapping[str, Any],
                   landed_payloads: Mapping[str, Mapping[str, Any]]) -> Path:
    path = spec.commit_path(ctx)
    with process_append_lock(path):
        current = commit_state(ctx, spec)
        if current is None:
            raise ProvenanceRefused('聚合提交缺 prepared journal')
        landed, post = _delivery(ctx, spec, current, primary_payload=primary_payload,
                                 finalization_payload=finalization_payload,
                                 landed_payloads=landed_payloads)
        if current['state'] == 'committed':
            if current['landed_payload_sha256s'] != landed or current['post_file_sha256s'] != post:
                raise ProvenanceRefused('已完成聚合的正式字节或主体漂移')
            return path
        recovery = preclaim_recovery(ctx, spec, current)
        updated = {**current, 'state': 'committed', 'committed_at': agent_runs._now(),
                   'recovery': recovery,
                   'landed_payload_sha256s': landed, 'post_file_sha256s': post}
        updated['commit_sha256'] = _hash(updated)
        agent_runs._atomic_json(path, updated)
    return path


def verify_committed_delivery(ctx: Any, spec: AggregateCommitSpec, record: Mapping[str, Any], *,
                              primary_payload: Mapping[str, Any],
                              finalization_payload: Mapping[str, Any],
                              landed_payloads: Mapping[str, Mapping[str, Any]]) -> None:
    current = _current(ctx, spec, record)
    landed, post = _delivery(ctx, spec, current, primary_payload=primary_payload,
                             finalization_payload=finalization_payload,
                             landed_payloads=landed_payloads)
    if (current['state'] != 'committed' or current['landed_payload_sha256s'] != landed
            or current['post_file_sha256s'] != post):
        raise ProvenanceRefused('聚合尚未完成或交付证据已漂移')
