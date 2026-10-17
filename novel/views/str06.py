"""str06 —— STR-06 候选链的只读监控视图（F7d；F8 激活前不进现役注册表）。

【这一层只【读】】全部函数纯读：不写盘、不发模型请求、不判定。盘上事实
（产物在不在、回执写了什么、ledger 记了什么）原样投影；「这本书跑到哪了」
的唯一业务状态来源是候选根自己的 `runtime/workflow_status.json` 与
`runtime/candidate_workflow.json`——节点表的 `produced` 只是 I/O 事实，
不充当多分支业务状态（I-32）。

【候选根的定位】一个候选根是输出根下的一个目录：`run/` 放书产物、
`_service/ledger.jsonl` 放成本账、`runtime/candidate_workflow.json` 是它
的身份标记。本模块不认「猜测的路径」：没有身份记录的目录不是候选根，
监控面上不出现。

【读口用 inspect 不用 read】生产消费走 `Str06Repository.read`（记来源边、
判过期）；监控是检查，走 `inspect`——一次页面刷新不该往下一份产物的
来源账里写边（同正式 store 的 `full_sha256_or_none` 分工）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from contracts.schema.api import (
    Str06AssemblyOutcomeView, Str06AssemblyReportView,
    Str06BlueprintBeatView, Str06BlueprintChapterView, Str06BlueprintView,
    Str06BodyAccessView, Str06BodyFailureChapterView, Str06ChapterFailuresView,
    Str06CandidateList, Str06CandidateSummary, Str06ChapterReceiptView,
    Str06ChapterView, Str06CharacterView, Str06CharactersView,
    Str06DesignBindingView, Str06DesignChapterView, Str06DesignHookView,
    Str06DesignSceneView, Str06DesignView, Str06DeliveryChapterView,
    Str06DeliveryView, Str06EpochView, Str06HookView, Str06IrChapterView,
    Str06IrNavView, Str06LedgerCostView, Str06LedgerNodeCostView,
    Str06NameStructureView, Str06NamingEntryView, Str06NamingView, Str06NodeView,
    Str06OwnerIssueItemView, Str06OwnerIssueRecordView, Str06OwnerIssuesView,
    Str06RunView, Str06SurfaceWindowView,
)
from contracts.invariants import NODES, OWNER_REVISION_MAX_ROUNDS
from contracts.schema.workflow import candidate_node_owner
from contracts.schema.hook import HookDesign
from novel.errors import ArtifactUnreadable
from novel.core.name_text import resolve_name_text
from novel.stages.common import hook_organization_summary
from novel.store import Str06Repository
from novel.work_unit_usage import read_cost_snapshot

#: 候选根内运行目录的固定名（`novel/stages/chain.py::execute` 的 `--run-dir` 约定；
#: 直接就是把书产物放在候选根下的旧布局也认，见 `_run_dir`）。
RUN_DIRNAME = 'run'
#: 候选 workflow 身份记录（候选根的身份标记）。
WORKFLOW_REL = 'runtime/candidate_workflow.json'
#: run 级执行状态（业务状态的唯一来源）。
STATUS_REL = 'runtime/workflow_status.json'
#: 章级局部失败留证（D3/W2：`str06.stage5.body` 逐章执行器收敛后按章落盘）。
BODY_FAILURES_REL = 'runtime/body_failures.json'
#: 章观察的逐章失败留证（D3/W2：`str06.stage2.chapters`，与正文同形）。
CHAPTER_OBSERVATION_FAILURES_REL = 'runtime/chapter_observation_failures.json'
#: 跨节点回派账目录（R3：`runtime/issues/<owner>.json`，每 owner 一份）。
ISSUES_DIR_REL = 'runtime/issues'
#: Stage7 集合核对报告（B4 后半：五态按 item）。
ASSEMBLY_REPORT_REL = 'runtime/delivery/assembly_report.json'
#: 章序模式（与候选产物表的 `{nn}` 一致）。
#: 书名模式（与候选产物表的 `{title}` 一致）。
_TITLE = re.compile(r'^(.*)_rewriting\.txt$')


#: 候选名必须是单个安全路径段（拒绝 `..`／分隔符／空段：候选名来自 URL）。
_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')


def _run_dir(base: str | os.PathLike[str], candidate: str) -> Path | None:
    """定位候选根的运行目录；不是候选根（没有身份记录）返回 None。"""
    if not _NAME_RE.match(candidate):
        return None
    root = Path(base) / candidate
    for run in (root / RUN_DIRNAME, root):
        if (run / WORKFLOW_REL).is_file():
            return run
    return None


def _candidates_base(candidate_root: Path) -> Path:
    """候选输出根（ledger 与 workflow 设施的落点）：run 目录的上一级。"""
    return candidate_root.parent if candidate_root.name == RUN_DIRNAME else candidate_root


def _repo(run: Path) -> Str06Repository:
    return Str06Repository(run)


def _inspect(run: Path, rel: str) -> Any | None:
    """只读检查一份 JSON 产物；缺席返回 None（不冒充合法空值）。"""
    try:
        return _repo(run).inspect(rel)
    except FileNotFoundError:
        return None


def _text(run: Path, rel: str) -> str | None:
    try:
        return _repo(run).path(rel).read_text(encoding='utf-8')
    except FileNotFoundError:
        return None


def _pattern_dir(run: Path, rel_pattern: str) -> Path | None:
    """产物模式的前缀目录（`ch{nn}.txt`／`{title}_rewriting.txt` 的列举用）。

    目录列举只读、且做越界检查；不走 `str06_artifact_path` 的家族准入——
    那是**产物文件**的写侧规则，对只读目录列举是另一件事。模式本身来自候选
    产物表（不是用户输入），用户输入只有候选名（`_run_dir` 已校验）。
    """
    root = run.resolve()
    prefix = rel_pattern.rsplit('/', 1)[0] if '/' in rel_pattern else ''
    directory = (root / prefix).resolve()
    if not directory.is_relative_to(root) or not directory.is_dir():
        return None
    return directory


#: 逐章产物文件名的前缀/后缀从模式解析（`ch{nn}.txt`／`ch{nn}.json` 同一扫描器）。
_CHAPTER_PREFIX = 'ch'


def _chapter_name_re(rel_pattern: str) -> re.Pattern[str]:
    """`stage5/body/ch{nn}.txt` → ``^ch(\\d+)\\.txt$``（后缀随模式，不写死）。"""
    tail = rel_pattern.rsplit('/', 1)[-1]
    suffix = re.escape(tail.split('{nn}', 1)[1])
    return re.compile(rf'^{_CHAPTER_PREFIX}(\d+){suffix}$')


def _chapter_files(run: Path, rel_pattern: str) -> list[tuple[int, Path]]:
    """把 `ch{nn}.txt`／`ch{nn}.json` 模式解析成盘上实际的 (章序, 路径)，按章序排序。"""
    directory = _pattern_dir(run, rel_pattern)
    if directory is None:
        return []
    name_re = _chapter_name_re(rel_pattern)
    found = []
    for entry in sorted(directory.iterdir()):
        match = name_re.match(entry.name)
        if match and entry.is_file():
            found.append((int(match.group(1)), entry))
    return sorted(found)


def _titled_files(run: Path, rel_pattern: str) -> list[Path]:
    """把 `{title}_rewriting.txt` 模式解析成盘上实际的成书文件。"""
    directory = _pattern_dir(run, rel_pattern)
    if directory is None:
        return []
    return sorted(p for p in directory.iterdir()
                  if p.is_file() and _TITLE.match(p.name))


def _node_produced(run: Path, node_id: str) -> bool:
    """节点产物在盘上吗（I/O 事实）：produces 里任一模式有落盘文件即真。"""
    for rel in NODES[node_id].produces:
        if '{nn}' in rel:
            if _chapter_files(run, rel):
                return True
        elif '{title}' in rel:
            if _titled_files(run, rel):
                return True
        elif _repo(run).path(rel).is_file():
            return True
    return False


# ─────────────────────────────── 候选根清单 ───────────────────────────────

def list_candidates(base: str | os.PathLike[str]) -> Str06CandidateList:
    """输出根下的全部候选根（以 workflow 身份记录为标记），按名字排序。"""
    root = Path(base)
    if not root.is_dir():
        return Str06CandidateList(candidates=())
    summaries = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        run = _run_dir(root, entry.name)
        if run is None:
            continue
        status = _inspect(run, STATUS_REL) or {}
        summaries.append(Str06CandidateSummary(
            candidate=entry.name, present=True,
            status=status.get('status'),
            updated_at=int(run.joinpath(WORKFLOW_REL).stat().st_mtime)))
    return Str06CandidateList(candidates=tuple(summaries))


# ─────────────────────────────── Stage2 面 ───────────────────────────────

def characters_view(base: str | os.PathLike[str], candidate: str) -> Str06CharactersView:
    """Characters v6：人物条目（称谓时间窗）＋源名称结构。"""
    run = _run_dir(base, candidate)
    if run is None:
        return Str06CharactersView(present=False)
    payload = _inspect(run, 'stage2/characters/characters.json')
    if payload is None:
        return Str06CharactersView(present=False)
    characters = tuple(
        Str06CharacterView(
            char_id=char_id, canonical=entry['canonical'],
            surfaces=tuple(
                Str06SurfaceWindowView(surface=s['surface'],
                                       from_chapter=s['from_chapter'],
                                       until_chapter=s.get('until_chapter'))
                for s in entry['surfaces']))
        for char_id, entry in sorted(payload.get('characters', {}).items()))
    structure = payload.get('name_structure')
    return Str06CharactersView(
        present=True, contract_version=payload.get('contract_version'),
        characters=characters,
        name_structure=Str06NameStructureView(
            families={k: v['literal'] for k, v in sorted(
                (structure.get('families') or {}).items())},
            people=dict(sorted((structure.get('people') or {}).items())))
        if structure else None)


def blueprint_view(base: str | os.PathLike[str], candidate: str) -> Str06BlueprintView:
    """Blueprint v17：世界约束＋逐章 beats（三类转移、场景边界与时间模式、场景分段）与因果边。"""
    run = _run_dir(base, candidate)
    if run is None:
        return Str06BlueprintView(present=False)
    payload = _inspect(run, 'stage2/blueprints/chapter_blueprints.json')
    if payload is None:
        return Str06BlueprintView(present=False)
    chapters = tuple(
        Str06BlueprintChapterView(
            chapter=chapter['chapter'],
            beats=tuple(
                Str06BlueprintBeatView(
                    beat_id=b['beat_id'], event=b['event'],
                    participants=tuple(b.get('participants') or ()),
                    effects=tuple(b.get('effects') or ()),
                    cognition=tuple(b.get('cognition') or ()),
                    loops=tuple(b.get('loops') or ()),
                    scene_boundary=b.get('scene_boundary'),
                    temporal_mode=b.get('temporal_mode'))
                for b in chapter['beats']),
            scenes=tuple(s.model_dump(mode='json') if hasattr(s, 'model_dump') else s
                         for s in (chapter.get('scenes') or ())),
            causal_edges=tuple(chapter.get('causal_edges') or ()))
        for chapter in payload.get('chapters') or ())
    return Str06BlueprintView(
        present=True,
        world_constraints=tuple(payload.get('world_constraints') or ()),
        chapters=chapters)


# ─────────────────────────────── Stage4 面 ───────────────────────────────

def _display_name_text(text: str, *, field: str) -> str:
    """只在明确的监控文本叶子解码；机器引用和源证据不经过本函数。"""
    decoded = resolve_name_text(text)
    if decoded.errors:
        codes = ', '.join(dict.fromkeys(issue.code for issue in decoded.errors))
        raise ArtifactUnreadable(f'{field} 的身份文本语法损坏，显示不可读（{codes}）')
    return decoded.text


def _display_fields(value: dict, fields: tuple[str, ...], *, path: str) -> dict:
    result = dict(value)
    for field in fields:
        if isinstance(result.get(field), str):
            result[field] = _display_name_text(result[field], field=f'{path}.{field}')
    return result


def _display_design_beat(beat: dict, *, path: str) -> dict:
    result = _display_fields(beat, ('event', 'delivery_hint'), path=path)
    if 'target_delta' in beat:
        result['target_delta'] = [
            _display_fields(delta, ('value',), path=f'{path}.target_delta[{index}]')
            for index, delta in enumerate(beat['target_delta'])]
    if 'threads' in beat:
        result['threads'] = [
            _display_fields(thread, ('text', 'progress', 'resolution'), path=f'{path}.threads[{index}]')
            for index, thread in enumerate(beat['threads'])]
    return result

def design_view(base: str | os.PathLike[str], candidate: str) -> Str06DesignView:
    """graph／binding：场景驱动与导演说明、章目的、Hook 正式选择与 Binding 条目。"""
    run = _run_dir(base, candidate)
    if run is None:
        return Str06DesignView(present=False)
    graph = _inspect(run, 'stage4/design/presentation_graph.json')
    if graph is None:
        return Str06DesignView(present=False)
    scenes = tuple(
        Str06DesignSceneView(
            scene_id=s['scene_id'], driver=_display_fields(s['driver'],
                ('immediate_want', 'opposition', 'turn'), path=f'{s["scene_id"]}.driver'),
            realization_brief=_display_name_text(s['realization_brief'], field=f'{s["scene_id"]}.realization_brief'),
            pov=s['delivery']['pov'], voice=_display_name_text(s['delivery']['voice'], field=f'{s["scene_id"]}.delivery.voice'),
            avoid=tuple(_display_name_text(text, field=f'{s["scene_id"]}.delivery.avoid[{index}]')
                        for index, text in enumerate(s['delivery'].get('avoid') or ())),
            beats=tuple(_display_design_beat(beat, path=f'{s["scene_id"]}#{beat["beat_id"]}')
                        for beat in s['beats']))
        for s in graph.get('scenes') or ())
    chapters = tuple(
        Str06DesignChapterView(chapter_ordinal=c['chapter_ordinal'],
                               purpose=_display_name_text(c['purpose'], field=f'chapter:{c["chapter_ordinal"]}.purpose'),
                               scene_ids=tuple(c['scene_ids']))
        for c in graph.get('chapters') or ())
    hook_payload = graph.get('hook')
    hook = None
    if hook_payload:
        try:
            brief = hook_organization_summary(HookDesign.model_validate(hook_payload))
        except ValidationError:
            # 未升级旧根上的 hook 早于当前契约形状：只读视图不因此 500——
            # 选择本身原样投影，组织说明如实标「不可推导」，不猜内容。
            brief = '（盘上 Hook 形状早于当前契约，组织说明不可推导；经登记升级重建后恢复）'
        hook = Str06DesignHookView(
            person=hook_payload['pov']['person'],
            focalizers=tuple(hook_payload['pov']['focalizers']),
            materials=tuple(m['beat_ref'] for m in hook_payload['materials']),
            concealed=tuple(hook_payload.get('concealed') or ()),
            forbidden_reveals=len(hook_payload.get('forbidden_reveals') or ()),
            design_brief=_display_name_text(brief, field='hook.design_brief'))
    binding_payload = _inspect(run, 'stage4/grounding/semantic_binding.json')
    binding = (Str06DesignBindingView(
        obligations=len(binding_payload.get('obligations') or ()),
        entries=tuple(binding_payload.get('entries') or ()),
        unbound=tuple(binding_payload.get('unbound') or ()))
        if binding_payload is not None else None)
    return Str06DesignView(present=True, graph_id=graph.get('graph_id'),
                           scenes=scenes, chapters=chapters, hook=hook,
                           binding=binding)


def ir_nav_view(base: str | os.PathLike[str], candidate: str) -> Str06IrNavView:
    """WriterBookIR 导航：章预算、章目的、逐场摘要行与称谓释放位置表。"""
    run = _run_dir(base, candidate)
    if run is None:
        return Str06IrNavView(present=False)
    payload = _inspect(run, 'stage4/ir/writer_book_ir.json')
    if payload is None:
        return Str06IrNavView(present=False)
    brief = payload.get('book_brief') or {}
    chapters = tuple(
        Str06IrChapterView(
            chapter_ordinal=nav['chapter_ordinal'], purpose=_display_name_text(
                nav['purpose'], field=f'ir.chapter:{nav["chapter_ordinal"]}.purpose'),
            length_budget=next(c['length_budget'] for c in payload['chapters']
                               if c['chapter_ordinal'] == nav['chapter_ordinal']),
            scene_ids=tuple(nav['scene_ids']),
            scene_lines=tuple(_display_name_text(text,
                field=f'ir.chapter:{nav["chapter_ordinal"]}.scene_lines[{index}]')
                for index, text in enumerate(nav['scene_lines'])))
        for nav in brief.get('chapters') or ())
    return Str06IrNavView(
        present=True, book_title=brief.get('book_title') or '',
        target_chapter_count=brief.get('target_chapter_count'),
        total_length_budget=brief.get('total_length_budget'),
        length_band_low=brief.get('length_band_low'),
        length_band_high=brief.get('length_band_high'),
        chapters=chapters,
        release_schedule=tuple(brief.get('release_schedule') or ()))


# ─────────────────────────────── Stage5 面 ───────────────────────────────

def chapter_view(base: str | os.PathLike[str], candidate: str,
                 ordinal: int) -> Str06ChapterView:
    """一章的正文与章级回执：机械范围与语义未评分开，篇幅披露显式。"""
    run = _run_dir(base, candidate)
    if run is None:
        return Str06ChapterView(ordinal=ordinal, present=False)
    prose = _text(run, f'stage5/body/ch{ordinal:02d}.txt')
    receipt = _inspect(run, f'stage5/acceptance/body/ch{ordinal:02d}.json')
    return Str06ChapterView(
        ordinal=ordinal, present=prose is not None or receipt is not None,
        prose=prose,
        receipt=Str06ChapterReceiptView(
            status=receipt['status'],
            mechanical_checks=tuple(receipt['mechanical_checks']),
            semantic=receipt['semantic'],
            length=dict(receipt['length']),
            length_disclosed=bool(receipt.get('length_disclosed')),
            request_identity=receipt['request_identity'],
            protocol_version=receipt['protocol_version'],
            renderer_version=receipt['renderer_version'],
            validator_version=receipt['validator_version'])
        if receipt is not None else None)


def hook_view(base: str | os.PathLike[str], candidate: str) -> Str06HookView:
    """Hook 成文与机械回执（`semantic` 恒 unverified）。"""
    run = _run_dir(base, candidate)
    if run is None:
        return Str06HookView(present=False)
    prose = _text(run, 'stage5/hook/hook.txt')
    receipt = _inspect(run, 'stage5/hook/receipt.json')
    return Str06HookView(present=prose is not None or receipt is not None,
                         prose=prose, receipt=receipt)


# ─────────────────────────────── Stage7 面 ───────────────────────────────

def naming_view(base: str | os.PathLike[str], candidate: str) -> Str06NamingView:
    """NameMap v5：交付变换契约（只读投影；ApplyNames 才是唯一应用者）。"""
    run = _run_dir(base, candidate)
    if run is None:
        return Str06NamingView(present=False)
    payload = _inspect(run, 'stage7/naming/name_map.json')
    if payload is None:
        return Str06NamingView(present=False)
    entries = tuple(
        Str06NamingEntryView(
            char_ref=e['char_ref'], family=e['name']['family'],
            given=e['name'].get('given'), full=e['name']['full'],
            aliases=dict(e.get('aliases') or {}),
            keep_reason=e.get('keep_reason'))
        for e in payload.get('entries') or ())
    return Str06NamingView(present=True, entries=entries)


def delivery_view(base: str | os.PathLike[str], candidate: str) -> Str06DeliveryView:
    """成书交付：manifest 是就绪核对记录（文件先落盘、manifest 最后提交）。"""
    run = _run_dir(base, candidate)
    if run is None:
        return Str06DeliveryView(present=False)
    payload = _inspect(run, 'stage7/final/delivery_manifest.json')
    if payload is None:
        return Str06DeliveryView(present=False)
    return Str06DeliveryView(
        present=True,
        delivery_identity=payload.get('delivery_identity'),
        final_text_sha256=payload.get('final_text_sha256'),
        chapters=tuple(
            Str06DeliveryChapterView(
                chapter_ordinal=c['chapter_ordinal'],
                prose_sha256=c.get('prose_sha256'),
                length=dict(c['length']) if c.get('length') else None,
                length_disclosed=bool(c.get('length_disclosed')))
            for c in payload.get('chapters') or ()),
        length_reading=dict(payload['length_reading'])
        if payload.get('length_reading') else None,
        disclosure=payload.get('disclosure'))


def final_text_path(base: str | os.PathLike[str], candidate: str) -> Path | None:
    """成书字节的落点（下载用）；缺席返回 None，不造空文件。"""
    run = _run_dir(base, candidate)
    if run is None:
        return None
    files = _titled_files(run, 'stage7/final/{title}_rewriting.txt')
    return files[0] if files else None


# ─────────────────────────────── Run 与成本面 ───────────────────────────────

def _ledger_cost(candidate_root: Path) -> Str06LedgerCostView:
    """ledger 成本账的只读投影：实付与缓存分开，usage 缺失单列不并零。"""
    try:
        rows, partial = read_cost_snapshot(candidate_root)
    except (OSError, ValueError) as exc:
        raise ArtifactUnreadable(f'候选根成本账不可读：{exc}') from exc

    def known(keys: tuple[str, ...], subset: list[dict]) -> int | None:
        values = [row.get(k) for row in subset for k in keys]
        if any(v is None for v in values):
            return None
        return sum(v for v in values if isinstance(v, int))

    paid = [row for row in rows if not row.get('cached')]
    cached = [row for row in rows if row.get('cached')]
    unknown = sum(1 for row in paid
                  if row.get('prompt_tokens') is None
                  or row.get('completion_tokens') is None)
    per_node: dict[str, list[dict]] = {}
    for row in rows:
        per_node.setdefault(str(row.get('node') or 'unknown'), []).append(row)
    return Str06LedgerCostView(
        present=True, rows=len(rows), new_paid_rows=len(paid), cached_rows=len(cached),
        prompt_tokens=known(('prompt_tokens',), paid),
        completion_tokens=known(('completion_tokens',), paid),
        reasoning_tokens=known(('reasoning_tokens',), paid),
        unknown_cost_rows=unknown + (1 if partial else 0),
        per_node=tuple(
            Str06LedgerNodeCostView(
                node=node, rows=len(node_rows),
                new_paid_rows=sum(1 for r in node_rows if not r.get('cached')),
                cached_rows=sum(1 for r in node_rows if r.get('cached')),
                prompt_tokens=known(('prompt_tokens',),
                                    [r for r in node_rows if not r.get('cached')]),
                completion_tokens=known(('completion_tokens',),
                                        [r for r in node_rows if not r.get('cached')]),
                reasoning_tokens=known(('reasoning_tokens',),
                                       [r for r in node_rows if not r.get('cached')]))
            for node, node_rows in sorted(per_node.items())))


def _chapter_failures_view(run: Path, rel: str) -> Str06ChapterFailuresView | None:
    """逐章失败留证的直投影（D3/W2；章观察与正文共用同一实现）。

    「最近一次完成执行」形状 `{requested, attempted, chapters, recorded_at}`；
    无文件返回 None（该节点还没有一次完成执行，不冒充合法空清单）。文件畸形
    在此 fail fast（I-34）——失败留证丢一条比整面 500 更坏。
    """
    payload = _inspect(run, rel)
    if payload is None:
        return None
    try:
        requested = tuple(int(n) for n in payload['requested'])
        attempted = tuple(int(n) for n in payload['attempted'])
        recorded_at = int(payload['recorded_at'])
        chapters = payload['chapters']
        if not isinstance(chapters, dict):
            raise ValueError('chapters 必须是章序 → 失败详情的映射')
        rows = tuple(
            Str06BodyFailureChapterView(
                chapter_ordinal=int(ordinal),
                failure_kind=str(row['failure_kind']), detail=str(row['detail']))
            for ordinal, row in sorted(chapters.items(), key=lambda kv: int(kv[0]))
            if isinstance(row, dict))
    except (KeyError, TypeError, ValueError) as exc:
        raise ArtifactUnreadable(
            f'{rel} 形状损坏：{exc}；它是逐章失败留证，'
            '拒绝静默丢条或当作「没有问题」继续') from exc
    return Str06ChapterFailuresView(
        present=True, requested=requested, attempted=attempted, recorded_at=recorded_at,
        chapters=rows)


def _assembly_report_view(run: Path) -> Str06AssemblyReportView | None:
    """`runtime/delivery/assembly_report.json` 直投影：五态按 item、结论与逐条问题。"""
    payload = _inspect(run, ASSEMBLY_REPORT_REL)
    if payload is None:
        return None
    access = payload.get('body_access') if isinstance(payload, dict) else None
    outcome = payload.get('assembly') if isinstance(payload, dict) else None
    return Str06AssemblyReportView(
        present=True,
        accepted_body=payload.get('accepted_body'),
        body_access=Str06BodyAccessView(
            accepted=tuple(access.get('accepted') or ()),
            missing=tuple(access.get('missing') or ()),
            rejected=tuple(access.get('rejected') or ()),
            unknown=tuple(access.get('unknown') or ()),
            stale=tuple(access.get('stale') or ()))
        if isinstance(access, dict) else None,
        chain_problems=tuple(payload.get('chain_problems') or ()),
        assembly=Str06AssemblyOutcomeView(
            status=outcome.get('status'), reason=outcome.get('reason'),
            delivery_identity=outcome.get('delivery_identity'))
        if isinstance(outcome, dict) else None)


def _owner_issues_view(run: Path) -> Str06OwnerIssuesView | None:
    """修订轮的只读投影（R3）：`runtime/issues/<owner>.json` 全部回派账。

    目录缺席（从未起过修订轮）返回 None；open 与 resolved 都投（resolved 是
    历史留账，轮数供监控面读取）。记录形状损坏显式拒绝（I-34）——它是修订
    路由的输入，不能静默当作「没有问题」。停止原因不在这里推导：它在
    `workflow_status.json` 的失败形状里，两面并排呈现。
    """
    directory = _pattern_dir(run, f'{ISSUES_DIR_REL}/_*.json')
    if directory is None:
        return None
    from contracts.schema.workflow import owner_issue_record_of_payload
    from novel.stages.owner_revision import owner_product_digest_at

    records = []
    for entry in sorted(directory.iterdir()):
        if not entry.name.endswith('.json') or not entry.is_file():
            continue
        payload = _inspect(run, f'{ISSUES_DIR_REL}/{entry.name}')
        try:
            record = owner_issue_record_of_payload(
                payload,
                lambda record_owner: owner_product_digest_at(run, record_owner))
        except ValueError as exc:
            raise ArtifactUnreadable(
                f'回派账 {ISSUES_DIR_REL}/{entry.name} 形状损坏：{exc}；'
                '它是修订路由的输入，拒绝当作「没有问题」继续') from exc
        records.append(Str06OwnerIssueRecordView(
            owner=record.owner, node=record.node, raised_by=record.raised_by,
            status=record.status, rounds_total=record.rounds_total,
            fingerprint=record.fingerprint, against_sha256=record.against_sha256,
            updated_at=record.updated_at,
            issues=tuple(
                Str06OwnerIssueItemView(
                    code=issue.code, item=issue.item, message=issue.message,
                    context=issue.context)
                for issue in record.issues)))
    return Str06OwnerIssuesView(
        present=True, rounds_cap=OWNER_REVISION_MAX_ROUNDS,
        records=tuple(records))


def run_view(base: str | os.PathLike[str], candidate: str) -> Str06RunView:
    """候选根总览：workflow 状态、失败形状、章级失败留证、集合核对报告与成本账。"""
    run = _run_dir(base, candidate)
    if run is None:
        return Str06RunView(candidate=candidate, present=False)
    status = _inspect(run, STATUS_REL) or {}
    workflow = _inspect(run, WORKFLOW_REL) or {}
    epoch_payload = workflow.get('epoch') or {}
    nodes = tuple(
        Str06NodeView(node=node_id, stage=spec.stage,
                      owner=candidate_node_owner(node_id),
                      paid=spec.paid, produced=_node_produced(run, node_id))
        for node_id, spec in NODES.items() if node_id.startswith('str06.'))
    return Str06RunView(
        candidate=candidate, present=True,
        status=status.get('status'), failed_node=status.get('failed_node'),
        accepted_body=status.get('accepted_body'),
        failure_kind=status.get('failure_kind'),
        detail=status.get('detail'),
        error_code=status.get('error_code'),
        error_stage=status.get('error_stage'),
        error_node=status.get('error_node'),
        error_item=status.get('error_item'),
        error_phase=status.get('error_phase'),
        error_context=status.get('error_context'),
        body_failures=_chapter_failures_view(run, BODY_FAILURES_REL),
        chapter_observation_failures=_chapter_failures_view(
            run, CHAPTER_OBSERVATION_FAILURES_REL),
        assembly_report=_assembly_report_view(run),
        owner_issues=_owner_issues_view(run),
        epoch=Str06EpochView(
            epoch_id=epoch_payload['epoch_id'], ordinal=epoch_payload['ordinal'],
            reason=epoch_payload['reason'],
            session_run_id=epoch_payload.get('session_run_id'))
        if epoch_payload else None,
        nodes=nodes,
        cost=_ledger_cost(_candidates_base(run)))
