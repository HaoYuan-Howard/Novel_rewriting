"""CS-F3 金标装载器：仓外 Golden yaml ＋ rc15 冻结根 → 标定材料与冻结标签。

【数据来源（2026-10-05 用户确认）】开发集《夫君的报恩》、留出集另两本（本文件
只装开发集与 rc15 真实案件；留出集映射是后续批次，冻结前不运行）。标签先冻结
（tests/resources/csf3_labels_20261005.json，只存引用与判定，不进正文语料——
断言行在运行时从 yaml 逐字取，yaml 以 sha256 钉住），后发付费调用。

【映射规则（透明、可审计）】每个 source_check 出两条断言候选：正向＝
expected_reading 原文（预期 支持）；反向＝negative_control 原文（预期按 yaml 的
expected_reading 判定：给出锚点反证 → 矛盾；原文未给出／不能确认 → 未确定且
evidence_insufficient）。逐案判定表随冻结文件提交，分歧由用户裁决、agent 不改
标签。批次＝一个 diagnostic_case 一次集中核验（与生产形态同构：一章／一案一调）。

【rc15 真实案件】ch03 主体误派（b3.e1，已裁决 矛盾）＋同章如实观察（b6.k1，
草案 支持）；ob44 无依据否定限制（已裁决 矛盾）＋ob01 有锚定义务（草案 支持）；
design 锁 kf_0b89（暂缓和离）正确承载拍（草案 支持）与无关拍（已裁决 矛盾，
「赏花」模式）。真实材料全部只读，不改动冻结根。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from contracts.schema.observation import ChapterObservation
from tests.semantic_calibration import (
    CalibrationCase, DesignLock, DesignMaterial, FactCandidate,
    ObligationMaterial, Stage2FactMaterial, TextSegment,
    fact_candidates_from_observation,
)

FROZEN_AT = '2026-10-05T18:00:00+08:00'

#: 开发集逐案判定表（negative_control 的预期判定＋事实类别）。依据＝各 check 的
#: expected_reading：有锚点反证记 矛盾；「原文未给出／没有写成／不能确认」记
#: 未确定。类别为六类闭集的最佳归属（同一 check 的正反两条同类别）。
DEV_VERDICTS: dict[str, dict[str, str]] = {
    'GS-01-a': {'neg': '矛盾', 'category': 'key_misunderstanding'},
    'GS-01-b': {'neg': '未确定', 'category': 'identity'},
    'GS-02-a': {'neg': '矛盾', 'category': 'key_knowledge'},
    'GS-02-b': {'neg': '未确定', 'category': 'key_misunderstanding'},
    'GS-02-c': {'neg': '矛盾', 'category': 'key_misunderstanding'},
    'GS-03-a': {'neg': '矛盾', 'category': 'key_knowledge'},
    'GS-03-b': {'neg': '矛盾', 'category': 'key_knowledge'},
    'GS-03-c': {'neg': '矛盾', 'category': 'key_misunderstanding'},
    'GS-03-d': {'neg': '矛盾', 'category': 'key_misunderstanding'},
    'GS-04-a': {'neg': '未确定', 'category': 'key_knowledge'},
    'GS-04-b': {'neg': '未确定', 'category': 'key_knowledge'},
    'GS-04-c': {'neg': '未确定', 'category': 'key_knowledge'},
    'GS-05-a': {'neg': '矛盾', 'category': 'key_knowledge'},
    'GS-05-b': {'neg': '矛盾', 'category': 'key_knowledge'},
    'GS-05-c': {'neg': '矛盾', 'category': 'key_misunderstanding'},
    'GS-05-d': {'neg': '矛盾', 'category': 'key_misunderstanding'},
    'GS-06-a': {'neg': '矛盾', 'category': 'key_knowledge'},
    'GS-06-b': {'neg': '未确定', 'category': 'key_misunderstanding'},
    'GS-06-c': {'neg': '矛盾', 'category': 'key_knowledge'},
    'GS-06-d': {'neg': '未确定', 'category': 'key_misunderstanding'},
    'GS-07-a': {'neg': '矛盾', 'category': 'relation'},
    'GS-07-b': {'neg': '矛盾', 'category': 'relation'},
    'GS-07-c': {'neg': '矛盾', 'category': 'key_misunderstanding'},
    'GS-08-a': {'neg': '矛盾', 'category': 'identity'},
    'GS-08-b': {'neg': '矛盾', 'category': 'key_knowledge'},
    'GS-08-c': {'neg': '矛盾', 'category': 'key_knowledge'},
    'GS-09-a': {'neg': '矛盾', 'category': 'key_knowledge'},
    'GS-09-b': {'neg': '矛盾', 'category': 'key_knowledge'},
    'GS-09-c': {'neg': '矛盾', 'category': 'key_knowledge'},
    'GS-10-a': {'neg': '矛盾', 'category': 'relation'},
    'GS-10-b': {'neg': '未确定', 'category': 'key_knowledge'},
    'GS-10-c': {'neg': '未确定', 'category': 'identity'},
}

#: rc15 案件的固定选择（真实材料在盘上，选择显式登记于此）。
RC15_BOOK = '夫君的报恩_2059579376044396719'
DESIGN_ATTEMPT_UNIT = '0381986000570d0ab12b74ddac96dc37b396bd0dc04297d054f1c0486d9ad26c'
DESIGN_LOCK_KF = 'kf_0b894923ea33c5cc'


def _longpath(path: Path) -> str:
    text = str(path)
    return text if text.startswith('\\\\?\\') else '\\\\?\\' + str(path.resolve())


def _read_json(path: Path) -> Any:
    return json.loads(Path(_longpath(path)).read_text(encoding='utf-8-sig'))


def _paragraphs(text: str) -> list[str]:
    """同 `novel/stages/chain.py::_segment_paragraphs`（证据切分唯一实现的同尺副本）。"""
    return [line.strip() for line in text.splitlines() if line.strip()]


def load_golden(golden_path: Path, source_txt: Path) -> dict:
    """yaml＋源文加载（sha 核对；不符拒绝——金标身份先于一切）。"""
    import yaml
    data = yaml.safe_load(Path(_longpath(golden_path)).read_text(encoding='utf-8'))
    raw = Path(_longpath(source_txt)).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != data['source']['sha256_raw']:
        raise ValueError(f'源文 sha256 与金标登记不符：{source_txt} ({digest})')
    return data


def _anchor_windows(data: dict, check: dict, span: int = 2) -> list[TextSegment]:
    """锚点行 ±span 的合并窗口（1-based 行号、空行计入、不清洗）。"""
    lines = data['_lines']
    ranges: list[tuple[int, int]] = []
    for anchor in check.get('support_scope', {}).get('anchors', []):
        line = int(anchor['line'])
        ranges.append((max(1, line - span), min(len(lines), line + span)))
    ranges.sort()
    merged: list[list[int]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    segments = []
    for start, end in merged:
        text = '\n'.join(lines[start - 1:end])
        if text.strip():
            segments.append(TextSegment(segment_id=f'e{start}_{end}', text=text))
    return segments


def golden_batches(data: dict) -> tuple[dict[str, Stage2FactMaterial],
                                        list[CalibrationCase]]:
    """开发集 yaml → 批次材料＋案件。断言行逐字取自 yaml（正＝expected_reading，
    反＝negative_control）；段窗口按锚点行合并。"""
    materials: dict[str, Stage2FactMaterial] = {}
    cases: list[CalibrationCase] = []
    for case in data['diagnostic_cases']:
        case_id = case['case_id']
        batch = f'golden-{case_id}'
        candidates: list[FactCandidate] = []
        batch_cases: list[CalibrationCase] = []
        segments: list[TextSegment] = []
        for check in case.get('source_checks', []):
            key = f"{case_id}-{check['key']}"
            verdict = DEV_VERDICTS.get(key)
            if verdict is None:
                raise ValueError(f'冻结判定表缺 {key}；不猜标签')
            anchors = check.get('support_scope', {}).get('anchors', [])
            quotes = tuple(a['quote'] for a in anchors)
            for direction, text, expected, insufficient in (
                    ('neg', check['negative_control'], verdict['neg'],
                     verdict['neg'] == '未确定'),
                    ('pos', check['expected_reading'], '支持', False)):
                handle = f'{key}-{direction}'
                candidates.append(FactCandidate(
                    handle=handle, kind='effect', line=text, basis=quotes))
                batch_cases.append(CalibrationCase.model_validate({
                    'case_id': handle, 'batch': batch, 'target': 'stage2_fact',
                    'category': verdict['category'], 'item': handle,
                    'expected': expected, 'evidence_insufficient': insufficient,
                    'high_impact': True, 'frozen_at': FROZEN_AT,
                    'label_source': 'golden_yaml', 'subset': 'dev',
                    # 引文不进仓（正文语料纪律）：审计回 sha 钉住的 yaml 锚点。
                    'gold_evidence': [{'ref': f"{a['section']}#L{a['line']}",
                                       'quote': f"锚点见冻结 yaml {a['section']}#L{a['line']}"}
                                      for a in anchors],
                }))
            segments.extend(_anchor_windows(data, check))
        seen: dict[str, TextSegment] = {}
        for segment in segments:
            seen.setdefault(segment.segment_id, segment)
        materials[batch] = Stage2FactMaterial(
            segments=tuple(seen.values()), candidates=tuple(candidates))
        cases.extend(batch_cases)
    return materials, cases


def _rc15_book_root(rc15_root: Path) -> Path:
    return rc15_root / RC15_BOOK


def _rc15_chapters(rc15_root: Path) -> list[dict]:
    doc = _read_json(_rc15_book_root(rc15_root) / 'stage1' / 'main_text.json')
    payload = doc.get('payload', doc)
    sections = payload.get('root', payload) if isinstance(payload, dict) else payload
    return [{'chapter_ordinal': s['chapter_ordinal'], 'text': s['text']}
            for s in sections]


def _chapter_segments(rc15_root: Path, ordinal: int) -> tuple[TextSegment, ...]:
    chapters = _rc15_chapters(rc15_root)
    text = next(c['text'] for c in chapters if c['chapter_ordinal'] == ordinal)
    return tuple(TextSegment(segment_id=f'e{ordinal:02d}_{i:02d}', text=paragraph)
                 for i, paragraph in enumerate(_paragraphs(text), 1))


def rc15_stage2_case(rc15_root: Path) -> tuple[str, Stage2FactMaterial,
                                                list[CalibrationCase]]:
    """ch03 全候选（生产形态：一章一次集中核验）＋两案标签。"""
    observation = _read_json(_rc15_book_root(rc15_root) / 'stage2' / 'observations'
                             / 'ch03.json')
    raw_observation = observation.get('payload', observation)
    raw_observation.pop('_provenance', None)  # 传输身份不进业务模型
    canonical = ChapterObservation.model_validate(raw_observation)
    candidates = fact_candidates_from_observation(3, canonical)
    material = Stage2FactMaterial(segments=_chapter_segments(rc15_root, 3),
                                  candidates=candidates)

    def _find(handle: str) -> FactCandidate:
        return next(c for c in candidates if c.handle == handle)

    misread = 'ch03.b3.e1'
    faithful = 'ch03.b6.k1'
    batch = 'rc15-ch03'
    cases = [
        CalibrationCase.model_validate({
            'case_id': 'rc15-ch03-misread', 'batch': batch, 'target': 'stage2_fact',
            'category': 'relation', 'item': misread, 'expected': '矛盾',
            'high_impact': True, 'frozen_at': FROZEN_AT,
            'label_source': 'user_adjudicated', 'subset': 'dev',
            'gold_evidence': [{'ref': 'user_adjudication/2026-10-04',
                               'quote': '主体是夫君（模仿表弟称谓），非表弟'}],
            'notes': 'rc15 真实观察：b3.e1 把夫君假作表弟记成与表弟已发生关系'}),
        CalibrationCase.model_validate({
            'case_id': 'rc15-ch03-faithful', 'batch': batch, 'target': 'stage2_fact',
            'category': 'key_knowledge', 'item': faithful, 'expected': '支持',
            'high_impact': True, 'frozen_at': FROZEN_AT,
            'label_source': 'rc15_material', 'subset': 'dev',
            'gold_evidence': [{'ref': 'e03_13', 'quote': _find(faithful).basis[0]}],
            'notes': '同章如实观察的反例对照（agent 草案标签）'}),
    ]
    return batch, material, cases


def _obligation(rc15_root: Path, obligation_id: str) -> dict:
    checkpoint = _read_json(_rc15_book_root(rc15_root) / 'runtime'
                            / 'preserve_checkpoint.json')
    payload = checkpoint.get('payload', checkpoint)
    return next(o for o in payload['obligations'] if o['obligation_id'] == obligation_id)


def rc15_preserve_cases(rc15_root: Path) -> tuple[dict[str, ObligationMaterial],
                                                   list[CalibrationCase]]:
    out: dict[str, ObligationMaterial] = {}
    cases: list[CalibrationCase] = []
    for obligation_id, chapter, expected, source, notes in (
            ('ob44', 8, '矛盾', 'user_adjudicated',
             'rc15 真实义务：否定限制「不得改成芸娘当场识破」无源依据'),
            ('ob01', 1, '支持', 'rc15_material',
             '有锚定义务的对照（agent 草案标签）')):
        obligation = _obligation(rc15_root, obligation_id)
        batch = f'rc15-{obligation_id}'
        out[batch] = ObligationMaterial(
            obligation_id=obligation_id, statement=obligation['statement'],
            satisfaction=obligation.get('satisfaction', ''),
            must_preserve=obligation['must_preserve'],
            anchors=tuple(q for a in obligation['anchors']
                          for q in a.get('quotes', ())),
            segments=_chapter_segments(rc15_root, chapter))
        cases.append(CalibrationCase.model_validate({
            'case_id': batch, 'batch': batch, 'target': 'preserve_basis',
            'category': 'negative_basis' if expected == '矛盾' else 'affirmative_basis',
            'item': obligation_id, 'expected': expected, 'frozen_at': FROZEN_AT,
            'label_source': source, 'subset': 'dev',
            'gold_evidence': [{'ref': f'runtime/preserve_checkpoint.json#{obligation_id}',
                               'quote': obligation['statement'][:40]}],
            'notes': notes}))
    return out, cases


def _design_wire(rc15_root: Path) -> dict:
    unit_dir = (rc15_root / '_service' / 'runs' / RC15_BOOK
                / 'work_units' / DESIGN_ATTEMPT_UNIT)
    for path in sorted(unit_dir.glob('*.json')):
        if path.name.startswith('accept_'):
            continue
        doc = _read_json(path)
        content = (doc.get('payload', {}).get('response', {}) or {}).get('content')
        if not content:
            continue
        try:
            wire = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(wire, dict) and wire.get('scenes'):
            return wire
    raise ValueError('rc15 设计尝试响应未找到（冻结根只读，不重算）')


def _design_rows(rc15_root: Path, picks: tuple[tuple[int, int], ...]) -> tuple[str, ...]:
    wire = _design_wire(rc15_root)
    rows = []
    for scene_index, beat_index in picks:
        scene = wire['scenes'][scene_index - 1]
        beat = scene['beats'][beat_index - 1]
        scene_id = scene.get('scene_id') or f's{scene_index:02d}'
        beat_id = beat.get('beat_id') or f'b{beat_index:02d}'
        rows.append(f'{scene_id}#{beat_id}｜{beat["ev"]}')
    return tuple(rows)


def rc15_design_cases(rc15_root: Path) -> tuple[dict[str, DesignMaterial],
                                                 list[CalibrationCase]]:
    brief = _read_json(_rc15_book_root(rc15_root) / 'stage4' / 'grounding'
                       / 'reconstruction_brief.json')
    payload = brief.get('payload', brief)
    lock = next(l for l in payload['fact_locks'] if l['kf_id'] == DESIGN_LOCK_KF)
    lock_statement = lock['statement']
    bound_rows = _design_rows(rc15_root, ((3, 2),))    # 「和离的事先不急，等表弟来再商量」
    unbound_rows = _design_rows(rc15_root, ((8, 2),))  # 「寄住陆家，白日上学」（无关）
    out: dict[str, DesignMaterial] = {}
    cases: list[CalibrationCase] = []
    for batch, rows, expected, source, notes in (
            ('rc15-design-bound', bound_rows, '支持', 'rc15_material',
             '锁内容由该拍真实承载（agent 草案标签）'),
            ('rc15-design-unbound', unbound_rows, '矛盾', 'user_adjudicated',
             '「赏花」模式：锁绑到无关内容应否决（引用合法不是语义承载）')):
        out[batch] = DesignMaterial(
            locks=(DesignLock(item=DESIGN_LOCK_KF, statement=lock_statement),),
            rows=rows)
        cases.append(CalibrationCase.model_validate({
            'case_id': batch, 'batch': batch, 'target': 'design_fragment',
            'category': 'restriction_preservation', 'item': DESIGN_LOCK_KF,
            'expected': expected, 'frozen_at': FROZEN_AT, 'label_source': source,
            'subset': 'dev',
            'gold_evidence': [{'ref': f'stage4/grounding/reconstruction_brief.json#{DESIGN_LOCK_KF}',
                               'quote': lock_statement[:40]}],
            'notes': notes}))
    return out, cases


def build_run_set(golden_path: Path, source_txt: Path,
                  rc15_root: Path) -> tuple[dict, list[CalibrationCase]]:
    """开发集＋rc15 真实案件的完整运行集（材料运行时派生，不进冻结文件）。"""
    data = load_golden(golden_path, source_txt)
    data['_lines'] = Path(_longpath(source_txt)).read_text(
        encoding='utf-8-sig').splitlines()
    materials, cases = golden_batches(data)
    batch, material, stage2_cases = rc15_stage2_case(rc15_root)
    materials[batch] = material
    cases.extend(stage2_cases)
    preserve_materials, preserve_cases = rc15_preserve_cases(rc15_root)
    materials.update(preserve_materials)
    cases.extend(preserve_cases)
    design_materials, design_cases = rc15_design_cases(rc15_root)
    materials.update(design_materials)
    cases.extend(design_cases)
    return materials, cases
