#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''stage0 的十三条判据实现。纯检查不改任何产物。'''
from __future__ import annotations

import os
from pathlib import Path

from contracts.invariants import BODY_RATIO_MIN
from contracts.schema.report import CheckReport, finding
from contracts.schema.stage0 import Stage0Snapshot, Stage0Transform
from novel import paths, prov
from novel.core.ingest import (
    DecodedSource,
    decode_source_bytes,
    nonbody_kind,
    scan_text,
)
from novel.core.source import describe_source, resolve_recorded_source
from novel.core.warnings import describe
from novel.report.builder import build
from novel.report.findings import Outcome, ran, unverifiable
from novel.run import RunContext
from novel.store import ArtifactStore

from novel.stages.stage0 import revisions as rev
from novel.stages.stage0.removal import CLEAN_REL


def _hit(rule: str, detail: str, *evidence: str):
    return finding(rule, detail=detail, evidence=list(evidence))


def _source(report: Stage0Snapshot,
            base: str | os.PathLike[str]) -> tuple[DecodedSource | None, str | None]:
    path = resolve_recorded_source(report.source_ref, base)
    if path is None:
        return None, f'原始小说已不在记录位置：{describe_source(report.source_ref)}'
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, f'原始小说无法读取：{path}（{exc!r}）'
    try:
        return decode_source_bytes(raw), None
    except ValueError as exc:
        return None, f'原始小说无法按摄入契约无损解码：{exc}'


def check(
    report: Stage0Snapshot,
    clean_text: str,
    *,
    source: DecodedSource | None,
    source_why: str | None = None,
    novel_dir: Path | None = None,
    transform: Stage0Transform | None = None,
) -> list[Outcome]:
    '''核对运行时快照、正文、可选原文与书目录；每条注册判据返回一个 Outcome。'''
    out: list[Outcome] = []

    residual = scan_text(clean_text).nonbody
    nonbody_findings = [
        _hit(
            's0_nonbody_shared',
            f'clean_text 第 {clean_text.count(chr(10), 0, marker.line_start) + 1} 行'
            f'仍命中非正文标记「{marker.title}」',
            marker.kind,
            marker.title,
        )
        for marker in residual
    ]
    machine_segments = [
        (index, segment)
        for index, segment in enumerate(report.removed)
        if segment.origin == 'machine'
    ]
    for index, segment in machine_segments:
        detected = nonbody_kind(segment.marker_line)
        if detected != segment.kind:
            nonbody_findings.append(_hit(
                's0_nonbody_shared',
                f'removed[{index}] 声称 kind={segment.kind!r}，'
                f'共用判据复算 marker_line 得到 {detected!r}',
                segment.marker_line,
                str(detected),
            ))
    out.append(ran(
        's0_nonbody_shared',
        nonbody_findings,
        scanned=f'clean_text {len(clean_text)} 码点 · {len(residual)} 个残留 · '
                f'{len(machine_segments)} 个机器标记',
    ))

    if source is None:
        out.append(unverifiable(
            's0_removal_ledger',
            source_why or '没有原始小说，无法把 removed.span 回投到原文',
        ))
    else:
        problems: list[str] = []
        spans = [segment.span for segment in report.removed]
        ordered_spans = sorted(spans)
        if any(ordered_spans[index][1] > ordered_spans[index + 1][0]
               for index in range(len(ordered_spans) - 1)):
            problems.append(f'removed.span 彼此重叠：{ordered_spans}')
        if report.total_code_points != len(source.text):
            problems.append(
                f'total_code_points={report.total_code_points}，原文实际为 {len(source.text)}')
        for index, segment in enumerate(report.removed):
            lo, hi = segment.span
            if hi > len(source.text):
                problems.append(f'removed[{index}].span={segment.span} 越过原文末尾')
                continue
            piece = source.text[lo:hi]
            if piece.find(segment.marker_line) < 0:
                problems.append(
                    f'removed[{index}] 的 span 内找不到 marker_line「{segment.marker_line}」')
        if report.removed:
            if spans != sorted(spans) or any(
                    spans[i][1] != spans[i + 1][0] for i in range(len(spans) - 1)):
                problems.append('机器剔除 span 没有按原文顺序首尾相接')
            first, last = spans[0][0], spans[-1][1]
            if last != len(source.text):
                problems.append(f'机器剔除没有覆盖到原文末尾：最后止于 {last}')
            if clean_text != source.text[:first]:
                problems.append('机器轮 clean_text 不等于原文在首个剔除点之前的逐字前缀')
            rebuilt = rev.reconstruct_clean(source.text, list(report.removed))
            if rebuilt != clean_text:
                problems.append('clean_text 不是原文按 removed spans 的确定性重建')
        elif clean_text != source.text:
            problems.append('current 没有剔除，但 clean_text 与归一化原文不同')
        out.append(ran(
            's0_removal_ledger',
            [_hit('s0_removal_ledger', detail, detail) for detail in problems],
            scanned=f'{len(report.removed)} 个剔除段 · 原文 {len(source.text)} 码点',
        ))

    from novel.stages.stage0.eligibility import is_stage0_executable
    admission_findings = []
    if not is_stage0_executable(report):
        admission_findings.append(_hit(
            's0_approval',
            f'当前机械 admission 是 {report.admission.status!r}/'
            f'{report.admission.scope!r}，stage1 不得启动',
            report.admission.status,
            report.admission.scope,
        ))
    out.append(ran('s0_approval', admission_findings, scanned='1 个机械 admission'))

    if transform is None:
        out.append(unverifiable(
            's0_revision_chain',
            '手上没有 stage0/revisions.json（合成样本直接对快照判据），无法复算 transform',
        ))
    else:
        chain_problems: list[str] = []
        try:
            rev.verify_transform(transform, clean_text=clean_text)
        except Exception as exc:  # noqa: BLE001 — 判据要把失败落成 finding
            chain_problems.append(str(exc))
        if clean_text and transform.clean_sha1 != prov.content_sha1(
                clean_text.encode('utf-8')):
            chain_problems.append(
                'clean_text.txt 不是当前 transform 的投影；重跑 stage0.remove 可重新物化')
        out.append(ran(
            's0_revision_chain',
            [_hit('s0_revision_chain', detail, detail) for detail in chain_problems],
            scanned=f'transform_id={transform.transform_id[:12]}… · '
                    f'removed {len(transform.removed)} · admission='
                    f'{transform.admission.status}/{transform.admission.scope}',
        ))

    actual_clean_sha1 = prov.content_sha1(clean_text.encode('utf-8'))
    if report.clean_sha1 != actual_clean_sha1:
        hash_detail = f'clean_sha1 记录 {report.clean_sha1}，实际 {actual_clean_sha1}'
        out.append(ran(
            's0_hashes',
            [_hit('s0_hashes', hash_detail, hash_detail)],
            scanned='clean_sha1 已计算 · normalized_source_sha1 '
                    + ('已计算' if source is not None else '因原文不可用未计算'),
        ))
    elif source is None:
        out.append(unverifiable(
            's0_hashes',
            source_why or '没有原始小说，只能核对 clean_sha1，无法同时核对两个指纹',
            scanned='clean_sha1 已计算 · normalized_source_sha1 不可计算',
        ))
    else:
        hash_problems = []
        if report.normalized_source_sha1 != source.normalized_source_sha1:
            hash_problems.append(
                f'normalized_source_sha1 记录 {report.normalized_source_sha1}，'
                f'实际 {source.normalized_source_sha1}')
        out.append(ran(
            's0_hashes',
            [_hit('s0_hashes', detail, detail) for detail in hash_problems],
            scanned='normalized_source_sha1 + clean_sha1 两个 40 位内容指纹',
        ))

    human_problems = []
    human_segments = [i for i, segment in enumerate(report.removed)
                      if segment.origin == 'human']
    if human_segments:
        human_problems.append(
            f'removed{human_segments} 标成 human；v3 transform 禁止人工剔除段')
    out.append(ran(
        's0_human_preserved',
        [_hit('s0_human_preserved', detail, detail) for detail in human_problems],
        scanned=f'{len(report.removed)} 个 origin · revision_origin=machine',
    ))

    ratio = report.body_code_points / report.total_code_points
    out.append(ran(
        's0_body_ratio',
        [_hit(
            's0_body_ratio',
            f'清理后正文占比 {ratio:.1%}，低于 {BODY_RATIO_MIN:.0%}',
            f'{report.body_code_points}/{report.total_code_points}',
        )] if ratio < BODY_RATIO_MIN else [],
        scanned=f'{report.body_code_points}/{report.total_code_points}={ratio:.4f}',
    ))

    warning_findings = [
        _hit('s0_warnings', describe(warning), warning.kind, describe(warning))
        for warning in report.warnings
    ]
    out.append(ran(
        's0_warnings',
        warning_findings,
        scanned=f'按当前 transform 现算 {len(report.warnings)} 条可疑项',
    ))

    if source is None:
        out.append(unverifiable(
            's0_decode',
            source_why or '原始小说不可用，无法复核编码与无损解码',
        ))
    else:
        out.append(ran(
            's0_decode',
            [],
            scanned=f'原始小说 1 份 · 编码 {source.encoding}',
        ))

    norm_problems = []
    if clean_text.startswith('\ufeff'):
        norm_problems.append('clean_text 仍带 UTF-8 BOM 字符')
    if '\r' in clean_text:
        norm_problems.append('clean_text 仍含 CR/CRLF')
    out.append(ran(
        's0_normalization',
        [_hit('s0_normalization', detail, detail) for detail in norm_problems],
        scanned=f'口径 1 项 · clean_text {len(clean_text)} 码点',
    ))

    sid, title, _platform = paths.derive_source_meta(report.original_filename)
    dir_problems = paths.dirname_roundtrip_errors(title, sid)
    expected_dir = paths.novel_dirname(title, sid)
    if novel_dir is not None and novel_dir.name != expected_dir:
        dir_problems.append(
            f'当前书目录是 {novel_dir.name!r}，由 original_filename 推导应为 {expected_dir!r}')
    out.append(ran(
        's0_dir_roundtrip',
        [_hit('s0_dir_roundtrip', detail, detail) for detail in dir_problems],
        scanned=f'文件名 {report.original_filename!r} · 目录 {expected_dir!r}',
    ))

    if source is None:
        out.append(unverifiable(
            's0_source_identity',
            source_why or '原始小说不可用，无法判断是否被替换',
        ))
    else:
        out.append(ran(
            's0_source_identity',
            [_hit(
                's0_source_identity',
                '记录位置的原始小说内容身份已经变化',
                report.normalized_source_sha1,
                source.normalized_source_sha1,
            )] if report.normalized_source_sha1 != source.normalized_source_sha1 else [],
            scanned='原始小说 1 份 · 40 位归一化内容指纹',
        ))

    out.append(unverifiable(
        's0_correctness',
        '结构规则只能定位疑似非正文；剔除内容在语义上是否真是番外/后记必须由人阅读确认',
        scanned=f'{len(report.removed)} 个剔除段待人工语义确认',
    ))
    return out


def run_check_only(ctx: RunContext) -> CheckReport:
    '''从当前书目录复检 stage0；不写任何产物。'''
    store = ArtifactStore(ctx)
    transform = rev.read(store)
    if transform is None:
        from novel.errors import UpstreamMissing
        raise UpstreamMissing('stage0 还没跑，盘上没有 transform')
    report = rev.snapshot_of(transform)
    clean_text = store.read_text(CLEAN_REL)
    source, why = _source(report, ctx.novel_dir.parent)
    outcomes = check(
        report,
        clean_text,
        source=source,
        source_why=why,
        novel_dir=ctx.novel_dir,
        transform=transform,
    )
    return build(
        sid=ctx.sid,
        node=ctx.node,
        freshness=store.freshness(CLEAN_REL),
        outcomes=outcomes,
        accounting=ctx.accounting,
    )
