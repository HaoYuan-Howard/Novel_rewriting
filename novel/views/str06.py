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

from contracts.schema.str06.api import (
    Str06BlueprintBeatView, Str06BlueprintChapterView, Str06BlueprintView,
    Str06CandidateList, Str06CandidateSummary, Str06ChapterReceiptView,
    Str06ChapterView, Str06CharacterView, Str06CharactersView,
    Str06DesignBindingView, Str06DesignChapterView, Str06DesignHookView,
    Str06DesignSceneView, Str06DesignView, Str06DeliveryChapterView,
    Str06DeliveryView, Str06EpochView, Str06HookView, Str06IrChapterView,
    Str06IrNavView, Str06LedgerCostView, Str06LedgerNodeCostView,
    Str06NameStructureView, Str06NamingEntryView, Str06NamingView, Str06NodeView,
    Str06RunView, Str06SurfaceWindowView,
)
from contracts.schema.str06.candidates import STR06_CANDIDATE_NODES
from novel.errors import ArtifactUnreadable
from novel.store import Str06Repository
from novel.work_unit_usage import read_cost_snapshot

#: 候选根内运行目录的固定名（tools/str06_workflow.py 的 `--run-dir` 约定；
#: 直接就是把书产物放在候选根下的旧布局也认，见 `_run_dir`）。
RUN_DIRNAME = 'run'
#: 候选 workflow 身份记录（候选根的身份标记）。
WORKFLOW_REL = 'runtime/candidate_workflow.json'
#: run 级执行状态（业务状态的唯一来源）。
STATUS_REL = 'runtime/workflow_status.json'
#: 章序模式（与候选产物表的 `{nn}` 一致）。
_NN = re.compile(r'^ch(\d+)\.txt$')
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


def _chapter_files(run: Path, rel_pattern: str) -> list[tuple[int, Path]]:
    """把 `ch{nn}.txt` 模式解析成盘上实际的 (章序, 路径)，按章序排序。"""
    directory = _pattern_dir(run, rel_pattern)
    if directory is None:
        return []
    found = []
    for entry in sorted(directory.iterdir()):
        match = _NN.match(entry.name)
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
    for rel in STR06_CANDIDATE_NODES[node_id].produces:
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
    """Characters v4：人物条目（称谓时间窗）＋源名称结构。"""
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
    """Blueprint v12：世界约束＋逐章 beats（事件／参与者／effects／reveals）与因果边。"""
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
                    reveals=tuple(b.get('reveals') or ()))
                for b in chapter['beats']),
            causal_edges=tuple(chapter.get('causal_edges') or ()))
        for chapter in payload.get('chapters') or ())
    return Str06BlueprintView(
        present=True,
        world_constraints=tuple(payload.get('world_constraints') or ()),
        chapters=chapters)


# ─────────────────────────────── Stage4 面 ───────────────────────────────

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
            scene_id=s['scene_id'], driver=dict(s['driver']),
            realization_brief=s['realization_brief'],
            pov=s['delivery']['pov'], voice=s['delivery']['voice'],
            avoid=tuple(s['delivery'].get('avoid') or ()),
            beats=tuple(s['beats']))
        for s in graph.get('scenes') or ())
    chapters = tuple(
        Str06DesignChapterView(chapter_ordinal=c['chapter_ordinal'],
                               purpose=c['purpose'],
                               scene_ids=tuple(c['scene_ids']))
        for c in graph.get('chapters') or ())
    hook_payload = graph.get('hook')
    hook = (Str06DesignHookView(
        person=hook_payload['pov']['person'],
        focalizers=tuple(hook_payload['pov']['focalizers']),
        materials=tuple(m['beat_ref'] for m in hook_payload['materials']),
        concealed=tuple(hook_payload.get('concealed') or ()),
        forbidden_reveals=len(hook_payload.get('forbidden_reveals') or ()),
        design_brief=hook_payload['design_brief'])
        if hook_payload else None)
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
            chapter_ordinal=nav['chapter_ordinal'], purpose=nav['purpose'],
            length_budget=next(c['length_budget'] for c in payload['chapters']
                               if c['chapter_ordinal'] == nav['chapter_ordinal']),
            scene_ids=tuple(nav['scene_ids']),
            scene_lines=tuple(nav['scene_lines']))
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


def run_view(base: str | os.PathLike[str], candidate: str) -> Str06RunView:
    """候选根总览：workflow 状态、epoch、节点盘面事实与成本账。"""
    run = _run_dir(base, candidate)
    if run is None:
        return Str06RunView(candidate=candidate, present=False)
    status = _inspect(run, STATUS_REL) or {}
    workflow = _inspect(run, WORKFLOW_REL) or {}
    epoch_payload = workflow.get('epoch') or {}
    nodes = tuple(
        Str06NodeView(node=spec.node, stage=spec.stage, owner=spec.owner,
                      paid=spec.paid, produced=_node_produced(run, spec.node))
        for spec in STR06_CANDIDATE_NODES.values())
    return Str06RunView(
        candidate=candidate, present=True,
        status=status.get('status'), failed_node=status.get('failed_node'),
        accepted_body=status.get('accepted_body'),
        epoch=Str06EpochView(
            epoch_id=epoch_payload['epoch_id'], ordinal=epoch_payload['ordinal'],
            reason=epoch_payload['reason'],
            session_run_id=epoch_payload.get('session_run_id'))
        if epoch_payload else None,
        nodes=nodes,
        cost=_ledger_cost(_candidates_base(run)))
