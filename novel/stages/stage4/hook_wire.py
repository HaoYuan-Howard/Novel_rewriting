"""Hook v4 的确定性目录与 wire 展开；历史 v3 解码保留独立身份。

模型仅选择已给出的引用与呈现策略；compiler 不补出旧版缺少的组织及读者期待。
答案、后续承载位置只用于校验，不作为 Writer 的正向写作材料。

【请求面派生标注（2026-10-10 裁决，方向一）】目录每条在原始事实字段之外附
`later_writes`／`name_exposure` 两类 compiler 预推导标注，只进请求、不进 wire
契约：判据可机械推导的部分（同地址覆盖时序、命题文本的专名暴露）由代码算好
给模型查表，不再要求模型在全书事实上手工重放状态机（I-15/I-20）。
"""
from __future__ import annotations

from typing import Any, Mapping

from pydantic import ValidationError

from contracts.schema.design import BodyDesign
from contracts.schema.hook import HookDesignWire, HookDesignWireV3
from novel.core.repair import shape_repair_paths
from novel.stages.stage4.design_paths import design_issue
from novel.stages.stage4.hook_facts import hook_facts


def hook_wire_model(wire: Mapping[str, Any]) -> HookDesignWire | HookDesignWireV3:
    """历史 v3 仅在这里解码，不修改响应字节或冒充 v4 请求身份。"""
    model = HookDesignWireV3 if wire.get('protocol_version') == 'stage4-hook-design-v3' else HookDesignWire
    return model.model_validate(wire)


def ref_parts(ref: str) -> str | dict[str, str]:
    if ref == 'init':
        return ref
    scene, beat = ref.split('#', 1)
    return {'scene': scene, 'beat': beat}


def hook_catalog(body: BodyDesign) -> dict[str, dict]:
    return {fact.fact_ref: {'ref': ref_parts(fact.ref),
                           **fact.delta.model_dump(mode='json')}
            for fact in hook_facts(body)}


def hook_name_exposure(text: str | None, *,
                       faces: Mapping[str, Iterable[str]]) -> dict[str, list[str]]:
    """文本命中的各人物目标字面（与 presentation 泄漏判据同一字面宇宙）。

    只做存在性报告，不做替换；按人物分组让模型对照自己的 surface_releases
    决定「释放该人物」还是「换选条目」。rc26（命名前移批 C）：`faces` 是每人
    目标字面闭集（`name_materials.person_target_literals` 产物）——请求面与
    泄漏判据同尺，源旧名不进请求标注。
    """
    exposure: dict[str, list[str]] = {}
    if not text:
        return exposure
    for char_id, literals in faces.items():
        found = {literal for literal in literals if literal and literal in text}
        if found:
            exposure[char_id] = sorted(found, key=lambda value: (-len(value), value))
    return exposure


def _statement_text(fact, kp_statements: Mapping[str, str],
                    world_statements: Mapping[str, str]) -> str:
    """该条事实渲染进写手视图时的命题/取值文本（`_render_fact` 同一来源：
    kp 句柄经 `kp_statement` 回填，非 kp 键的 value 本就是内容）。"""
    from novel.core.text import kp_statement
    if fact.delta.domain.value == 'knowledge':
        statement = kp_statement(kp_statements, fact.delta.key)
        return statement if statement is not None else fact.delta.value
    if fact.delta.domain.value == 'world' and fact.delta.key in world_statements:
        return world_statements[fact.delta.key]
    return fact.delta.value


def annotated_hook_catalog(body: BodyDesign, *,
                           kp_statements: Mapping[str, str] = (),
                           world_statements: Mapping[str, str] = (),
                           faces: Mapping[str, Iterable[str]] | None = None,
                           ) -> dict[str, dict]:
    """hook_catalog + 请求面派生标注（later_writes／name_exposure）。

    · `later_writes`——同地址在本条建立位置之后的全部写入（含 unset，阅读序）。
      world/pov 起笔事实必须是冷开起点前该地址的**最后一次**写入；起点不早于
      任一后续写入时旧值已失效（`hook_opening_stale`）。scene 作用域的有效性
      由模型按 `scope`＋所在场核对，规则文本另有说明。
    · `name_exposure`——本条命题/取值文本命中的目标字面（按人物）。这些文本会
      原样进入写手视图；未释放且无显示面替换的人物字面即泄漏
      （`positive_material_leak`）。rc26：字面宇宙是目标字面表（`faces`）。
    """
    facts = hook_facts(body)
    catalog = hook_catalog(body)
    by_address: dict[tuple, list] = {}
    for fact in facts:
        by_address.setdefault(fact.delta.address(), []).append(fact)
    for fact in facts:
        row = catalog[fact.fact_ref]
        later = [other for other in by_address[fact.delta.address()]
                 if other.position > fact.position]
        if later:
            row['later_writes'] = [
                {'fact_ref': other.fact_ref, 'at': ref_parts(other.ref)}
                for other in later]
        if faces is not None:
            exposure = hook_name_exposure(
                _statement_text(fact, kp_statements, world_statements), faces=faces)
            if exposure:
                row['name_exposure'] = exposure
    return catalog


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


def project_hook_input(body: BodyDesign, *,
                       faces: Mapping[str, Iterable[str]] | None = None,
                       kp_statements: Mapping[str, str] = (),
                       world_statements: Mapping[str, str] = ()) -> dict:
    """保留入口决策的完整故事语义，剥离预算、审计和重复状态地址。

    `faces`（每人目标字面闭集）在场时目录与拍附派生标注
    （`annotated_hook_catalog`；拍上的 `name_exposure` 标注 `event` 文本——
    材料渲染会原样带上它），`identity_surfaces` 同尺给目标字面。rc26：不再
    携带 name_candidates（候选覆盖硬闸随 identity-text 协议退役）。
    """
    facts = hook_facts(body)

    def beat_row(scene, beat) -> dict:
        row = {**beat.model_dump(mode='json', exclude={'target_delta'}),
               'fact_refs': [f.fact_ref for f in facts
                             if f.ref == f'{scene.scene_id}#{beat.beat_id}']}
        if faces is not None:
            exposure = hook_name_exposure(beat.event, faces=faces)
            if exposure:
                row['name_exposure'] = exposure
        return row

    return {
        'input_version': 'stage4-hook-input-v4',
        'identity_surfaces': {char_id: sorted(set(literals) - {''})
                              for char_id, literals in faces.items()}
                             if faces is not None else {},
        'facts': annotated_hook_catalog(body, faces=faces,
                                        kp_statements=kp_statements,
                                        world_statements=world_statements),
        'book_style': body.book_style, 'narration': body.narration.model_dump(mode='json'),
        'world_rules': list(body.world_rules),
        'character_voices': [v.model_dump(mode='json') for v in body.character_voices],
        'expression_decisions': [d.model_dump(mode='json') for d in body.expression_decisions],
        'initial_surfaces': [s.model_dump(mode='json') for s in body.initial_surfaces],
        'scenes': [{
            'scene_id': scene.scene_id, 'driver': scene.driver.model_dump(mode='json'),
            'realization_brief': scene.realization_brief,
            'delivery': scene.delivery.model_dump(mode='json'),
            'beats': [beat_row(scene, beat) for beat in scene.beats]}
            for scene in body.scenes],
        'chapters': [c.model_dump(mode='json', exclude={'length_budget'}) for c in body.chapters],
        'opening_chapter': opening_chapter_input(body),
    }


def expand_hook_wire(wire: dict[str, Any], body: BodyDesign) -> tuple[dict | None, list[str]]:
    try:
        parsed = hook_wire_model(wire)
    except ValidationError as exc:
        errors = []
        model = (HookDesignWireV3 if wire.get('protocol_version') == 'stage4-hook-design-v3'
                 else HookDesignWire)
        entries = shape_repair_paths(model.model_json_schema(), wire,
                                     exc.errors(include_url=False))
        for entry in entries:
            path = entry['replace_path']
            if path is None:
                code = 'wire_shape_unlocatable'
            elif not path or entry['issue_path'] == ['protocol_version']:
                code = 'hook_wire_root_shape'
                path = []
            else:
                code = 'hook_wire_shape'
            location = tuple(entry['issue_path'] if entry['issue_path'] is not None
                             else entry['raw_location'])
            errors.append(design_issue(
                f'Hook wire 形状非法：{location}：{entry["message"]}（{entry["error_type"]}）',
                code=code, location=(location, entry['error_type']),
                paths=(tuple(path),) if path else (), context={'shape_errors': [entry]}))
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
