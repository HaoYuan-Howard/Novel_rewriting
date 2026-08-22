#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''stage0 的十三条判据实现。纯检查不改任何产物。'''
from __future__ import annotations

import os
from pathlib import Path

from contracts.invariants import BODY_RATIO_MIN
from contracts.schema.report import CheckReport, finding
from contracts.schema.stage0 import RemovalReport, Stage0Revisions
from novel import paths, prov
from novel.core.ingest import (
    DecodedSource,
    TEXT_NORM,
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
from novel.stages.stage0.removal import (
    CLEAN_REL,
    REPORT_REL,
    preview_text,
    warnings_for,
)


def _hit(rule: str, detail: str, *evidence: str):
    return finding(rule, detail=detail, evidence=list(evidence))


def _source(report: RemovalReport,
            base: str | os.PathLike[str]) -> tuple[DecodedSource | None, str | None]:
    """复检要拿来对账的那份原文；取不到就把**理由**带出去（判据退成 unverifiable）。

    ⚠ 记录的位置优先，只对界面上传的原文做 base 重锚（`novel/core/source.py`）。
      外部语料被删没有回落——那是契约有意接受的代价，不是这里该"救"的事。
    """
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
    report: RemovalReport,
    clean_text: str,
    *,
    source: DecodedSource | None,
    source_why: str | None = None,
    novel_dir: Path | None = None,
    revisions: Stage0Revisions | None = None,
) -> list[Outcome]:
    '''核对报告、正文、可选原文与书目录；每条注册判据恰好返回一个 Outcome。'''
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

    ledger_problems: list[str] = []
    if report.body_code_points != len(clean_text):
        ledger_problems.append(
            f'body_code_points={report.body_code_points}，clean_text 实际为 {len(clean_text)}')
    if source is None and ledger_problems:
        out.append(ran(
            's0_removal_ledger',
            [_hit('s0_removal_ledger', detail, detail) for detail in ledger_problems],
            scanned=f'clean_text {len(clean_text)} 码点 · 原文不可用，span 未能回投',
        ))
    elif source is None:
        out.append(unverifiable(
            's0_removal_ledger',
            source_why or '没有原始小说，无法把 removed.span 回投到原文',
        ))
    else:
        problems = ledger_problems
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
            if len(piece) != segment.code_points:
                problems.append(
                    f'removed[{index}].code_points={segment.code_points}，span 实际为 {len(piece)}')
            marker_at = piece.find(segment.marker_line)
            if marker_at < 0:
                problems.append(
                    f'removed[{index}] 的 span 内找不到 marker_line「{segment.marker_line}」')
            else:
                actual_line = source.text.count('\n', 0, lo + marker_at) + 1
                if actual_line != segment.line_no:
                    problems.append(
                        f'removed[{index}].line_no={segment.line_no}，原文实际为 {actual_line}')
                line_break = piece.find('\n', marker_at)
                after_marker = piece[line_break + 1:] if line_break >= 0 else ''
                expected_head = preview_text(after_marker, 80)
                if segment.head_preview != expected_head:
                    problems.append(
                        f'removed[{index}].head_preview 与 span 内标记后的原文不一致')
            expected_tail = preview_text(piece[-120:], 40)
            if segment.tail_preview != expected_tail:
                problems.append(
                    f'removed[{index}].tail_preview 与 span 尾部原文不一致')
        machine_only = all(segment.origin == 'machine' for segment in report.removed)
        if machine_only and report.removed:
            if spans != sorted(spans) or any(
                    spans[i][1] != spans[i + 1][0] for i in range(len(spans) - 1)):
                problems.append('机器剔除 span 没有按原文顺序首尾相接')
            first, last = spans[0][0], spans[-1][1]
            if last != len(source.text):
                problems.append(f'机器剔除没有覆盖到原文末尾：最后止于 {last}')
            if (report.revision_origin != 'human_edit'
                    and clean_text != source.text[:first]):
                problems.append('机器轮 clean_text 不等于原文在首个剔除点之前的逐字前缀')
        elif (machine_only and report.revision_origin != 'human_edit'
                and clean_text != source.text):
            problems.append('报告称没有剔除、没有人工编辑，但 clean_text 与归一化原文不同')
        out.append(ran(
            's0_removal_ledger',
            [_hit('s0_removal_ledger', detail, detail) for detail in problems],
            scanned=f'{len(report.removed)} 个剔除段 · 原文 {len(source.text)} 码点',
        ))

    approval_findings = []
    from novel.stages.stage0.eligibility import is_stage0_executable
    if not is_stage0_executable(report):
        approval_findings.append(_hit(
            's0_approval',
            f'当前批准状态是 {report.approval.status!r}，stage1 不得启动',
            report.approval.status,
        ))
    out.append(ran('s0_approval', approval_findings, scanned='1 个批准状态'))

    # ⚠ 2026-08-22 阶段 C：这条判据从「上一轮归档在不在盘上」换成「链自洽 + live 是它的
    #   物化投影」。`_rounds/` 与不可覆盖审计链一起下线之后，它接管了两个洞：
    #     ① 历史版本有没有被改过 —— 逐条复算 `revision_id`（前像含 parent ⇒ 哈希链）；
    #     ② 两份 live 产物是不是同一版 —— 那正是删掉 WAL 之后唯一会出现的中间态，
    #        而它可由「重跑一次任何 stage0 写路径」自愈，所以判据要说得出这句话。
    if revisions is None:
        out.append(unverifiable(
            's0_revision_chain',
            '手上没有 stage0/revisions.json（合成样本直接对报告判据），无法复算链',
        ))
    else:
        chain_problems: list[str] = []
        for item in revisions.revisions:
            expected = rev.revision_digest(
                parent_revision_id=item.parent_revision_id, origin=item.origin,
                actor=item.actor, reason=item.reason, content=item.content)
            if expected != item.revision_id:
                chain_problems.append(
                    f'revision {item.revision_id[:12]}… 的 id 与内容对不上，'
                    f'复算得到 {expected[:12]}…')
        current = rev.current_revision(revisions)
        if clean_text != current.content.clean_text:
            chain_problems.append(
                'clean_text.txt 不是 current 那一版的正文；重跑任一 stage0 写路径即可'
                '重新物化自愈')
        if report != rev.report_of(revisions):
            chain_problems.append(
                'removal_report.json 与链上 current 的物化投影不一致；'
                '重跑任一 stage0 写路径即可重新物化自愈')
        out.append(ran(
            's0_revision_chain',
            [_hit('s0_revision_chain', detail, detail) for detail in chain_problems],
            scanned=f'{len(revisions.revisions)} 条 revision 逐条复算 id · '
                    f'current={current.revision_id[:12]}… · '
                    f'候选 {"1" if revisions.candidate_revision_id else "0"} 条 · '
                    f'已否 {len(revisions.dismissed)} 条',
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
                f'normalized_source_sha1 记录 {report.normalized_source_sha1}，实际 {source.normalized_source_sha1}')
        out.append(ran(
            's0_hashes',
            [_hit('s0_hashes', detail, detail) for detail in hash_problems],
            scanned='normalized_source_sha1 + clean_sha1 两个 40 位内容指纹',
        ))

    human_problems = []
    human_segments = [i for i, segment in enumerate(report.removed)
                      if segment.origin == 'human']
    if human_segments and report.revision_origin != 'human_edit':
        human_problems.append(
            f'removed{human_segments} 标成 human，但这一版的起源是 '
            f'{report.revision_origin}（只有 human_edit 那一支会产生人工圈选）')
    if report.clean_sha1 != actual_clean_sha1:
        human_problems.append(
            '盘上 clean_text 已变化但报告没有同步登记；机器重跑不得把人工状态当作旧哈希')
    out.append(ran(
        's0_human_preserved',
        [_hit('s0_human_preserved', detail, detail) for detail in human_problems],
        scanned=f'{len(report.removed)} 个 origin · revision_origin={report.revision_origin}',
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

    # ⚠ 2026-08-22 起比**整条证据**而不只是 `kind`。此前只比 kind，于是一条
    #   `line_no` 或码点数记错的告警照样绿——而 warning 的全部价值就是那几个数。
    #   证据落盘之后这条复算才真的有判别力。
    expected_warnings = sorted(
        (warning.model_dump(mode='json') for warning in warnings_for(
            report.total_code_points, report.body_code_points, report.removed)),
        key=lambda item: sorted(item.items()))
    stored_warnings = sorted(
        (warning.model_dump(mode='json') for warning in report.warnings),
        key=lambda item: sorted(item.items()))
    warning_findings = [
        _hit('s0_warnings', describe(warning), warning.kind, describe(warning))
        for warning in report.warnings
    ]
    if stored_warnings != expected_warnings:
        warning_findings.append(_hit(
            's0_warnings',
            f'warnings={stored_warnings}，按当前数值与标记复算应为 {expected_warnings}',
            str(stored_warnings),
            str(expected_warnings),
        ))
    out.append(ran(
        's0_warnings',
        warning_findings,
        scanned=f'落盘 {len(report.warnings)} 条 · 复算 {len(expected_warnings)} 条可疑项'
                f'（逐条比证据，不只比 kind）',
    ))

    if source is None:
        out.append(unverifiable(
            's0_decode',
            source_why or '原始小说不可用，无法复核编码与无损解码',
        ))
    else:
        encoding_findings = []
        if report.source_encoding != source.encoding:
            encoding_findings.append(_hit(
                's0_decode',
                f'记录编码 {report.source_encoding!r}，当前无损解码为 {source.encoding!r}',
                report.source_encoding,
                source.encoding,
            ))
        out.append(ran(
            's0_decode',
            encoding_findings,
            scanned=f'原始小说 1 份 · 编码 {source.encoding}',
        ))

    norm_problems = []
    if report.text_norm != TEXT_NORM:
        norm_problems.append(
            f'text_norm={report.text_norm!r}，当前口径应为 {TEXT_NORM!r}')
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
    '''从当前书目录复检 stage0；不更新批准状态、不写任何产物。'''
    store = ArtifactStore(ctx)
    report, _raw = store.read_own(RemovalReport, REPORT_REL)
    clean_text = store.read_text(CLEAN_REL)
    source, why = _source(report, ctx.novel_dir.parent)
    outcomes = check(
        report,
        clean_text,
        source=source,
        source_why=why,
        novel_dir=ctx.novel_dir,
        revisions=rev.read(store),
    )
    return build(
        sid=ctx.sid,
        node=ctx.node,
        freshness=store.freshness(REPORT_REL),
        outcomes=outcomes,
        accounting=ctx.accounting,
    )
