"""STR-06 隔离入口：source JSON → Stage2 → Preserve/Design/Bind → 免费 planned 干跑。

--wire-dir 显式采用已购/人工修订 wire，逐份记录字节 SHA；无付费循环。
--run-dir 固定重用；只有缺失同请求身份结果且同时 --yes --allow-source-export 才购买一次。
此入口不发布 Stage5 accepted receipt，也不切换现役工作流（删除条件 STR-06-F8）。
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from contracts.schema.str06 import OutputConstraints
from novel.config import load_config
from novel.llm.ledger import CostLedger, ledger_path
from novel.llm.str06_requests import PaidRequestSession, digest
from novel.llm.transport import ChatRequest, DeepSeekTransport
from novel.store import Str06Repository
from novel.stages.str06.source_compile import (
    ChapterCompiler, assemble_blueprint, compile_book, chapter_input_snapshot, initial_snapshot, source_fold,
)
from novel.stages.str06.grounding import assemble_preserve, project_brief, assemble_binding
from novel.stages.str06.design_compile import assemble_presentation_graph
from novel.stages.str06.compile import compile_execution_plan
from novel.stages.str06.fold import fold_accepted_prefix
from novel.stages.str06.project import project_context
from novel.stages.str06.guard import run_mechanical_guard
from novel.stages.str06.prompts import (
    SYS, BOOK_PROMPT, CHAPTER_PROMPT, PRESERVE_RULES, DESIGN_RULES, BIND_RULES, pack_prompt,
)


def build_evidence(chapters):
    evidence = {}
    for chapter in chapters:
        no, text = chapter['chapter_ordinal'], chapter['text']
        segments, buffer = [], ''
        for sentence in re.split(r'(?<=。)', text):
            if buffer and len(buffer) + len(sentence) > 300:
                segments.append(buffer)
                buffer = sentence
            else:
                buffer += sentence
        if buffer.strip():
            segments.append(buffer)
        for i, segment in enumerate(segments, 1):
            evidence[f'e{no:02d}_{i:02d}'] = {'chapter': no, 'text': segment}
    return evidence


def run_workflow(*, repository, chapters, ask, budget=11000, through='stage4',
                 provider='manual-adoption', model='unknown'):
    """记录显式执行状态；失败保留旧产物供恢复，但不能仍宣称上次绿灯有效。"""
    identity = digest({'chapters': chapters, 'budget': budget, 'through': through})
    status_rel = 'runtime/workflow_status.json'
    repository.write(status_rel, {'status': 'running', 'input_sha256': identity,
                                  'accepted_body': False})
    try:
        report = _run_workflow(repository=repository, chapters=chapters, ask=ask,
                               budget=budget, through=through, provider=provider, model=model)
    except Exception as exc:
        repository.write(status_rel, {'status': 'failed', 'input_sha256': identity,
                                      'accepted_body': False, 'failure_kind': type(exc).__name__,
                                      'detail': str(exc)})
        raise
    repository.write(status_rel, {'status': 'source_compiled' if through == 'stage2' else 'planned_validated',
                                  'input_sha256': identity,
                                  'accepted_body': False, 'through': through})
    return report


def _run_workflow(*, repository, chapters, ask, budget, through, provider, model):
    if [c['chapter_ordinal'] for c in chapters] != list(range(1, len(chapters) + 1)):
        raise ValueError('source JSON 必须为非空连续章序列表')
    if not chapters or any(not c['text'].strip() for c in chapters):
        raise ValueError('源章不能为空')
    evidence = build_evidence(chapters)
    full = '\n'.join(f'〔第{c["chapter_ordinal"]}章〕\n{c["text"]}' for c in chapters)
    block = '\n'.join(f'[{k}] {v["text"]}' for k, v in evidence.items())
    book = ask('book', BOOK_PROMPT.format(n=len(chapters), text=full, evidence=block),
               lambda w: compile_book(w, evidence_ids=evidence)[2])
    chars, wcs, errors = compile_book(book, evidence_ids=evidence)
    if errors:
        raise ValueError(errors)
    compiler, compiled, snapshots = ChapterCompiler(chars, evidence_ids=evidence), [], {}
    for chapter in chapters:
        no = chapter['chapter_ordinal']
        snapshot = (initial_snapshot(chars) if no == 1 else
                    chapter_input_snapshot(chars, assemble_blueprint(wcs, compiled), no))
        snapshots[str(no)] = snapshot
        prompt = CHAPTER_PROMPT.format(
            ch=no, glossary=pack_prompt('人物闭集', characters=book['characters']),
            snapshot='\n'.join(snapshot),
            kreg='\n'.join(f'{k} -> {v}' for k, v in compiler.statements.items()),
            evidence='\n'.join(f'[{k}] {v["text"]}' for k, v in evidence.items() if v['chapter'] == no),
            known_beats=', '.join(sorted(compiler.known_beats)))
        wire = ask(f'ch{no:02d}', prompt, lambda w: compiler.validate(w, no))
        bp, errors = compiler.compile(wire, no)
        if errors:
            raise ValueError(errors)
        compiled.append(bp)
    blueprint = assemble_blueprint(wcs, compiled)
    repository.write('stage2/characters/characters.json', chars)
    repository.write('stage2/blueprints/chapter_blueprints.json', blueprint)
    repository.write('stage2/compiled/input_snapshots.json', snapshots)
    _, events = source_fold(chars, blueprint)
    repository.write('stage2/acceptance/fold.json', {'events': events})
    if through == 'stage2':
        return {'chapters': len(compiled), 'fold_events': events}
    def preserve_compile(w):
        return assemble_preserve(w, policy_id='preserve-default', policy_version=1,
                                 characters=chars, blueprint=blueprint,
                                 provider=provider, model=model,
                                 request_sha256=digest(preserve_prompt))
    preserve_prompt = pack_prompt(PRESERVE_RULES, characters=chars.model_dump(mode='json'),
                                  blueprints=blueprint.model_dump(mode='json'))
    pw = ask('preserve', preserve_prompt, lambda w: preserve_compile(w)[1])
    checkpoint, errors = preserve_compile(pw)
    if errors:
        raise ValueError(errors)
    repository.write('runtime/preserve_checkpoint.json', checkpoint)
    brief, identities = project_brief(checkpoint, chars)
    repository.write('stage4/grounding/reconstruction_brief.json', brief)
    constraints = OutputConstraints(target_chapter_count=len(chapters), total_length_budget=budget)
    design_prompt = pack_prompt(DESIGN_RULES, brief=brief.model_dump(mode='json'),
                                identities=[i.model_dump(mode='json') for i in identities],
                                constraints=constraints.model_dump(mode='json'))
    def design_compile(w):
        return assemble_presentation_graph(w, known_char_ids=set(chars.characters), constraints=constraints)
    dw = ask('design', design_prompt, lambda w: design_compile(w)[1])
    graph, errors = design_compile(dw)
    if errors:
        raise ValueError(errors)
    repository.write('stage4/design/presentation_graph.json', graph)
    positions = {s.scene_id: {b.beat_id for b in s.beats} for s in graph.scenes}
    bind_prompt = pack_prompt(BIND_RULES, design=graph.model_dump(mode='json'),
                              obligations=[o.model_dump(mode='json') for o in checkpoint.obligations])
    bw = ask('bind', bind_prompt, lambda w: assemble_binding(w, checkpoint, positions)[1])
    binding, errors = assemble_binding(bw, checkpoint, positions)
    if errors:
        raise ValueError(errors)
    repository.write('stage4/grounding/semantic_binding.json', binding)
    plan, errors = compile_execution_plan(graph, binding)
    repository.write('stage4/acceptance/guard/compile.json', {'errors': errors})
    if errors:
        raise ValueError(errors)
    repository.write('stage4/compiled/execution_plan.json', plan)
    problems = []
    for i, scene in enumerate(graph.scenes):
        folded = fold_accepted_prefix(plan.scenes[:i], initial=plan.initial_establishments)
        context, slice_ = project_context(scene_id=scene.scene_id, attempt_id='planned-dry-run',
                                         graph=graph, plan=plan, binding=binding,
                                         folded_prefix=folded, total_budget=budget)
        errors = run_mechanical_guard(binding=binding, context=context, graph=graph)
        problems.extend(errors)
        repository.write(f'stage4/context/{scene.scene_id}.json', context)
        repository.write(f'stage4/compiled/slice_{scene.scene_id}.json', slice_)
    report = {'mode': 'planned-only', 'accepted_body': False, 'chapters': len(compiled),
              'scenes': len(graph.scenes), 'obligations': len(binding.obligations),
              'guard_errors': problems, 'source_fold_events': events}
    repository.write('stage4/acceptance/guard/report.json', report)
    if problems:
        raise ValueError(problems)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-json', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--wire-dir', type=Path)
    parser.add_argument('--repair-manifest', type=Path)
    parser.add_argument('--through', choices=['stage2', 'stage4'], default='stage4')
    parser.add_argument('--budget', type=int, default=11000)
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--allow-source-export', action='store_true')
    args = parser.parse_args()
    base = os.environ.get('NOVEL_OUTPUT')
    if not base:
        raise ValueError('NOVEL_OUTPUT 未设置：没查、未运行')
    if not args.run_dir.resolve().is_relative_to(Path(base).resolve()):
        raise ValueError('--run-dir 必须位于 NOVEL_OUTPUT 下的隔离根')
    repository = Str06Repository(args.run_dir)
    chapters = repository.read_external(args.source_json)
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
    ledger = CostLedger(path=ledger_path(Path(base)))
    session = PaidRequestSession(repository, ledger, DeepSeekTransport(cfg), yes=args.yes,
                                 allow_source_export=args.allow_source_export,
                                 provider=cfg.base_url)
    def ask(owner, prompt, validate):
        if args.wire_dir:
            path = args.wire_dir / f'{owner}_wire.json'
            if args.repair_manifest and (
                    owner not in adopted or Path(adopted[owner]['path']).resolve() != path.resolve()
                    or repository.file_sha256(path) != adopted[owner]['sha256']):
                raise ValueError(f'{owner} wire 不在已核实修订 manifest 内')
            raw = repository.read_external(path)
            errors = validate(raw)
            repository.write(f'runtime/adopt_{owner}.json', {
                'owner': owner, 'path': str(path.resolve()),
                'sha256': repository.file_sha256(path),
                'new_paid_calls': 0, 'historical_usage': None, 'errors': errors})
            if errors:
                raise ValueError(f'{owner} 免费采用失败：{errors}')
            return raw
        request = ChatRequest(model=cfg.model, system=SYS, user=prompt, reasoning_effort='high',
                              thinking=cfg.thinking_mode == 'on', json_mode=True,
                              max_tokens=65536, temperature=0.3)
        return session.ask(owner, request, validate)
    print(run_workflow(repository=repository, chapters=chapters, ask=ask,
                       budget=args.budget, through=args.through,
                       provider='manual-adoption' if args.wire_dir else cfg.base_url,
                       model='unknown' if args.wire_dir else cfg.model))


if __name__ == '__main__':
    main()
