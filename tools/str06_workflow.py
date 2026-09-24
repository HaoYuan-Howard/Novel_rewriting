"""STR-06 隔离入口：source JSON → Stage2(+names) → Preserve/Design/Hook/Bind → Compile+Guard+WriterIR
→ 按章正文 → Hook 成文 → Naming → Assembly（F5/F6；F7a-2 接正式设施）。

--wire-dir 显式采用已购/人工修订 wire，逐份记录字节 SHA；owner wire 缺失时走付费
边界：同请求身份结果优先免费采用（缓存先于授权），缺失且未授权停在该 owner 并
报告需要授权（PurchaseRequired）。--run-dir 固定重用。

F5 起的腿边界：Stage4 完整发布 graph 与 WriterBookIR 后结束（零正文购买）；
Stage5 完成正文（严格按章串行，--body-chapter-limit 限定前缀）与 Hook；
Stage7 由真实 Assembly 产生交付（文件先落盘、manifest 最后提交）。
--check-only 强制不新购：只做免费采用与免费重放。

F7a-2：候选根首次运行初始化 workflow／epoch 身份（runtime/candidate_workflow.json），
之后原 epoch 复用、代码漂移滚动、源输入变化拒绝；付费边界经正式工作单元与整书
总闸记账，ledger 行带候选节点归属。此入口不切换现役工作流（删除条件 STR-06-F8）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import NamedTuple

from contracts.schema.str06 import STR06_RUNTIME_CONFIG
from contracts.schema.str06.hook import HookConstraints
from novel.config import load_config
from novel.llm.str06_run_context import init_or_resume_candidate_workflow
from novel.llm.str06_requests import PaidRequestSession, PurchaseRequired, digest
from novel.llm.transport import ChatRequest, DeepSeekTransport
from novel import paths, prov
from novel.stages.str06.body_writer import AnsweredText
from novel.stages.str06.prompts import SYS, BODY_WRITER_SYSTEM, HOOK_WRITER_SYSTEM
from novel.stages.str06.runner import ChainContext, derive_total_length_budget, run_chain
from novel.store import Str06Repository
from novel.work_units import require_workflow


class Stage1Input(NamedTuple):
    """候选链的 Stage1 源输入：章数组 + 盘上字节身份 + 根 SHA-1（F7c 生产输入路径）。

    输入装配属工具层（薄驱动）：读正式 Stage1 章数组要经 `novel.paths` 定位书目录、
    经正式契约解码——迁移包（novel/stages/str06）按纪律不 import 现役设施。
    """

    chapters: list[dict]
    source_identity: str      # stage1/main_text.json 盘上字节 SHA-256
    root_sha1: str            # 同一字节的 SHA-1（provenance 的根身份格）


def load_stage1_chapters(base, sid: str) -> Stage1Input:
    """从正式 Stage1 章数组读候选链源输入（`--book-sid` 生产路径）。

    章数组即全链唯一章节键（阅读序 `chapter_ordinal`，从 1 连续）；目标章数等于
    源章数由 `MainText` 模型自身保证。`stage1/hook_copy.json` 在新链没有消费者
    （Hook 设计吃目标 Design 的材料投影），退役边界登记在 REQUIREMENTS。
    """
    from contracts.artifacts import STAGE1_MAIN_TEXT
    from contracts.schema import artifact_spec, decode_artifact
    book_dir = paths.resolve_novel_dir(Path(base), sid)
    raw = (book_dir / STAGE1_MAIN_TEXT).read_bytes()
    decoded = decode_artifact(STAGE1_MAIN_TEXT, json.loads(raw.decode('utf-8-sig')))
    model = artifact_spec(STAGE1_MAIN_TEXT).model.model_validate(decoded.payload)
    chapters = [{'chapter_ordinal': section.chapter_ordinal, 'text': section.text}
                for section in model.root]
    if [c['chapter_ordinal'] for c in chapters] != list(range(1, len(chapters) + 1)):
        raise ValueError('Stage1 章数组必须为非空连续章序列表')
    return Stage1Input(chapters=chapters,
                       source_identity=hashlib.sha256(raw).hexdigest(),
                       root_sha1=prov.content_sha1(raw))


def run_workflow(*, repository, chapters, ask, ask_text, ask_body, budget=11000,
                 through='stage4', provider='manual-adoption', model='unknown',
                 book_title='', hook_constraints=None, length_band=(0.6, 1.3),
                 body_chapter_limit=None, node_filter=None):
    """记录显式执行状态；失败保留旧产物供恢复，但不能仍宣称上次绿灯有效。

    流程编排已归 owner runner（novel/stages/str06/runner.py）：本函数只装配
    ChainContext、驱动节点计划并落 run 级状态（F7b 薄驱动）。
    """
    hook_constraints = hook_constraints or HookConstraints(
        target_chars=280, hard_max_chars=360)
    ctx = ChainContext(
        repository=repository, ask=ask, ask_text=ask_text, ask_body=ask_body,
        chapters=chapters, budget=budget, book_title=book_title,
        hook_constraints=hook_constraints, length_band=length_band,
        body_chapter_limit=body_chapter_limit, provider=provider, model=model,
        digest=digest)
    status_rel = 'runtime/workflow_status.json'
    identity = digest({'chapters': chapters, 'budget': budget, 'through': through,
                       'band': length_band, 'body_limit': body_chapter_limit})
    repository.write(status_rel, {'status': 'running', 'input_sha256': identity,
                                  'accepted_body': False})
    try:
        report, final_status, accepted_body = run_chain(
            ctx, through=through, node_filter=tuple(node_filter) if node_filter else None)
    except PurchaseRequired as exc:
        repository.write(status_rel, {
            'status': 'needs_authorization', 'input_sha256': identity,
            'accepted_body': False, 'blocked_owner': exc.owner, 'detail': str(exc)})
        raise
    except Exception as exc:
        # run_chain 已按候选节点留痕（failed_node）；此处只补 run 级身份，
        # 不覆盖精确 owner 归属。
        status = {}
        try:
            status = repository.read(status_rel)
        except FileNotFoundError:
            pass
        status.update({'status': 'failed', 'input_sha256': identity,
                       'accepted_body': False,
                       'failure_kind': type(exc).__name__, 'detail': str(exc)})
        repository.write(status_rel, status)
        raise
    repository.write(status_rel, {'status': final_status, 'input_sha256': identity,
                                  'accepted_body': accepted_body, 'through': through})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-json', type=Path,
                        help='开发／测试用源章 JSON；生产路径用 --book-sid 读正式 Stage1')
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--wire-dir', type=Path)
    parser.add_argument('--repair-manifest', type=Path)
    parser.add_argument('--through', choices=['stage2', 'stage4', 'stage5', 'stage7'],
                        default='stage4')
    parser.add_argument('--book-sid', help='正式 Stage1 输入的书 id（生产路径：'
                                         '读 <base>/<sid>/stage1/main_text.json）')
    parser.add_argument('--budget', type=int, default=None,
                        help='书级正文篇幅目标；缺省从源章数组总字数确定性派生'
                             '（保留全书篇幅边界，见 STR06_RUNTIME_CONFIG）')
    parser.add_argument('--hook-target-chars', type=int,
                        default=STR06_RUNTIME_CONFIG.hook_target_chars)
    parser.add_argument('--hook-hard-max', type=int,
                        default=STR06_RUNTIME_CONFIG.hook_hard_max_chars)
    parser.add_argument('--body-band-low', type=float,
                        default=STR06_RUNTIME_CONFIG.length_band_low)
    parser.add_argument('--body-band-high', type=float,
                        default=STR06_RUNTIME_CONFIG.length_band_high)
    parser.add_argument('--body-chapter-limit', type=int,
                        help='只生成前 N 章（前缀式）；缺省为全书')
    parser.add_argument('--book-call-ceiling', type=int,
                        help='本次运行允许的新购调用上限（锁内核对；缓存采用不计）')
    parser.add_argument('--book-title', default='')
    parser.add_argument('--reasoning-effort', default='high',
                        help='JSON/正文请求的思考档位；high 档 reasoning 可能耗尽 '
                             'max_tokens 导致响应截断（F5e 实证），可显式降档')
    parser.add_argument('--check-only', action='store_true',
                        help='免费检查：只允许缓存采用与 wire 采用，不签发新调用')
    parser.add_argument('--adopt-root', type=Path, action='append', default=[],
                        help='登记的显式历史采用源根（候选 run 目录，可重复）：'
                             '同请求身份的已购结果经身份／字节／成本关联核对后零新购 '
                             '采用；身份不符、证据损坏或付费状态未知明确阻断')
    parser.add_argument('--node', action='append', default=[],
                        help='只跑指定候选节点（可重复；单节点重跑与定向复检）')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--allow-source-export', action='store_true')
    args = parser.parse_args()
    if not args.book_sid and args.source_json is None:
        raise ValueError('必须给出 --book-sid（正式 Stage1 输入）或 --source-json（开发路径）')
    if args.hook_target_chars > args.hook_hard_max:
        raise ValueError('--hook-target-chars 不得大于 --hook-hard-max')
    if not 0 < args.body_band_low < args.body_band_high:
        raise ValueError('--body-band-low/high 必须满足 0 < low < high')
    if args.check_only and (args.yes or args.allow_source_export):
        raise ValueError('--check-only 不签发新调用：去掉 --yes/--allow-source-export 再跑')
    for root in args.adopt_root:
        if not root.resolve().is_dir():
            raise ValueError(f'--adopt-root 不是目录：{root}')
    base = os.environ.get('NOVEL_OUTPUT')
    if not base:
        raise ValueError('NOVEL_OUTPUT 未设置：没查、未运行')
    base_path = Path(base).resolve()
    run_dir = args.run_dir.resolve()
    if not run_dir.is_relative_to(base_path):
        raise ValueError('--run-dir 必须位于 NOVEL_OUTPUT 下的隔离根')
    # 一个 NOVEL_OUTPUT 根一个候选链：facilities 的 sid 取 run 目录名。
    sid = run_dir.relative_to(base_path).as_posix().replace('/', '_')
    repository = Str06Repository(run_dir)
    if args.book_sid:
        # 生产路径：正式 Stage1 章数组（F7c）。
        stage1 = load_stage1_chapters(base_path, args.book_sid)
        chapters, source_identity, root_sha1 = (
            stage1.chapters, stage1.source_identity, stage1.root_sha1)
    else:
        source_bytes = args.source_json.read_bytes()
        source_identity = hashlib.sha256(source_bytes).hexdigest()
        root_sha1 = prov.content_sha1(source_bytes)
        chapters = repository.read_external(args.source_json)
    if args.budget is None:
        args.budget = derive_total_length_budget(
            chapters, rounding=STR06_RUNTIME_CONFIG.length_budget_rounding)
    adopted = {}
    if args.repair_manifest:
        manifest = repository.read_external(args.repair_manifest)
        source_input = manifest['source_input']
        if (Path(source_input['path']).resolve() != args.source_json.resolve()
                or repository.file_sha256(args.source_json) != source_input['sha256']):
            raise ValueError('owner patch 源章输入身份不匹配')
        adopted = {entry['owner']: entry['result'] for entry in manifest['files']}
        if len(adopted) != len(manifest['files']):
            raise ValueError('owner patch manifest 中 owner 重复')
        for entry in manifest['files']:
            for field in ('base', 'result'):
                item = entry[field]
                actual = repository.file_sha256(Path(item['path']))
                if actual != item['sha256']:
                    raise ValueError(f'owner patch {entry["owner"]} 的 {field} 身份变化，拒绝采用')
        repository.write('runtime/repair_manifest.json', manifest)
    cfg = load_config()
    # workflow／epoch 身份：首开落记录，复用不换身份，代码漂移滚动，源输入变化拒绝。
    workflow, context = init_or_resume_candidate_workflow(
        repository=repository, base=base_path, code=prov.code_version(),
        root_sha1=root_sha1, source_identity=source_identity,
        task_id=require_workflow(base_path).task_id,
        configured_model='unknown' if args.wire_dir else cfg.model,
        created_at=int(time.time()), readonly=args.check_only)
    ledger = context.ledger
    session = PaidRequestSession(repository, ledger, DeepSeekTransport(cfg), yes=args.yes,
                                 allow_source_export=args.allow_source_export,
                                 provider=cfg.base_url,
                                 book_call_ceiling=args.book_call_ceiling, sid=sid,
                                 adopt_roots=tuple(args.adopt_root))

    def adopt(owner, path, validate, *, system, prompt, json_mode, temperature):
        if args.repair_manifest and (
                owner not in adopted or Path(adopted[owner]['path']).resolve() != path.resolve()
                or repository.file_sha256(path) != adopted[owner]['sha256']):
            raise ValueError(f'{owner} wire 不在已核实修订 manifest 内')
        raw = repository.read_external(path)
        errors = validate(raw)
        record = {
            'owner': owner, 'path': str(path.resolve()),
            'sha256': repository.file_sha256(path),
            'new_paid_calls': 0, 'historical_usage': None, 'errors': errors}
        if errors:
            repository.write(f'runtime/adopt_{owner}.json', record)
            raise ValueError(f'{owner} 免费采用失败：{errors}')
        # F7a-3：wire 采用登记身份／成本绑定——注册源根有同请求身份已购行时三方
        # 绑定（身份／响应／成本），否则退到采用链（批次口径）或如实标未核。
        model = 'unknown'
        if args.adopt_root:
            request = build_request(owner, prompt, json_mode=json_mode, system=system,
                                    temperature=temperature)
            identity = digest({'owner': owner, 'provider': session.provider,
                               'request': request.cache_payload()})
            allow_patch, patch_reason = False, None
            note_path = (args.wire_dir / f'{owner}_patch_note.json'
                         if args.wire_dir else None)
            if note_path is not None and note_path.exists():
                note = repository.read_external(note_path)
                if note.get('base_response_identity') != identity:
                    raise ValueError(
                        f'{owner} patch note 的基线身份与当前请求不一致，拒绝采用')
                allow_patch, patch_reason = True, note.get('reason')
            record.update(session.bind_wire_adoption(owner, identity, path,
                                                     allow_patch=allow_patch))
            if patch_reason:
                record['owner_patch_reason'] = patch_reason
            model = record.get('model') or 'unknown'
        repository.write(f'runtime/adopt_{owner}.json', record)
        context.note_adoption(owner=owner, model=model,
                              prompts={'system': system, 'user': prompt})
        return raw

    def wire_path(owner):
        if not args.wire_dir:
            return None
        path = args.wire_dir / f'{owner}_wire.json'
        return path if path.exists() else None

    def build_request(owner, prompt, *, json_mode, system, temperature):
        from novel.llm.budget import enforce_request_budget
        request = ChatRequest(model=cfg.model, system=system, user=prompt,
                              reasoning_effort=args.reasoning_effort,
                              thinking=cfg.thinking_mode == 'on',
                              json_mode=json_mode, max_tokens=65536, temperature=temperature)
        if not args.wire_dir:
            # 完整输入超限就阻断：精确 tokenizer + 官方 framing，不摘要、不截断。
            enforce_request_budget([request], node='str06', purpose=owner)
        return request

    def ask(owner, prompt, validate):
        path = wire_path(owner)
        if path is not None:
            return adopt(owner, path, validate, system=SYS, prompt=prompt,
                         json_mode=True, temperature=0.3)
        request = build_request(owner, prompt, json_mode=True, system=SYS, temperature=0.3)
        return session.ask(owner, request, validate)

    def ask_text(owner, prompt):
        path = wire_path(owner)
        if path is not None:
            raw = adopt(owner, path, lambda w: [], system=HOOK_WRITER_SYSTEM,
                        prompt=prompt, json_mode=False, temperature=0.7)
            prose = (raw.get('prose') or '').strip() if isinstance(raw, dict) else ''
            if not prose:
                raise ValueError(f'{owner} wire 文本为空')
            return prose, [], 'wire-adoption'
        request = build_request(owner, prompt, json_mode=False, system=HOOK_WRITER_SYSTEM,
                                temperature=0.7)
        answered = session.ask_answered(owner, request)
        return answered.text, answered.errors, answered.identity

    def ask_body(owner, prompt):
        path = wire_path(owner)
        if path is not None:
            raw = adopt(owner, path, lambda w: [], system=BODY_WRITER_SYSTEM,
                        prompt=prompt, json_mode=False, temperature=0.7)
            prose = (raw.get('prose') or '').strip() if isinstance(raw, dict) else ''
            if not prose:
                raise ValueError(f'{owner} wire 文本为空')
            return AnsweredText(text=prose, finish_reason=None, identity='wire-adoption',
                                cached=True)
        request = build_request(owner, prompt, json_mode=False, system=BODY_WRITER_SYSTEM,
                                temperature=0.7)
        answered = session.ask_answered(owner, request)
        return AnsweredText(text=answered.text, finish_reason=answered.finish_reason,
                            identity=answered.identity, cached=answered.cached)

    print(run_workflow(repository=repository, chapters=chapters, ask=ask, ask_text=ask_text,
                       ask_body=ask_body, budget=args.budget, through=args.through,
                       node_filter=tuple(args.node) or None,
                       provider='manual-adoption' if args.wire_dir else cfg.base_url,
                       model='unknown' if args.wire_dir else cfg.model,
                       book_title=args.book_title,
                       hook_constraints=HookConstraints(target_chars=args.hook_target_chars,
                                                         hard_max_chars=args.hook_hard_max),
                       length_band=(args.body_band_low, args.body_band_high),
                       body_chapter_limit=args.body_chapter_limit))


if __name__ == '__main__':
    main()
