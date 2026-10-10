"""Hook v3 的确定性目录与 wire 展开。

模型仅选择已给出的引用与呈现策略；compiler 不补出旧版缺少的组织及读者期待。
答案、后续承载位置只用于校验，不作为 Writer 的正向写作材料。
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from contracts.schema.design import BodyDesign
from contracts.schema.hook import HookDesignWire
from novel.stages.stage4.design_paths import design_issue
from novel.stages.stage4.hook_facts import hook_facts


def ref_parts(ref: str) -> str | dict[str, str]:
    if ref == 'init':
        return ref
    scene, beat = ref.split('#', 1)
    return {'scene': scene, 'beat': beat}


def hook_catalog(body: BodyDesign) -> dict[str, dict]:
    return {fact.fact_ref: {'ref': ref_parts(fact.ref),
                           **fact.delta.model_dump(mode='json')}
            for fact in hook_facts(body)}


def hook_wire_schema(body: BodyDesign) -> dict:
    """完整分支 schema 与书级闭集进入请求字节；不声称 provider 强制执行 schema。"""
    schema = HookDesignWire.model_json_schema()
    facts = hook_facts(body)
    established = [f.fact_ref for f in facts if f.delta.op == 'set']
    all_refs = [f.fact_ref for f in facts]
    for name in ('HookOpeningFactWire', 'HookForbiddenFactWire'):
        field = schema['$defs'][name]['properties']['fact_ref']
        field['enum'] = established
    for definition in schema['$defs'].values():
        properties = definition.get('properties', {})
        if (properties.get('kind', {}).get('const') == 'fact'
                and 'focus' in properties and 'fact_ref' in properties):
            properties['fact_ref']['enum'] = established
    schema['$defs']['HookMaterialWire']['properties']['withheld']['items']['enum'] = all_refs
    schema['$defs']['HookBeatRefWire']['enum'] = [
        {'scene': s.scene_id, 'beat': b.beat_id} for s in body.scenes for b in s.beats]
    return schema


def opening_chapter_input(body: BodyDesign) -> dict:
    """首章分工的正式投影；引用保持章内分配顺序，不读取已写正文。"""
    opening = next(chapter for chapter in body.chapters if chapter.chapter_ordinal == 1)
    scenes = {scene.scene_id: scene for scene in body.scenes}
    return {
        'purpose': opening.purpose, 'scene_refs': list(opening.scene_ids),
        'beat_refs': [ref_parts(f'{scene_ref}#{beat.beat_id}')
                      for scene_ref in opening.scene_ids for beat in scenes[scene_ref].beats]}


def project_hook_input(body: BodyDesign) -> dict:
    """保留入口决策的完整故事语义，剥离预算、审计和重复状态地址。"""
    facts = hook_facts(body)
    return {
        'input_version': 'stage4-hook-input-v3', 'facts': hook_catalog(body),
        'book_style': body.book_style, 'narration': body.narration.model_dump(mode='json'),
        'world_rules': list(body.world_rules),
        'character_voices': [v.model_dump(mode='json') for v in body.character_voices],
        'expression_decisions': [d.model_dump(mode='json') for d in body.expression_decisions],
        'initial_surfaces': [s.model_dump(mode='json') for s in body.initial_surfaces],
        'scenes': [{
            'scene_id': scene.scene_id, 'driver': scene.driver.model_dump(mode='json'),
            'realization_brief': scene.realization_brief,
            'delivery': scene.delivery.model_dump(mode='json'),
            'beats': [{
                **beat.model_dump(mode='json', exclude={'target_delta'}),
                'fact_refs': [f.fact_ref for f in facts
                              if f.ref == f'{scene.scene_id}#{beat.beat_id}']}
                for beat in scene.beats]}
            for scene in body.scenes],
        'chapters': [c.model_dump(mode='json', exclude={'length_budget'}) for c in body.chapters],
        'opening_chapter': opening_chapter_input(body),
    }


def expand_hook_wire(wire: dict[str, Any], body: BodyDesign) -> tuple[dict | None, list[str]]:
    try:
        parsed = HookDesignWire.model_validate(wire)
    except ValidationError as exc:
        errors = []
        for row in exc.errors(include_url=False):
            loc = tuple(row['loc'])
            path = loc[:2] if len(loc) > 1 and isinstance(loc[1], int) else loc[:1]
            errors.append(design_issue(f'Hook wire 形状非法：{loc}：{row["msg"]}',
                                       code='hook_wire_shape', location=loc,
                                       paths=(path,) if path and path[0] != 'protocol_version' else ()))
        return None, errors
    data = parsed.model_dump(mode='json', exclude={'protocol_version'})
    index = {fact.fact_ref: fact for fact in hook_facts(body)}
    errors = []

    def resolve(handle, path):
        fact = index.get(handle)
        if fact is None:
            errors.append(design_issue(f'未知事实引用：{handle}（只能选择输入目录）',
                                       code='hook_fact_ref', location=path, paths=(path,)))
        return fact

    def address(fact):
        return {key: getattr(fact.delta, key) for key in ('domain', 'key', 'subjects', 'holder')}

    for k, item in enumerate(data['opening_facts']):
        fact = resolve(item.pop('fact_ref'), ('opening_facts', k))
        if fact is not None:
            item.update(ref=fact.ref, address=address(fact))
    for k, item in enumerate(data['materials']):
        item['beat_ref'] = '#'.join((item['beat_ref']['scene'], item['beat_ref']['beat']))
        hidden = []
        for handle in item['withheld']:
            fact = resolve(handle, ('materials', k))
            if fact is not None:
                if fact.ref != item['beat_ref']:
                    errors.append(design_issue(
                        f'材料 {item["beat_ref"]} 的 withheld 不属于该拍 delta：{handle}',
                        code='hook_withheld_ref', location=('materials', k, handle),
                        paths=(('materials', k),)))
                hidden.append(address(fact))
        item['withheld'] = hidden
    for k, item in enumerate(data['forbidden_reveals']):
        if item.pop('kind') == 'fact':
            fact = resolve(item.pop('fact_ref'), ('forbidden_reveals', k))
            if fact is not None:
                item.update(ref=fact.ref, address=address(fact))
        else:
            ref = item.pop('beat_ref')
            item.update(ref='#'.join((ref['scene'], ref['beat'])), address=None)
    expectation = data['reader_expectation']
    if expectation['kind'] == 'fact':
        fact = resolve(expectation.pop('fact_ref'), ('reader_expectation',))
        if fact is not None:
            expectation['answer'] = {'ref': fact.ref, 'address': address(fact)}
    else:
        expectation['followup_refs'] = [
            '#'.join((ref['scene'], ref['beat']))
            for ref in expectation['followup_refs']]
    return (None, errors) if errors else (data, [])
