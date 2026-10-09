"""Hook v2 的确定性目录、wire 展开与显式 v1 换算。

模型仅选择已给出的引用。换算仅供已购回答的显式恢复，生产 assembler 不收 v1；
找不到或有多个对应建立时拒绝，不改写语义、补地址或把原请求伪装成新请求。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from contracts.schema.design import BodyDesign
from contracts.schema.hook import HookDesignWire
from contracts.schema.workflow import STAGE4_HOOK_DESIGN_PROTOCOL
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
    schema['$defs']['HookMaterialWire']['properties']['withheld']['items']['enum'] = all_refs
    schema['$defs']['HookBeatRefWire']['enum'] = [
        {'scene': s.scene_id, 'beat': b.beat_id} for s in body.scenes for b in s.beats]
    return schema


def project_hook_input(body: BodyDesign) -> dict:
    """保留入口决策的完整故事语义，剥离预算、审计和重复状态地址。"""
    facts = hook_facts(body)
    return {
        'input_version': 'stage4-hook-input-v2', 'facts': hook_catalog(body),
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
    return (None, errors) if errors else (data, [])


def upgrade_purchased_hook_wire(wire: dict, body: BodyDesign) -> dict:
    """显式、无损的 v1→v2 引用换算；调用方另核已购来源与完整接受闸。"""
    if wire.get('protocol_version') != 'stage4-hook-design-v1':
        raise ValueError('已购 Hook 换算只接受显式 stage4-hook-design-v1')
    result = deepcopy(wire)
    facts = hook_facts(body)

    def select(ref, raw):
        if isinstance(ref, dict):
            ref = '#'.join((ref['scene'], ref['beat']))
        key = (raw['domain'], tuple(raw.get('subjects') or ()), raw.get('holder') or '', raw['key'])
        matches = [f.fact_ref for f in facts if f.identity == (ref, key)]
        if len(matches) != 1:
            raise ValueError(f'已购事实引用无法无损换算：{ref}／{key}')
        return matches[0]

    for item in result.get('opening_facts', []):
        item['fact_ref'] = select(item.pop('ref'), item.pop('address'))
    for item in result['materials']:
        item['withheld'] = [select(item['beat_ref'], raw) for raw in item.get('withheld', [])]
    for k, item in enumerate(result.get('forbidden_reveals', [])):
        result['forbidden_reveals'][k] = (
            {'kind': 'fact', 'fact_ref': select(item['ref'], item['address'])}
            if item.get('address') is not None else {'kind': 'event', 'beat_ref': item['ref']})
    result['protocol_version'] = STAGE4_HOOK_DESIGN_PROTOCOL
    return result
