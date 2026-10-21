"""story-v3 事实层的纯语义协议、编译与机械检查。

chapters 先理解本章，book 后置综合全书，names 仅解析名字，associate 只回答显式
未决候选，compile 唯一发布 SourceStory。此模块不读盘、不调模型、不启动下游，
不修复输入、不猜身份/命题/因果。检查通过只证明形状与引用，不证明小说理解正确。
"""
from __future__ import annotations

import hashlib
import heapq
import json
import re
from collections import Counter
from copy import deepcopy
from typing import Any, Mapping, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from contracts.schema.source_story import (
    STORY_VERSION, AssociateObservation, AssociateWire, AssociationEvidence, AssociationVerdict, BookObservation, BookWire,
    ChapterObservation, ChapterWire, EntityWire, NamesObservation, NamesWire, SourceStory, SourceStoryDraft,
    StoryIssue, StoryNameStructure, SourceEvidence, SourceObservationIssue,
    BookRepairScope, BookRepairWire, SourceRevisionAddress, SourceRevisionScopeError,
    source_revision_owner, SOURCE_REVISION_SELECTORS,
)
from novel.core.source_index import SourceIndex, build_source_index

T = TypeVar('T', bound=BaseModel)

_MATERIALS_RULE = '''
【材料补全控制协议】材料不足时可先回复
{"kind":"need_materials","requests":[{"operation":"read_segments","segment_ids":["已给段号"],"adjacent_paragraphs":1}]}
或 requests 中使用 {"operation":"search_literal","terms":["原文字面"],"mode":"all","chapter_range":[0,当前可读末章],"cursor":0,"limit":20}。
章节只可查询截至本章的原文；其他单位按材料许可读取。结果 complete/truncated/empty
均有明确分母；空命中不是不存在的证明。补充材料不会扩大本单位写权限。材料齐备后
直接返回下面业务 schema 的 JSON，不输出材料请求与业务结果的混合体。
'''

_CHAPTER_MATERIALS_RULE = '''
【材料补全控制协议】材料不足时可先回复
{"kind":"need_materials","requests":[{"operation":"read_segments","segment_ids":[1],"adjacent_paragraphs":1}]}
或 requests 中使用 {"operation":"search_literal","terms":["原文字面"],"mode":"all","chapter_range":[0,当前可读末章],"cursor":0,"limit":20}。
segment_ids只选材料中已给的JSON正整数ref；地址按全书阅读序固定，补料不会从1重新编号。
章节只可查询截至本章的原文。结果 complete/truncated/empty 均有明确分母；
空命中不是不存在的证明。补充材料不会扩大本章事实写权限。材料齐备后
直接返回下面业务 schema 的 JSON，不输出材料请求与业务结果的混合体。
'''


class StoryCompileError(ValueError):
    """机械失败携带 owner/item/path，调用者不解析人读报错反推控制流。"""

    def __init__(self, issues: Sequence[StoryIssue]):
        self.issues = tuple(issues)
        super().__init__('；'.join(issue.message for issue in self.issues))


class SourceObservationIssueRequired(ValueError):
    """后置综合只能报告原章观察漏错，不能拿整书权限暗改局部事实。"""

    def __init__(self, source_issues: Sequence[SourceObservationIssue]):
        self.source_issues = tuple(source_issues)
        first = self.source_issues[0]
        self.node = 'str06.stage2.chapters'
        self.owner = f'ch{first.chapter_ordinal:02d}'
        self.item, self.code = first.item, 'source_observation_issue'
        self.context = {'source_issues': [item.model_dump(mode='json') for item in self.source_issues]}
        super().__init__('章观察需要按原文证据窄修：' + '；'.join(item.message for item in self.source_issues))

    def as_issue(self) -> dict[str, Any]:
        return {'owner': self.owner, 'node': self.node, 'item': self.item, 'code': self.code,
                'message': str(self), 'context': self.context}


def _issue(code: str, owner: str, path: tuple, message: str, *, item: str = '') -> StoryIssue:
    return StoryIssue(code=code, owner=owner, item=item or (str(path[1]) if len(path) > 1 else owner),
                      path=path, message=message)


def _fail(code: str, owner: str, path: tuple, message: str):
    raise StoryCompileError((_issue(code, owner, path, message),))


def _parse(model: type[T], wire: Any, owner: str) -> T:
    try:
        if isinstance(wire, BaseModel):
            wire = wire.model_dump(mode='json')
        return model.model_validate(wire)
    except ValidationError as exc:
        raise StoryCompileError(tuple(_issue(
            'shape', owner, tuple(error['loc']), error['msg']) for error in exc.errors())) from exc


def _json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _stable_handles(keys, *, index: SourceIndex, domain: str, prefix: str) -> dict[str, str]:
    """语义键是明确身份，不从文本猜同一性；增删/重排其它条目不换已有句柄。"""
    handles = {key: prefix + _digest([index.source_identity, domain, key])[:20] for key in keys}
    if len(set(handles.values())) != len(handles):
        _fail('handle_collision', 'chapters' if domain.startswith('chapter:') else 'book',
              (domain,), '确定性句柄发生碰撞，拒绝猜测或复用身份')
    return handles


def _schema(model: type[BaseModel]) -> str:
    return _json(model.model_json_schema())


def _render_evidence(index: SourceIndex, refs: Sequence[str] | None = None) -> str:
    selected = index.evidence if refs is None else {ref: index.evidence[ref] for ref in refs}
    return '\n'.join(f'[{ref}] {segment.text}' for ref, segment in selected.items())


def _evidence_refs(value: Any, path: tuple = ()):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in ('evidence_refs', 'source_refs') and isinstance(child, (list, tuple)):
                yield path + (key,), child
            else:
                yield from _evidence_refs(child, path + (key,))
    elif isinstance(value, (list, tuple)):
        for number, child in enumerate(value):
            yield from _evidence_refs(child, path + (number,))


def _evidence_issues(value: Any, index: SourceIndex, owner: str, *, allowed=None) -> list[StoryIssue]:
    """只收集当前权限下可独立定位的证据问题，供不同 scope 同轮合并。"""
    issues = []
    allowed_refs = set(index.evidence) if allowed is None else set(allowed)
    for path, refs in _evidence_refs(value):
        for ref in refs:
            if ref not in allowed_refs:
                issues.append(_issue('evidence_outside_scope', owner, path,
                                     f'{ref!r} 不是本单位获准的证据段'))
        if len(refs) != len(set(refs)):
            issues.append(_issue('duplicate_evidence', owner, path, '同条目证据段重复'))
    return issues


def _check_evidence(value: Any, index: SourceIndex, owner: str, *, allowed=None) -> None:
    issues = _evidence_issues(value, index, owner, allowed=allowed)
    if issues:
        raise StoryCompileError(issues)


def _map_ref(key: str, mapping: Mapping[str, str], owner: str, path: tuple) -> str:
    if key not in mapping:
        _fail('unknown_reference', owner, path, f'{key!r} 不在已给引用闭集中')
    return mapping[key]


def chapter_schema(index: SourceIndex, chapter_ordinal: int, *,
                   materials: Sequence[dict] = ()) -> dict:
    """证据引用选择已给段号；补料只扩大context的已见范围，不扩大本章事实权限。"""
    local_refs = tuple(index.evidence_numbers[ref] for ref in index.chapter(chapter_ordinal))
    context_refs = set(local_refs)
    for material in materials:
        if (not isinstance(material, Mapping)
                or material.get('source_identity') != index.source_identity
                or material.get('index_version') != index.index_version
                or not isinstance(material.get('segments'), list)):
            _fail('chapter_material_source', 'chapters', (), '章补料必须绑定当前真实源索引')
        seen = set()
        for raw in material['segments']:
            if (not isinstance(raw, Mapping) or type(raw.get('ref')) is not int or raw['ref'] <= 0
                    or raw['ref'] in seen or type(raw.get('chapter_ordinal')) is not int
                    or type(raw.get('paragraph_ordinal')) is not int):
                _fail('chapter_material_source', 'chapters', (),
                      '章补料ref须为无重复的稳定正整数地址，章序和段序须为严格整数')
            number = raw['ref']
            seen.add(number)
            segment = _parse(SourceEvidence, {key: value for key, value in raw.items() if key != 'ref'}, 'chapters')
            ref = index.evidence_ids.get(number)
            if (ref is None or segment != index.evidence[ref]
                    or segment.chapter_ordinal > chapter_ordinal):
                _fail('chapter_material_source', 'chapters', (), '章补料须逐字等于截至本章的源索引证据')
            context_refs.add(number)
    schema = ChapterWire.model_json_schema()
    # 共享正式时间结构允许事件锚；局部观察尚无机械句柄，不能下发该写入权限。
    schema['$defs']['StoryTemporal']['properties']['anchor_event'] = {'type': 'null'}
    for definition in schema['$defs'].values():
        fields = definition.get('properties', {})
        if 'evidence_refs' in fields:
            fields['evidence_refs']['items']['enum'] = list(local_refs)
            fields['evidence_refs']['uniqueItems'] = True
    context = schema['$defs']['ChapterContextNeedWire']['properties']['source_refs']
    context['items']['enum'] = [number for number in index.evidence_ids if number in context_refs]
    context['uniqueItems'] = True
    return schema


def chapter_prompt(index: SourceIndex, chapter_ordinal: int, *,
                   materials: Sequence[dict] = ()) -> str:
    """只给本章及第一章的引子；不注入后文真相、前章观察或隐藏身份表。"""
    schema = chapter_schema(index, chapter_ordinal, materials=materials)
    return f'''任务：忠实理解第 {chapter_ordinal} 章。你是局部事实观察者，不设计新故事。
先声明局部 referents：键是有语义的描述（如“本章未具名的婴儿”），不是编号。
label 描述局部主体；surface 只选择该主体在所引证据段中连续出现的一个代表称呼。
同一主体有多个称呼时不必在 surface 穷举别名；不要用斜线、分号、括号把称呼串成列表。
例如原文“我叫林舟，旁人称我林公子”，surface 可填“林舟”，不能填“我/林舟/林公子”。
events同样使用简短、明确的语义键。修订沿用既有键；新增事件使用新键，不能重命名未变条目。
记录所有会影响行为、关系、人物转变或事件承接的事件，保留直接触发、行动对象、
公开行为和内心反应的差别。participants 引用本章 referents 的键；被提及者不自动在场。
一个自然段可以包含多事件，多个事件可以共引证据；不能因段号覆盖完整就声称理解完整。
claims.statement 原样表达完整语义，不暗拆复合命题。world_status 区分叙述确立、
被否定和真伪未定。人物的猜测/自述/听说只记 knowledge，不能变成世界事实；读者
看到猜测也不代表得到确认。认知 holder 引用局部 referents，不把 reader 当人物。
claims.evidence_refs 必须是所属事件 evidence_refs 的子集；事件要使用的证据须显式列全。
读者披露逐条选择 confirmed/claimed/suggested/none，不从在场或人称推导。
temporal 明确 current/past/future/summary/unknown；回忆中的事实不自动是当前状态。
temporal.anchor_event 必须为 null；精确事件锚由后置 book 使用程序生成的句柄综合，
本章不得填写事件语义键、自然语言时间说明或自行编造的事件编号。
thread_hints 允许本章孤立 resolve（原问题可能在前章），不补造一个 open 强行闭合。
因果按原文给 causal_hints；不明指代/前因列 context_needs，不猜全书身份或不存在前文。
引子中的预告/后文高光不当作第一章已发生事件；不明性质明确列 context_needs。
全部证据仅选择下面给定的整数地址，任何正式 ID、章号、统计和模板均由代码生成。
【本章原文，一段一行】
{chr(10).join(f'[{index.evidence_numbers[ref]}] {segment.text}' for ref, segment in index.chapter(chapter_ordinal).items())}
{_CHAPTER_MATERIALS_RULE}
【引用格式约束】
evidence_refs/source_refs中的每一项只能是给定闭集中的JSON正整数，不加引号、不写小数、不填写正式证据ID。
整数地址按全书阅读序固定，包含引子；本章和后续补料均不重新编号。evidence_refs仅选本章已给地址。
context_needs.source_refs仅选本章及实际冻结补料已送达的截至本章地址；未读前文先请求材料，禁止引用未来章。
【输出 schema】{_json(schema)}'''


def _canonical_chapter_evidence(value: Any, index: SourceIndex) -> Any:
    """所有整数引用通过闭集检查后，仅机械换为正式ID，语义字段与布局保持原样。"""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    if isinstance(value, Mapping):
        return {key: [index.evidence_ids[number] for number in child]
                if key in ('evidence_refs', 'source_refs') and isinstance(child, (list, tuple))
                else _canonical_chapter_evidence(child, index)
                for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_chapter_evidence(child, index) for child in value]
    return value


def compile_chapter(wire: Any, *, index: SourceIndex,
                    chapter_ordinal: int) -> ChapterObservation:
    model = _parse(ChapterWire, wire, 'chapters')
    allowed = {index.evidence_numbers[ref]: segment for ref, segment in index.chapter(chapter_ordinal).items()}
    issues = _evidence_issues(model.model_dump(mode='json', exclude={'context_needs'}), index,
                             'chapters', allowed=allowed)
    prior_refs = {index.evidence_numbers[ref] for ref, segment in index.evidence.items()
                  if segment.chapter_ordinal <= chapter_ordinal}
    issues.extend(_evidence_issues({'context_needs': model.context_needs}, index,
                                  'chapters', allowed=prior_refs))
    # 一次报告所有可独立定位的机械错误，避免每发现一个坏称呼就再买一轮。
    # 字面由语义 owner 选择；这里不按标点拆称呼，也不猜补证据或人物身份。
    for key, referent in model.referents.items():
        # 坏引用已定位到 evidence_refs；没有可用证据时不能断言字面也错，更不能解引用坏 ID。
        evidence = [allowed[ref] for ref in referent.evidence_refs if ref in allowed]
        if evidence and not any(referent.surface in segment.text for segment in evidence):
            message = (f'局部指称字面 {referent.surface!r} 未逐字出现在自身获准证据中；'
                       '同轮核对 surface 与 evidence_refs 两端，保持主体含义，'
                       '不得随意截词或改指其他主体来过闸；只选该主体的一个连续原文称呼')
            for field in ('surface', 'evidence_refs'):
                issues.append(_issue('mention_not_in_evidence', 'chapters',
                                     ('referents', key, field), message))
    for key, event in model.events.items():
        for number, ref in enumerate(event.participants):
            if ref not in model.referents:
                issues.append(_issue('unknown_reference', 'chapters', ('events', key, 'participants', number),
                                     f'{ref!r} 不在已给本章 referents 引用闭集中'))
        if len(event.participants) != len(set(event.participants)):
            issues.append(_issue('duplicate_participant', 'chapters', ('events', key, 'participants'),
                                 '本事件参与者重复'))
        if event.temporal.anchor_event is not None:
            issues.append(_issue('local_temporal_anchor', 'chapters',
                                 ('events', key, 'temporal', 'anchor_event'),
                                 '章观察尚无事件句柄；时间锚交整书综合按代码句柄引用'))
        for claim_no, claim in enumerate(event.claims):
            if not set(claim.evidence_refs) <= set(event.evidence_refs):
                issues.append(_issue('claim_outside_event', 'chapters', ('events', key, 'claims', claim_no),
                    '局部判断的证据必须由所属事件显式引用；核对命题所引证据与事件漏列证据，不得为过闸删除有效证据'))
                issues.append(_issue('claim_outside_event', 'chapters', ('events', key, 'evidence_refs'),
                    '事件证据与局部判断证据未闭合；同轮核对两端，仅按原文选择正确证据'))
            for number, knowledge in enumerate(claim.knowledge):
                if knowledge.holder not in model.referents:
                    issues.append(_issue('unknown_reference', 'chapters',
                        ('events', key, 'claims', claim_no, 'knowledge', number, 'holder'),
                        f'{knowledge.holder!r} 不在已给本章 referents 引用闭集中'))
    if issues:
        raise StoryCompileError(issues)
    labels = _stable_handles(model.referents, index=index, domain=f'chapter:{chapter_ordinal}:referents',
                             prefix=f'ch{chapter_ordinal:03d}.m')
    referents = {labels[key]: _canonical_chapter_evidence(referent, index)
                 for key, referent in model.referents.items()}
    events = {}
    event_handles = _stable_handles(model.events, index=index, domain=f'chapter:{chapter_ordinal}:events',
                                    prefix=f'ch{chapter_ordinal:03d}.e')
    # 阅读位置由正式证据确定；字典插入顺序不再兼任事件身份或故事时间。
    ordered = sorted(model.events.items(), key=lambda pair: min(
        (allowed[ref].chapter_ordinal, allowed[ref].paragraph_ordinal)
        for ref in pair[1].evidence_refs))
    for key, event in ordered:
        data = _canonical_chapter_evidence(event, index)
        data['participants'] = [_map_ref(ref, labels, 'chapters', ('events', key, 'participants'))
                                for ref in event.participants]
        for claim_no, claim in enumerate(data['claims']):
            for knowledge in claim['knowledge']:
                knowledge['holder'] = _map_ref(knowledge['holder'], labels, 'chapters',
                                                ('events', key, 'claims', claim_no, 'knowledge'))
        events[event_handles[key]] = data
    return _parse(ChapterObservation, {
        'contract_version': STORY_VERSION, 'source_identity': index.source_identity,
        'chapter_ordinal': chapter_ordinal, 'referents': referents, 'events': events,
        'context_needs': _canonical_chapter_evidence(model.context_needs, index),
    }, 'chapters')


def chapter_wire_path(observation: ChapterObservation | Mapping[str, Any],
                      path: Sequence[str | int], *, wire: Any = None,
                      index: SourceIndex | None = None) -> tuple[str | int, ...]:
    """已定位章观察路径→原wire路径；只换events句柄，不解析句柄数字猜位置。"""
    model = _parse(ChapterObservation, observation, 'chapters')
    if (not isinstance(path, (list, tuple)) or not path
            or any(type(part) not in (str, int) or type(part) is int and part < 0 for part in path)):
        # 非法类型不能先经StoryIssue的路径模型归一化为0/1，避免制造貌似可授权的位置。
        _fail('source_issue_path', 'chapters', (), '修订路径只接受字符串键和非负整数索引，不接受布尔或浮点数')
    result = tuple(path)
    if not result or result[0] not in ('events', 'referents', 'context_needs'):
        _fail('source_issue_path', 'chapters', result, '源问题只可定位局部语义条目，不能修改编译元数据')
    payload = model.model_dump(mode='json', exclude={'source_identity', 'chapter_ordinal'})
    if wire is not None:
        if index is None:
            _fail('input_identity', 'chapters', result, '原wire路径投影必须提供源索引核对编译身份')
        compiled = compile_chapter(wire, index=index, chapter_ordinal=model.chapter_ordinal)
        if compiled != model:
            _fail('input_changed', 'chapters', result, '冻结wire不对应当前正式章观察，先免费补提交')
        payload = _parse(ChapterWire, wire, 'chapters').model_dump(mode='json')
    if len(result) > 1 and result[0] == 'events' and result[1] in model.events and wire is not None:
        mapping = _stable_handles(payload['events'], index=index,
            domain=f'chapter:{model.chapter_ordinal}:events', prefix=f'ch{model.chapter_ordinal:03d}.e')
        inverse = {handle: key for key, handle in mapping.items()}
        result = (result[0], inverse[result[1]], *result[2:])
    if len(result) > 1 and result[0] == 'referents' and result[1] in model.referents:
        mapping = _stable_handles(payload['referents'], index=index,
            domain=f'chapter:{model.chapter_ordinal}:referents', prefix=f'ch{model.chapter_ordinal:03d}.m') if wire is not None else {}
        labels = {handle: key for key, handle in mapping.items()} if wire is not None else {key: key for key in model.referents}
        result = (result[0], labels[result[1]], *result[2:])
    current: Any = payload
    for component in result:
        if (isinstance(current, dict) and type(component) is str and component in current
                or isinstance(current, (list, tuple)) and type(component) is int
                and 0 <= component < len(current)):
            current = current[component]
        else:
            _fail('source_issue_path', 'chapters', result, '修订路径不属于当前wire条目闭集')
    return result


def _check_chapters(index: SourceIndex, chapters: Mapping[int, ChapterObservation]) -> None:
    if set(chapters) != set(index.chapter_ordinals):
        _fail('chapter_set', 'book', ('chapters',), '全书综合必须获得恰好全部源章观察')
    for ordinal, observation in chapters.items():
        if observation.chapter_ordinal != ordinal or observation.source_identity != index.source_identity:
            _fail('source_identity', 'book', ('chapters', ordinal), '章观察身份或作用域与源原文不符')


def book_prompt(index: SourceIndex, chapters: Mapping[int, ChapterObservation], *,
                include_source: bool = True) -> str:
    _check_chapters(index, chapters)
    atlas = {str(chapter): observation.model_dump(mode='json')
             for chapter, observation in sorted(chapters.items())}
    source_block = ('【全书原文，只出现一次，一段一行】\n' + _render_evidence(index)
                    if include_source else
                    '【原文按需查询】本请求只携带完整章观察，未携带原文；不得把观察当原文。'
                    '需要判断时用 need_materials 回查证据，不猜未读原文。')
    return f'''任务：进行唯一一次全书事实综合。章观察是待综合材料，原小说才是事实证据。
按人物＋事件理解全书；不是重新设计故事，不生成目标小说义务或正文。
entities/propositions/state_slots/threads 的键用语义描述；所有正式 ID 由程序生成。
entities 用 mention_refs 归属每个局部人物/地点/物件句柄，恰好覆盖一次，不遗漏未具名人物。
surfaces 每条 literal 只填一个逐字出现在自身证据段中的称呼，不把多个称呼拼成字符串；
同一实体相同 literal 只登记一次并合并证据，不用逗号、斜杠或顿号代替多条 surface。
未具名婴儿与晚揭真名是同一实体也可在早章参与；实体内部 label/surfaces 不是读者揭名许可。
events 的键必须恰好覆盖全部章事件代码句柄；只判temporal与scene_boundary。
摘要、证据、参与者由compiler从章观察继承，不抄回、不移动章归属、不暗改动作语义。
发现漏抽/错抽时只在source_issues
提交 chapter_ordinal/item/path/operation/kind/message/evidence_refs，operation必须显式选择
replace（既有条目或字段）、remove（误抽的events/referents整条语义键）或append（漏抽条目的容器）。
remove不能删除整表、必填record字段或压缩数组；append只能定位events/referents根字典或实际数组，
不可向temporal等固定record增加字段。replace不能整表重做章观察；不能靠kind或message暗示操作。
由原章owner窄修后再综合。不补造新事件，不在本回答替局部owner修正文。
propositions 不暗拆复合命题；world_status=established/refuted/undetermined 独立于人物认识。
猜“阿姐送人来”仍只是 believes，即使猜测写在章观察中也不等于原文确立；后文证伪要更新
世界地位并给 rejects/retracts 或 supersedes，不让旧误信永久与新认识并存。
knowledge_updates 的 at=story_start 表初始认识，其他 at 引用 events 的键；每项键为
语义描述，supersedes 引用被纠正的认识键。初始知情可与初始客观事实引用同一命题。
knowledge_updates 字典顺序无语义；compiler按at阅读位置及supersedes依赖派生更新顺序。
supersedes只能指向同一持有者在当前或更早阅读位置的认识，禁止自指或循环纠正。
exposures 独立记录读者何时 confirmed/claimed/suggested 某命题；禁止从命题为真推早揭示。
state_slots 的 subjects＋aspect＋具体 focus 是完整维度，如“对甲的态度”“查身世计划”，
同一人的多个目标/物品不能互相覆盖。state_updates 的 at 表阅读位置，effective_time 表
事实生效时间；过去回忆、未来计划不得覆盖当前 actual 状态。未知持续期照实 unknown。
temporal.anchor_event 引用 events 的键；只知道过去就给 past，不虚构精确时间。
causal_links 同时包含章内外明确前因、动机、条件与结果，不以拍序代替因果；区分 stated/inferred。
threads 统一具体问题与粒度，updates 先 open 再 progress/resolve/abandon；部分答案是 progress，
原书未收束的 open 合法。章内孤立 resolve 必须找原文的此前问题；找不到则进入 unresolved，
不得靠补造 open 过闸。原书无法确定的含混用conflicts.status=source_ambiguous，
原书自相矛盾用conflicts.status=source_conflict；模型没判清的同一性
或时间锚放 unresolved（left/candidates 引用已定义语义键），不要把模型未决伪装成原书未知。
时间通常在本综合完成；只剩精确时间锚问题时kind=temporal_anchor，left引用已给局部事件，
candidates只给确实可能的事件锚，left时间暂明写unknown/null。原著没说明的时间可保留unknown；
系统尚未判清的时间必须显式未决，不能用unknown字段藏掉未完成判断。
world_rules 只收有依据的规则，expression 记录原书人称/权限/声线机制，第一人称 focalizers
引用真实人物键，第三人称 narrator 用空 focalizers。所有证据只选段号。
【全部章观察，句柄由代码生成】{_json(atlas)}
{source_block}
{_MATERIALS_RULE}
【输出 schema】{_schema(BookWire)}'''


def _book_surface_issues(entities: Mapping[str, EntityWire], index: SourceIndex) -> tuple[StoryIssue, ...]:
    """一次报告全部称谓错误；整个人物称谓数组可拆分拼接字面或合并重复项。"""
    issues = []
    for key, entity in entities.items():
        seen = set()
        path = ('entities', key, 'surfaces')
        for surface in entity.surfaces:
            if surface.literal in seen:
                issues.append(_issue('duplicate_surface', 'book', path,
                    f'称谓 {surface.literal!r} 重复；同实体同称谓须合并证据后只登记一次'))
            seen.add(surface.literal)
            if not any(surface.literal in index.evidence[ref].text for ref in surface.evidence_refs):
                issues.append(_issue('surface_not_in_evidence', 'book', path,
                    f'称谓 {surface.literal!r} 未逐字出现在自身证据中；多个称呼须拆成独立surface'))
    return tuple(issues)


def _book_reference_issues(model: BookWire, mentions: Mapping[str, Any]) -> tuple[StoryIssue, ...]:
    """一次检查现有wire引用闭集；不派发ID、不猜修复值、不修改任何语义条目。"""
    issues = []
    entities, events, propositions = set(model.entities), set(model.events), set(model.propositions)
    positions = events | {'story_start'}

    def references(values, allowed, path):
        for ref in values:
            if ref not in allowed:
                issues.append(_issue('unknown_reference', 'book', path,
                    f'{ref!r} 不在已给引用闭集中'))

    for key, entity in model.entities.items():
        mismatched = []
        for ref in entity.mention_refs:
            if ref not in mentions:
                issues.append(_issue('unknown_mention', 'book', ('entities', key, 'mention_refs'),
                    f'未知局部指称 {ref!r}'))
            elif mentions[ref].kind != entity.kind:
                mismatched.append(ref)
        if mismatched:
            issues.append(_issue('mention_kind', 'book', ('entities', key),
                f'实体类别与局部指称 {mismatched!r} 不符；不得将未具名人物改成地点/物件逃避身份问题'))
    for key, event in model.events.items():
        if event.temporal.anchor_event:
            references((event.temporal.anchor_event,), events, ('events', key, 'temporal', 'anchor_event'))
    for key, proposition in model.propositions.items():
        references(proposition.entity_refs, entities, ('propositions', key, 'entity_refs'))
    for key, update in model.knowledge_updates.items():
        for field, allowed in (('holder', entities), ('proposition_ref', propositions), ('at', positions)):
            references((getattr(update, field),), allowed, ('knowledge_updates', key, field))
        references(update.supersedes, set(model.knowledge_updates), ('knowledge_updates', key, 'supersedes'))
    for number, exposure in enumerate(model.exposures):
        references((exposure.event_ref,), events, ('exposures', number, 'event_ref'))
        references((exposure.proposition_ref,), propositions, ('exposures', number, 'proposition_ref'))
    for key, slot in model.state_slots.items():
        references(slot.subjects, entities, ('state_slots', key, 'subjects'))
    for key, update in model.state_updates.items():
        references((update.slot_ref,), set(model.state_slots), ('state_updates', key, 'slot_ref'))
        references((update.at,), positions, ('state_updates', key, 'at'))
        if update.proposition_ref:
            references((update.proposition_ref,), propositions, ('state_updates', key, 'proposition_ref'))
    for number, link in enumerate(model.causal_links):
        for field in ('from_event', 'to_event'):
            references((getattr(link, field),), events, ('causal_links', number, field))
    for key, thread in model.threads.items():
        for number, update in enumerate(thread.updates):
            references((update.event_ref,), events, ('threads', key, 'updates', number, 'event_ref'))
    references(model.expression.focalizers, entities, ('expression', 'focalizers'))
    unresolved_domains = {'entity_identity': entities, 'proposition_identity': propositions,
        'thread_identity': set(model.threads), 'temporal_anchor': events}
    for number, candidate in enumerate(model.unresolved):
        allowed = unresolved_domains[candidate.kind]
        references((candidate.left,), allowed, ('unresolved', number, 'left'))
        references(candidate.candidates, allowed, ('unresolved', number, 'candidates'))
    return tuple(issues)


def _ordered_book_knowledge(updates: Mapping[str, dict], *,
                            event_ranks: Mapping[str, tuple[int, int]]) -> list[dict]:
    """JSON对象顺序不是认知顺序；只按显式阅读位置和supersedes依赖排序。"""
    keys = {update['update_id']: key for key, update in updates.items()}
    predecessors, followers = {}, {key: set() for key in updates}
    issues = []
    for key, update in updates.items():
        predecessors[key] = set()
        for replaced in update['supersedes']:
            previous_key = keys[replaced]
            previous = updates[previous_key]
            if (previous['holder'] != update['holder']
                    or event_ranks[previous['at']] > event_ranks[update['at']]):
                issues.append(_issue('knowledge_supersedes_scope', 'book', ('knowledge_updates', key),
                    f'认知修正对 {previous_key!r} 的supersedes必须指向同一持有者在当前或更早阅读位置的认识'))
            predecessors[key].add(previous_key)
            followers[previous_key].add(key)
    ready = [(event_ranks[updates[key]['at']], key)
             for key, required in predecessors.items() if not required]
    heapq.heapify(ready)
    ordered, remaining = [], set(updates)
    while ready:
        _rank, key = heapq.heappop(ready)
        remaining.remove(key)
        ordered.append(updates[key])
        for follower in sorted(followers[key]):
            predecessors[follower].remove(key)
            if not predecessors[follower]:
                heapq.heappush(ready, (event_ranks[updates[follower]['at']], follower))
    if remaining:
        # 未就绪节点也可能只是循环的下游。迭代求全部强连通分量，只授权真正
        # 循环中的条目；不逐次花费模型轮数发现另一独立环，也不受Python递归深度限制。
        visited, finished = set(), []
        for start in sorted(remaining):
            stack = [(start, False)]
            while stack:
                key, complete = stack.pop()
                if complete:
                    finished.append(key)
                elif key not in visited:
                    visited.add(key)
                    stack.append((key, True))
                    stack.extend((required, False)
                        for required in sorted(predecessors[key] & remaining, reverse=True)
                        if required not in visited)
        assigned = set()
        for start in reversed(finished):
            if start in assigned:
                continue
            component, stack = set(), [start]
            while stack:
                key = stack.pop()
                if key in assigned:
                    continue
                assigned.add(key)
                component.add(key)
                stack.extend(sorted((followers[key] & remaining) - assigned, reverse=True))
            if len(component) > 1 or start in predecessors[start]:
                issues.extend(_issue('knowledge_supersedes_cycle', 'book',
                    ('knowledge_updates', member), '认知纠正不得自指或形成循环')
                    for member in sorted(component))
    if issues:
        raise StoryCompileError(tuple(issues))
    return ordered


def compile_book(wire: Any, *, index: SourceIndex,
                 chapters: Mapping[int, ChapterObservation]) -> BookObservation:
    _check_chapters(index, chapters)
    model = _parse(BookWire, wire, 'book')
    _check_evidence(model, index, 'book')
    for number, issue in enumerate(model.source_issues):
        if issue.chapter_ordinal not in chapters:
            _fail('source_issue_chapter', 'book', ('source_issues', number), '源问题定位到了不存在的章')
        if issue.path[0] not in ('events', 'referents', 'context_needs'):
            _fail('source_issue_path', 'book', ('source_issues', number, 'path'),
                '源问题只可定位章观察语义条目，不能改编译元数据')
        chapter = chapters[issue.chapter_ordinal]
        current: Any = chapter.model_dump(mode='json')
        for component in issue.path:
            if (isinstance(current, dict) and component in current
                    or isinstance(current, (list, tuple)) and type(component) is int
                    and 0 <= component < len(current)):
                current = current[component]
            else:
                _fail('source_issue_path', 'book', ('source_issues', number, 'path'),
                      '源问题必须选择已给章观察中的真实条目或漏抽容器')
        if issue.operation == 'replace' and len(issue.path) < 2:
            _fail('source_issue_operation', 'book', ('source_issues', number, 'operation'),
                  'replace必须定位具体既有条目或字段，不能替换整个章观察集合')
        if issue.operation == 'remove':
            removable = len(issue.path) == 2 and issue.path[0] in ('events', 'referents')
            if not removable:
                _fail('source_issue_operation', 'book', ('source_issues', number, 'operation'),
                      'remove只支持既有events/referents整条语义键；其它删除须显式扩大scope')
        if issue.operation == 'append':
            extensible = (isinstance(current, (list, tuple))
                or len(issue.path) == 1 and issue.path[0] in ('events', 'referents'))
            if not extensible:
                _fail('source_issue_operation', 'book', ('source_issues', number, 'operation'),
                      'append必须定位实际数组或events/referents字典容器，不可追加record字段')
        if any(index.evidence[ref].chapter_ordinal not in
               ({issue.chapter_ordinal, 0} if issue.chapter_ordinal == 1 else {issue.chapter_ordinal})
               for ref in issue.evidence_refs):
            _fail('source_issue_evidence', 'book', ('source_issues', number), '源观察问题必须引用所属章证据')
    if model.source_issues:
        raise SourceObservationIssueRequired(model.source_issues)
    mentions = {key: value for ch in chapters.values() for key, value in ch.referents.items()}
    observed = {key: (ordinal, number, event)
                for ordinal, chapter in sorted(chapters.items())
                for number, (key, event) in enumerate(chapter.events.items(), 1)}
    preflight_issues = (*_book_surface_issues(model.entities, index), *_book_reference_issues(model, mentions))
    if preflight_issues:
        raise StoryCompileError(preflight_issues)
    mapped_mentions, mention_entities = [], {}
    entity_map = _stable_handles(model.entities, index=index, domain='book:entities', prefix='ent')
    entities = {}
    for key, entity in model.entities.items():
        for ref in entity.mention_refs:
            if ref not in mentions:
                _fail('unknown_mention', 'book', ('entities', key, 'mention_refs'), f'未知局部指称 {ref}')
            if mentions[ref].kind != entity.kind:
                _fail('mention_kind', 'book', ('entities', key), '不得将未具名人物改成地点/物件逃避身份问题')
        mapped_mentions.extend(entity.mention_refs)
        mention_entities.update((ref, entity_map[key]) for ref in entity.mention_refs)
        entities[entity_map[key]] = {
            'kind': entity.kind, 'label': entity.label, 'surfaces': entity.surfaces, 'name': None}
    if Counter(mapped_mentions) != Counter(mentions.keys()):
        missing = set(mentions) - set(mapped_mentions)
        duplicated = [key for key, count in Counter(mapped_mentions).items() if count > 1]
        _fail('mention_coverage', 'book', ('entities',),
              f'全书实体必须恰好覆盖局部指称；遗漏={sorted(missing)}，重复={duplicated}')
    if set(model.events) != set(observed):
        _fail('event_coverage', 'book', ('events',), '全书必须恰好覆盖全部局部事件，不得默删或重复')
    event_order = []
    for input_order, (key, event) in enumerate(model.events.items()):
        chapter, source_order, source_event = observed[key]
        refs = [index.evidence[ref] for ref in source_event.evidence_refs]
        if any(seg.chapter_ordinal not in (chapter, 0 if chapter == 1 else chapter) for seg in refs):
            _fail('event_chapter', 'book', ('events', key), '整书综合不得将已有事件移到别章')
        paragraph = min((seg.chapter_ordinal, seg.paragraph_ordinal) for seg in refs)
        event_order.append((chapter, paragraph, source_order, input_order, key))
    event_order.sort()
    event_map = _stable_handles(model.events, index=index, domain='book:events', prefix='ev')
    proposition_map = _stable_handles(model.propositions, index=index, domain='book:propositions', prefix='p')
    slot_map = _stable_handles(model.state_slots, index=index, domain='book:state_slots', prefix='slot')
    thread_map = _stable_handles(model.threads, index=index, domain='book:threads', prefix='th')
    knowledge_map = _stable_handles(model.knowledge_updates, index=index, domain='book:knowledge_updates', prefix='ku')
    world_rule_map = _stable_handles(model.world_rules, index=index, domain='book:world_rules', prefix='wr')
    at_map = {**event_map, 'story_start': 'story_start'}
    events, chapter_counts = {}, Counter()
    for chapter, _paragraph, _input_order, _source_order, key in event_order:
        event = model.events[key]
        source_event = observed[key][2]
        chapter_counts[chapter] += 1
        data = event.model_dump(mode='json')
        data.update(chapter_ordinal=chapter, order=chapter_counts[chapter],
                    summary=source_event.summary, evidence_refs=list(source_event.evidence_refs))
        data['participants'] = list(dict.fromkeys(
            _map_ref(ref, mention_entities, 'book', ('events', key, 'participants'))
            for ref in source_event.participants))
        if event.temporal.anchor_event:
            data['temporal']['anchor_event'] = _map_ref(event.temporal.anchor_event, event_map, 'book',
                                                       ('events', key, 'temporal'))
        events[event_map[key]] = data
    propositions = {}
    for key, proposition in model.propositions.items():
        data = proposition.model_dump(mode='json')
        data['entity_refs'] = [_map_ref(ref, entity_map, 'book', ('propositions', key))
                               for ref in proposition.entity_refs]
        propositions[proposition_map[key]] = data
    knowledge = {}
    for key, update in model.knowledge_updates.items():
        data = update.model_dump(mode='json')
        data.update(update_id=knowledge_map[key],
                    holder=_map_ref(update.holder, entity_map, 'book', ('knowledge_updates', key, 'holder')),
                    proposition_ref=_map_ref(update.proposition_ref, proposition_map, 'book',
                                             ('knowledge_updates', key, 'proposition_ref')),
                    at=_map_ref(update.at, at_map, 'book', ('knowledge_updates', key, 'at')),
                    supersedes=[_map_ref(ref, knowledge_map, 'book', ('knowledge_updates', key, 'supersedes'))
                                for ref in update.supersedes])
        knowledge[key] = data
    exposures = []
    for number, exposure in enumerate(model.exposures):
        data = exposure.model_dump(mode='json')
        data['event_ref'] = _map_ref(exposure.event_ref, event_map, 'book', ('exposures', number, 'event_ref'))
        data['proposition_ref'] = _map_ref(exposure.proposition_ref, proposition_map, 'book',
                                         ('exposures', number, 'proposition_ref'))
        exposures.append(data)
    slots = {}
    for key, slot in model.state_slots.items():
        data = slot.model_dump(mode='json')
        data['subjects'] = [_map_ref(ref, entity_map, 'book', ('state_slots', key)) for ref in slot.subjects]
        slots[slot_map[key]] = data
    updates = []
    update_map = _stable_handles(model.state_updates, index=index, domain='book:state_updates', prefix='su')
    for key, update in model.state_updates.items():
        data = update.model_dump(mode='json')
        data.update(update_id=update_map[key],
                    slot_ref=_map_ref(update.slot_ref, slot_map, 'book', ('state_updates', key, 'slot_ref')),
                    at=_map_ref(update.at, at_map, 'book', ('state_updates', key, 'at')))
        if update.proposition_ref:
            data['proposition_ref'] = _map_ref(update.proposition_ref, proposition_map, 'book',
                                             ('state_updates', key, 'proposition_ref'))
        updates.append(data)
    causal = []
    for number, link in enumerate(model.causal_links):
        data = link.model_dump(mode='json')
        for field in ('from_event', 'to_event'):
            data[field] = _map_ref(data[field], event_map, 'book', ('causal_links', number, field))
        causal.append(data)
    threads = {}
    for key, thread in model.threads.items():
        data = thread.model_dump(mode='json')
        for number, update in enumerate(data['updates']):
            update['event_ref'] = _map_ref(update['event_ref'], event_map, 'book', ('threads', key, number))
        threads[thread_map[key]] = data
    expression = model.expression.model_dump(mode='json')
    expression['focalizers'] = [_map_ref(ref, entity_map, 'book', ('expression', 'focalizers'))
                                for ref in model.expression.focalizers]
    if model.expression.person == 'first' and len(expression['focalizers']) != 1:
        _fail('first_person_holder', 'book', ('expression', 'focalizers'),
              '第一人称必须明确对应一个真实人物，未知时人称应保留 unknown')
    # 将全书级跨字段错误定位回语义条目，避免根模型错误把窄修扩大成整书重购。
    event_ranks = {key: (event['chapter_ordinal'], event['order']) for key, event in events.items()}
    event_ranks['story_start'] = (0, 0)
    knowledge = _ordered_book_knowledge(knowledge, event_ranks=event_ranks)
    inverse_props = {formal: semantic for semantic, formal in proposition_map.items()}
    for number, exposure in enumerate(exposures):
        if exposure['mode'] == 'confirmed' and propositions[exposure['proposition_ref']]['world_status'] != 'established':
            _fail('exposure_truth_conflict', 'book', ('exposures', number),
                  f'命题 {inverse_props[exposure["proposition_ref"]]!r} 世界地位未确立，不得标成读者确认')
    for key, thread in threads.items():
        semantic = next(label for label, formal in thread_map.items() if formal == key)
        opened, terminal, previous_rank = False, False, (0, 0)
        for number, update in enumerate(thread['updates']):
            path = ('threads', semantic, 'updates', number)
            rank = event_ranks[update['event_ref']]
            if rank < previous_rank:
                _fail('thread_order', 'book', ('threads', semantic), '源线程更新阅读序倒转；修订范围是本线程')
            if update['op'] == 'open':
                if opened:
                    _fail('thread_duplicate_open', 'book', ('threads', semantic), '同一线程不得用第二次 open 改写问题')
                opened = True
            elif not opened or terminal:
                _fail('thread_isolated_close', 'book', ('threads', semantic),
                      '整合线程不得孤立推进/收束，也不得在终止后继续；需查此前问题或保留未决')
            if update['op'] in ('resolve', 'abandon'):
                terminal = True
            previous_rank = rank
    unresolved = []
    maps = {'entity_identity': entity_map, 'proposition_identity': proposition_map,
            'thread_identity': thread_map, 'temporal_anchor': event_map}
    for number, item in enumerate(model.unresolved):
        mapping = maps[item.kind]
        data = item.model_dump(mode='json')
        data['left'] = _map_ref(item.left, mapping, 'book', ('unresolved', number, 'left'))
        data['candidates'] = [_map_ref(ref, mapping, 'book', ('unresolved', number, 'candidates'))
                              for ref in item.candidates]
        if data['left'] in data['candidates']:
            _fail('association_self', 'book', ('unresolved', number), '同一性候选不得自指')
        if item.kind == 'temporal_anchor' and events[data['left']]['temporal'] != {
                'frame': 'unknown', 'anchor_event': None}:
            _fail('association_temporal_target', 'book', ('unresolved', number),
                  '时间窄判只接受尚未解释的目标事件，不能改已有时间判定')
        unresolved.append(data)
    if len({item.key for item in model.unresolved}) != len(model.unresolved):
        _fail('association_key', 'book', ('unresolved',), '未决问题语义键重复')
    temporal_targets = [item['left'] for item in unresolved if item['kind'] == 'temporal_anchor']
    if len(temporal_targets) != len(set(temporal_targets)):
        _fail('association_temporal_target', 'book', ('unresolved',), '同一事件不能有多份时间窄判真源')
    cited = ({ref for _path, refs in _evidence_refs(model) for ref in refs}
             | {ref for event in events.values() for ref in event['evidence_refs']})
    story = _parse(SourceStoryDraft, {
        'contract_version': STORY_VERSION, 'source_identity': index.source_identity,
        'entities': entities, 'events': events, 'propositions': propositions,
        'knowledge_updates': knowledge, 'exposures': exposures, 'state_slots': slots,
        'state_updates': updates, 'causal_links': causal, 'threads': threads,
        'world_rules': {world_rule_map[key]: rule for key, rule in model.world_rules.items()},
        'expression': expression, 'conflicts': model.conflicts,
        'evidence': {ref: seg for ref, seg in index.evidence.items() if ref in cited},
    }, 'book')
    return BookObservation(
        contract_version=STORY_VERSION, story=story, entity_handles=entity_map,
        event_handles=event_map, proposition_handles=proposition_map, slot_handles=slot_map,
        thread_handles=thread_map, chapter_digests={ordinal: _digest(value) for ordinal, value in chapters.items()},
        unresolved=unresolved)


_BOOK_MAPS = ('entities', 'events', 'propositions', 'knowledge_updates', 'state_slots', 'state_updates', 'threads', 'world_rules')
_BOOK_LISTS = ('exposures', 'causal_links', 'conflicts', 'unresolved')


def _book_dependencies(value: Any) -> tuple[set[tuple[str, str]], set[str]]:
    """只追显式引用字段，不从自然语言相似度猜依赖。"""
    domains = {'mention_refs': 'mentions', 'holder': 'entities', 'entity_refs': 'entities',
        'participants': 'entities', 'subjects': 'entities', 'focalizers': 'entities',
        'event_ref': 'events', 'at': 'events', 'anchor_event': 'events',
        'from_event': 'events', 'to_event': 'events', 'proposition_ref': 'propositions',
        'slot_ref': 'state_slots', 'supersedes': 'knowledge_updates'}
    refs, evidence = set(), set()
    def visit(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key in ('evidence_refs', 'source_refs') and isinstance(child, (list, tuple)):
                    evidence.update(child)
                elif key in domains:
                    values = child if isinstance(child, (list, tuple)) else (child,)
                    refs.update((domains[key], one) for one in values if isinstance(one, str))
                elif key in ('left', 'candidates') and item.get('kind') in (
                        'entity_identity', 'proposition_identity', 'thread_identity', 'temporal_anchor'):
                    domain = {'entity_identity': 'entities', 'proposition_identity': 'propositions',
                              'thread_identity': 'threads', 'temporal_anchor': 'events'}[item['kind']]
                    values = child if isinstance(child, (list, tuple)) else (child,)
                    refs.update((domain, one) for one in values)
                else:
                    visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
    visit(value)
    return refs, evidence


def book_repair_scope(wire: Any, *, index: SourceIndex,
                      before: Mapping[int, ChapterObservation],
                      after: Mapping[int, ChapterObservation]) -> BookRepairScope:
    """圈定章修订的显式引用/证据闭包；范围未知不默认整书重做。"""
    _check_chapters(index, before)
    _check_chapters(index, after)
    model = _parse(BookWire, wire, 'book')
    _check_evidence(model, index, 'book')
    changed = tuple(ch for ch in before if _digest(before[ch]) != _digest(after[ch]))
    if not changed:
        _fail('book_repair_no_progress', 'book', ('source_issues',), '源章观察语义未改变，不能重购全书综合掩盖无进展')
    affected, evidence = set(), set()
    for chapter in changed:
        for field, domain in (('referents', 'mentions'), ('events', 'events')):
            old, new = getattr(before[chapter], field), getattr(after[chapter], field)
            for key in old.keys() | new.keys():
                if key not in old or key not in new or old[key] != new[key]:
                    affected.add((domain, key))
                    for table in (old, new):
                        if key in table:
                            evidence.update(table[key].evidence_refs)
    for issue in model.source_issues:
        if issue.chapter_ordinal in changed:
            evidence.update(issue.evidence_refs)
    data = model.model_dump(mode='json')
    allowed = {('source_issues',)}
    entries = [(root, key, value) for root in _BOOK_MAPS for key, value in data[root].items()]
    entries += [(root, number, value) for root in _BOOK_LISTS for number, value in enumerate(data[root])]
    progress = True
    while progress:
        progress = False
        for root, key, value in entries:
            refs, support = _book_dependencies(value)
            # 同一段行动被修正并不意味着出现于此段的人物身份也变了。实体统一只由
            # mention/实体的显式引用传播；否则一个段落会把整书人物关系都圈进窄修。
            if (root == 'events' and ('events', key) in affected or refs & affected
                    or root != 'entities' and support & evidence):
                allowed.add((root, key))
                if root in _BOOK_MAPS and (root, key) not in affected:
                    affected.add((root, key))
                    progress = True
    current_events = {key for chapter in after.values() for key in chapter.events}
    allowed.update(('events', key) for domain, key in affected if domain == 'events' and key in current_events)
    if any(('entities', ref) in affected for ref in model.expression.focalizers):
        allowed.add(('expression', 'focalizers'))
    grouped = {domain: tuple(sorted(key for owner, key in affected if owner == domain))
               for domain in sorted({domain for domain, _key in affected})}
    return BookRepairScope(contract_version=STORY_VERSION, source_identity=index.source_identity,
        content_sha256=index.content_sha256, baseline_sha256=_digest(wire),
        before_digests={ch: _digest(value) for ch, value in before.items()},
        after_digests={ch: _digest(value) for ch, value in after.items()}, changed_chapters=changed,
        affected_evidence=tuple(ref for ref in index.evidence if ref in evidence), affected_references=grouped,
        allowed_paths=tuple(sorted(allowed, key=lambda path: tuple(map(str, path)))))


def apply_book_repair(wire: Any, repair: Any, *, scope: BookRepairScope,
                      index: SourceIndex) -> tuple[dict, tuple[tuple[str | int, ...], ...]]:
    """逐项set/remove；保留其余值和顺序，新增联系显式留证。"""
    if isinstance(wire, BaseModel):
        wire = wire.model_dump(mode='json')
    if (_digest(wire) != scope.baseline_sha256 or index.source_identity != scope.source_identity
            or index.content_sha256 != scope.content_sha256):
        _fail('book_repair_identity', 'book', ('changes',), '冻结候选或源身份已改变，拒绝采用旧权限')
    patch = _parse(BookRepairWire, repair, 'book')
    data, allowed = deepcopy(wire), set(scope.allowed_paths)
    affected = {(domain, ref) for domain, refs in scope.affected_references.items() for ref in refs}
    evidence, expanded, seen, removals, appends = set(scope.affected_evidence), [], set(), [], []
    acknowledged = False
    for number, change in enumerate(patch.changes):
        path = tuple(change.path)
        if path in seen:
            _fail('book_repair_duplicate', 'book', ('changes', number), '同一路径不能重复修订')
        seen.add(path)
        if not set(change.evidence_refs) <= set(index.evidence) or not set(change.evidence_refs) & evidence:
            _fail('book_repair_evidence', 'book', ('changes', number), '修订必须明引受影响的冻结源证据')
        if path == ('source_issues',):
            if change.operation != 'set' or not isinstance(change.value, list):
                _fail('book_repair_source_issues', 'book', ('changes', number), 'source_issues须显式重判为数组')
            if wire['source_issues'] and change.value == wire['source_issues']:
                _fail('book_repair_no_progress', 'book', ('changes', number), '不能原样保留已回派的source_issues')
            acknowledged = True
            data['source_issues'] = deepcopy(change.value)
            continue
        if len(path) != 2 or path[0] not in (*_BOOK_MAPS, *_BOOK_LISTS, 'expression'):
            _fail('book_repair_scope', 'book', ('changes', number), '只可修改具体整合条目，禁止整表/整书替换')
        root, key = path
        if root == 'expression' and path != ('expression', 'focalizers'):
            _fail('book_repair_scope', 'book', ('changes', number), '章修订不能改变全书表达机制')
        if path not in allowed:
            dependencies, supporting = _book_dependencies(change.value)
            if change.operation != 'set' or not (dependencies & affected or supporting & evidence):
                _fail('book_repair_scope', 'book', ('changes', number), '新联系须显式引用已受影响对象或证据')
            expanded.append(path)
            if root in _BOOK_MAPS:
                affected.add((root, str(key)))
        parent = data[root]
        if isinstance(parent, dict):
            if not isinstance(key, str) or change.operation == 'remove' and key not in parent:
                _fail('book_repair_path', 'book', ('changes', number), '字典只允许具体既有键或新增语义键')
            if change.operation == 'remove':
                del parent[key]
            else:
                parent[key] = deepcopy(change.value)
        elif isinstance(parent, list) and isinstance(key, int) and not isinstance(key, bool):
            if 0 <= key < len(wire[root]):
                if change.operation == 'remove':
                    removals.append((root, key))
                else:
                    parent[key] = deepcopy(change.value)
            elif change.operation == 'set' and key >= len(wire[root]):
                appends.append((root, key, deepcopy(change.value)))
            else:
                _fail('book_repair_path', 'book', ('changes', number), '数组只允许既有条目或尾部显式新增')
        else:
            _fail('book_repair_path', 'book', ('changes', number), '修订路径类型错误')
    if not acknowledged:
        _fail('book_repair_source_issues', 'book', ('changes',), '必须显式重判已回派问题，不能采用过期候选')
    for root, key in sorted(removals, key=lambda item: (item[0], -item[1])):
        del data[root][key]
    counts = {}
    for root, key, value in sorted(appends, key=lambda item: (item[0], item[1])):
        if key != len(wire[root]) + counts.get(root, 0):
            _fail('book_repair_path', 'book', ('changes',), '新增数组条目须从原尾部连续增配')
        data[root].append(value)
        counts[root] = counts.get(root, 0) + 1
    return data, tuple(expanded)


def book_repair_prompt(wire: Any, *, scope: BookRepairScope,
                       after: Mapping[int, ChapterObservation]) -> str:
    changed = {ch: after[ch].model_dump(mode='json') for ch in scope.changed_chapters}
    return f'''任务：继续同一份已购全书整合，只修源章修订影响的条目，不重新综合全书。
冻结候选未发布，不能直接采用。只返回book_repair逐项set/remove，不返回整书BookWire。
allowed_paths是显式引用/证据闭包。新增联系须单列具体路径、reason及受影响证据，替代条目
也须显式引用受影响对象或证据；代码记录扩大。禁止整表替换或改无关条目。
数组路径使用冻结候选原索引；新增只在原长度之后连续append。新语义键由代码增配正式ID。
必须显式重判source_issues（修复完成用[]），不能代写局部事件。
【冻结候选】{_json(wire)}
【身份与权限】{_json(scope)}
【实际改变的章观察】{_json(changed)}
{_MATERIALS_RULE}
【输出schema】{_schema(BookRepairWire)}'''


def _name_story(book: BookObservation, associate: AssociateObservation | None, *,
                index: SourceIndex | None = None) -> SourceStoryDraft:
    if associate is not None:
        associate = compile_associate(associate, book=book, index=index)
    if book.unresolved:
        if associate is None:
            _fail('association_required', 'associate', ('unresolved',), '姓名解析须等待已登记的精确关联未决收口')
        verdicts = compile_associate(associate, book=book, index=index)
        return _parse(SourceStoryDraft, _apply_associate(book, verdicts), 'associate')
    if associate is not None and associate.verdicts:
        _fail('stray_association', 'associate', ('verdicts',), '无候选不得携带额外判定')
    return book.story


def names_prompt(book: BookObservation, *, associate: AssociateObservation | None = None,
                 index: SourceIndex | None = None) -> str:
    story = _name_story(book, associate, index=index)
    people = {key: {'label': entity.label, 'surfaces': [surface.literal for surface in entity.surfaces]}
              for key, entity in story.entities.items() if entity.kind == 'person'}
    refs = {ref for entity in story.entities.values() if entity.kind == 'person' for surface in entity.surfaces
            for ref in surface.evidence_refs}
    evidence = {ref: seg.text for ref, seg in story.evidence.items() if ref in refs}
    schema = NamesWire.model_json_schema()
    person_schema = schema['$defs']['PersonNameWire']
    properties = {}
    for key, person in people.items():
        entry = deepcopy(person_schema)
        form_schema = deepcopy(entry['properties']['surfaces']['additionalProperties'])
        entry['properties']['surfaces'] = {'type': 'object',
            'properties': {surface: deepcopy(form_schema) for surface in person['surfaces']},
            'required': person['surfaces'], 'additionalProperties': False}
        component_schema = {'type': 'string', 'minLength': 1, 'pattern': r'\S'}
        entry['properties']['aliases'].update(uniqueItems=True, items=component_schema)
        for field in ('family', 'given'):
            entry['properties'][field] = {'anyOf': [component_schema, {'type': 'null'}]}
        properties[key] = entry
    schema['properties']['people'] = {'type': 'object', 'properties': properties,
        'required': list(people), 'additionalProperties': False}
    return f'''任务：只解析源姓名结构，不增删合并实体，不裁决首次出现与读者揭名。
people 必须恰好覆盖所有给定 person ID。family/given 不明或纯称谓人物给 null；
family_group 是有证据的亲属同姓群语义标签，不凭同姓自动并家族。
aliases 只登记独立化名原字面，不输出alias ID。surfaces必须恰覆盖该人物给定的称谓字面。
每个称谓按原顺序显式分段：普通文本用{{"kind":"text","literal":"原字面"}}；
本人姓或名分别用{{"kind":"family"}}、{{"kind":"given"}}；化名用{{"kind":"alias","literal":"已声明化名原字面"}}。
所有分段展开后必须逐字等于该称谓。即使普通文本含同一姓名字符，也保持text；
例如姓王的王妃“王王妃”应分为family与text“王妃”，不得把两处王都当姓。
纯称谓使用全text分段，family/given=null、aliases=[]；每个非null部件与声明化名均须实际被引用。
正式化名ID由compiler派发，不输出字符串模板、alias_ref或机械编号。
【人物】{_json(people)}
【原文证据】{_json(evidence)}
{_MATERIALS_RULE}
【输出 schema】{_json(schema)}'''


def compile_names(wire: Any, *, book: BookObservation,
                  associate: AssociateObservation | None = None,
                  index: SourceIndex | None = None) -> NamesObservation:
    story = _name_story(book, associate, index=index)
    model = _parse(NamesWire, wire, 'names')
    expected = {key for key, entity in story.entities.items() if entity.kind == 'person'}
    issues = []

    def report(code, path, message):
        issues.append(_issue(code, 'names', path, message))

    if set(model.people) != expected:
        report('name_coverage', ('people',), 'names 必须恰好覆盖 person 实体，不得增删拆合')
    people = {}
    for key, person in model.people.items():
        if key not in expected:
            continue
        first_issue = len(issues)
        surfaces = {surface.literal for surface in story.entities[key].surfaces}
        if set(person.surfaces) != surfaces:
            report('name_surface_coverage', ('people', key, 'surfaces'), '姓名分段必须恰好覆盖本人物称谓闭集')
        aliases = {f'al{number:02d}': literal for number, literal in enumerate(person.aliases, 1)}
        alias_refs = {literal: alias for alias, literal in aliases.items()}
        if len(set(person.aliases)) != len(person.aliases):
            report('duplicate_alias', ('people', key, 'aliases'), '化名字面重复')
        for field in ('family', 'given'):
            literal = getattr(person, field)
            if literal is not None and not literal.strip():
                report('name_component', ('people', key, field), '姓名部件必须是非空普通原文字面；未知用null')
        for number, literal in enumerate(person.aliases):
            if not literal.strip():
                report('name_component', ('people', key, 'aliases', number), '化名必须是非空原文字面')
        forms = {}
        used_primary, used_aliases = set(), set()
        for surface in sorted(surfaces & set(person.surfaces)):
            parts, rendered = [], []
            renderable = True
            for number, part in enumerate(person.surfaces[surface]):
                path = ('people', key, 'surfaces', surface, number)
                if part.kind == 'text':
                    parts.append(part.model_dump(mode='json'))
                    rendered.append(part.literal)
                elif part.kind in ('family', 'given'):
                    literal = getattr(person, part.kind)
                    used_primary.add(part.kind)
                    if literal is None:
                        report('name_component_reference', path, '姓名分段不能引用null部件')
                        renderable = False
                        continue
                    if not literal.strip():
                        renderable = False
                    parts.append(part.model_dump(mode='json'))
                    rendered.append(literal)
                else:
                    if part.literal not in alias_refs:
                        report('name_alias_reference', path, '化名分段只能引用本人物已声明化名原字面')
                        renderable = False
                        continue
                    used_aliases.add(part.literal)
                    if not part.literal.strip():
                        renderable = False
                    parts.append({'kind': 'alias', 'alias_ref': alias_refs[part.literal]})
                    rendered.append(part.literal)
            if renderable and ''.join(rendered) != surface:
                report('name_render', ('people', key, 'surfaces', surface), '姓名分段必须逐字还原原称谓')
            forms[surface] = parts
        # 字面覆盖不完整时无法断言声明未被使用；仍检查已给合法surface的独立错误。
        if set(person.surfaces) == surfaces:
            for field in ('family', 'given'):
                literal = getattr(person, field)
                if literal is not None and literal.strip() and field not in used_primary:
                    report('name_component_unused', ('people', key),
                        f'未使用原字段people[{key!r}].{field}：须在本人物内重判声明或分段角色；compiler不猜应删哪个声明或改哪条surface')
            for number, literal in enumerate(person.aliases):
                if literal.strip() and literal not in used_aliases:
                    report('name_alias_coverage', ('people', key),
                        f'未使用原字段people[{key!r}].aliases[{number}]：须在本人物内重判化名声明或分段角色')
        if len(issues) != first_issue:
            continue
        name = _parse(StoryNameStructure, {'family_group': person.family_group, 'family': person.family,
            'given': person.given, 'aliases': aliases, 'surface_forms': forms}, 'names')
        people[key] = name
    if issues:
        raise StoryCompileError(issues)
    return NamesObservation(contract_version=STORY_VERSION, people=people)


def _associate_supplements(book: BookObservation, *, index: SourceIndex | None,
                           materials: Sequence[dict]) -> tuple[AssociationEvidence, ...]:
    """只编译运行边界实际送达的候选补料，不从模型回答接纳证据权限。"""
    if not materials:
        return ()
    if index is None or index.source_identity != book.story.source_identity:
        _fail('association_material_source', 'associate', (), '关联补料必须绑定当前真实源索引')
    expected = {item.key: item for item in book.unresolved}
    grouped: dict[str, dict[str, SourceEvidence]] = {}
    for material in materials:
        key = material.get('candidate_key')
        if (not isinstance(key, str) or key not in expected
                or material.get('source_identity') != index.source_identity
                or material.get('index_version') != index.index_version
                or not isinstance(material.get('segments'), dict)):
            _fail('association_material_source', 'associate', (), '补充材料不属于当前源或冻结候选')
        for ref, raw in material['segments'].items():
            segment = _parse(SourceEvidence, raw, 'associate')
            if ref not in index.evidence or segment != index.evidence[ref]:
                _fail('association_material_source', 'associate', (), '补充证据必须逐字等于当前源索引')
            if ref not in expected[key].evidence_refs:
                grouped.setdefault(key, {})[ref] = segment
    return tuple(AssociationEvidence(candidate_key=key, candidate_sha256=_digest(expected[key]),
        evidence=grouped[key]) for key in sorted(grouped))


def _validate_associate_supplements(observation: AssociateObservation, *, book: BookObservation,
                                    index: SourceIndex | None) -> None:
    if (observation.source_identity != book.story.source_identity
            or observation.book_sha256 != _digest(book)):
        _fail('association_input_changed', 'associate', (), '正式关联结果的源或整书候选身份已变化')
    if observation.supplemental_evidence and (index is None or index.source_identity != observation.source_identity):
        _fail('association_material_source', 'associate', (), '正式补充证据复验必须提供同一真实源索引')
    expected = {item.key: item for item in book.unresolved}
    seen = set()
    for supplement in observation.supplemental_evidence:
        key = supplement.candidate_key
        if (key not in expected or key in seen or supplement.candidate_sha256 != _digest(expected[key])
                or not supplement.evidence or set(supplement.evidence) & set(expected[key].evidence_refs)):
            _fail('association_material_source', 'associate', (), '正式补充证据的候选分组、身份或范围不合法')
        seen.add(key)
        for ref, segment in supplement.evidence.items():
            if ref not in index.evidence or segment != index.evidence[ref]:
                _fail('association_material_source', 'associate', (), '正式补充证据与真实源字节不一致')


def associate_prompt(book: BookObservation, *, index: SourceIndex | None = None,
                      materials: Sequence[dict] = ()) -> str | None:
    if not book.unresolved:
        return None
    supplements = _associate_supplements(book, index=index, materials=materials)
    extra = {item.candidate_key: item.evidence for item in supplements}
    refs = {ref for item in book.unresolved for ref in item.evidence_refs}
    tables = {
        'entities': book.story.entities, 'propositions': book.story.propositions,
        'threads': book.story.threads, 'events': book.story.events,
    }
    selected = {}
    for item in book.unresolved:
        table = tables[{'entity_identity': 'entities', 'proposition_identity': 'propositions',
                           'thread_identity': 'threads', 'temporal_anchor': 'events'}[item.kind]]
        for key in (item.left, *item.candidates):
            selected[key] = table[key].model_dump(mode='json')
    evidence = {ref: book.story.evidence[ref].text for ref in sorted(refs)}
    evidence.update((ref, segment.text) for supplement in supplements for ref, segment in supplement.evidence.items())
    schema = AssociateWire.model_json_schema()
    branches = []
    for item in book.unresolved:
        temporal = item.kind == 'temporal_anchor'
        branch = deepcopy(schema['$defs']['TemporalAnchorVerdict' if temporal else 'AssociationVerdict'])
        branch['properties']['key'] = {'type': 'string', 'const': item.key}
        allowed_refs = list(dict.fromkeys((*item.evidence_refs, *extra.get(item.key, {}))))
        branch['properties']['evidence_refs'].update(items={'type': 'string', 'enum': allowed_refs})
        branch['oneOf'] = ([
            {'properties': {'verdict': {'const': 'resolved'},
                'frame': {'enum': ['current', 'past', 'future', 'summary']},
                'anchor_event': {'enum': list(item.candidates)}}},
            {'properties': {'verdict': {'enum': ['source_unknown', 'unresolved']},
                'frame': {'const': 'unknown'}, 'anchor_event': {'type': 'null'}}},
        ] if temporal else [
            {'properties': {'verdict': {'const': 'same'}, 'right': {'enum': list(item.candidates)}},
             'required': ['right']},
            {'properties': {'verdict': {'enum': ['distinct', 'source_unknown', 'unresolved']},
                'right': {'type': 'null'}}},
        ])
        branches.append(branch)
    schema['properties']['verdicts'] = {'type': 'array', 'minItems': len(branches),
        'maxItems': len(branches), 'items': {'oneOf': branches}, 'allOf': [
            {'contains': {'type': 'object', 'properties': {'key': {'const': item.key}},
                          'required': ['key']}, 'minContains': 1, 'maxContains': 1}
            for item in book.unresolved]}
    return f'''任务：只回答剩余精确候选，不重新综合全书、改写命题或补造事件。
每个 key 恰答一次。entity/proposition/thread_identity用kind=identity分支：
same 只选其 candidates 中一个 right；distinct 否决本候选集的同一性。
temporal_anchor用kind=temporal_anchor独立分支，只判该left事件的frame及给定候选anchor_event：
resolved明确frame和一个候选anchor_event；source_unknown/unresolved明确frame=unknown、anchor_event=null。
不得用same/distinct替代时间判断，也不得改变其他事件时间或叙事阅读序。
source_unknown 表原书本来不明，unresolved 表模型仍没判清。两者不可混淆。
证据只能引用该候选初始evidence_refs与真正送达该候选的补充证据，不能借用其他候选的材料。
材料不足时need_materials的每个requests查询必须另填candidate_key，值为下面已给key之一。
例如{{"kind":"need_materials","requests":[{{"candidate_key":"已给候选key","operation":"read_segments","segment_ids":["已有段号"],"adjacent_paragraphs":0}}]}}。
也可查询{{"candidate_key":"已给候选key","operation":"search_literal","terms":["原文字面"],
"mode":"all","chapter_range":[0,全书末章],"cursor":0,"limit":20}}；每个查询都必须绑定候选。
代码负责源身份、候选身份和正式补充证据；业务回答只含contract_version与verdicts，不自报这些元数据。
【未决】{_json([item.model_dump(mode='json') for item in book.unresolved])}
【候选内容】{_json(selected)}
【证据】{_json(evidence)}
【已送达补充证据权限】{_json({key: list(segments) for key, segments in extra.items()})}
查询操作与complete/truncated/empty语义沿用材料协议；empty不能证明不存在，truncated须继续翻页或保留未决。
【输出 schema】{_json(schema)}'''


def compile_associate(wire: Any, *, book: BookObservation, index: SourceIndex | None = None,
                       materials: Sequence[dict] = ()) -> AssociateObservation:
    if isinstance(wire, AssociateObservation):
        if materials:
            _fail('association_material_source', 'associate', (), '正式关联读回不能用自报材料重新签发元数据')
        model = _parse(AssociateObservation, wire, 'associate')
    else:
        answer = _parse(AssociateWire, wire, 'associate')
        model = AssociateObservation(contract_version=STORY_VERSION, verdicts=answer.verdicts,
            source_identity=book.story.source_identity, book_sha256=_digest(book),
            supplemental_evidence=_associate_supplements(book, index=index, materials=materials))
    _validate_associate_supplements(model, book=book, index=index)
    supplements = {item.candidate_key: item.evidence for item in model.supplemental_evidence}
    expected = {item.key: item for item in book.unresolved}
    if Counter(verdict.key for verdict in model.verdicts) != Counter(expected.keys()):
        _fail('association_coverage', 'associate', ('verdicts',), '必须恰好回答全部已给未决候选')
    issues = []
    for number, verdict in enumerate(model.verdicts):
        item = expected[verdict.key]
        if (item.kind == 'temporal_anchor') != (verdict.kind == 'temporal_anchor'):
            issues.append(_issue('association_branch', 'associate', ('verdicts', number),
                                 '回答分支必须匹配已给候选类型'))
        selected = verdict.anchor_event if verdict.kind == 'temporal_anchor' else verdict.right
        if selected is not None and selected not in item.candidates:
            issues.append(_issue('association_candidate', 'associate', ('verdicts', number),
                                 '判定只能选择已给候选'))
        if not set(verdict.evidence_refs) <= set(item.evidence_refs) | set(supplements.get(item.key, {})):
            issues.append(_issue('association_evidence', 'associate', ('verdicts', number),
                                 '判定不得引用未给本候选的证据'))
    if issues:
        raise StoryCompileError(issues)
    # unresolved 是合法的中间观察，仍由发布入口显式阻断；已决回答则须在付费接受前
    # 真正应用并验证，不能先接受再把不可消费的结果交到 names 才暴露。
    if not any(verdict.verdict == 'unresolved' for verdict in model.verdicts):
        data = _apply_associate(book, model)
        try:
            _parse(SourceStoryDraft, data, 'associate')
        except StoryCompileError as exc:
            # same 只授予身份合并，不授予删除生命周期或改写状态的语义权限。
            # 无具体可修的 verdict 字段时立即停，不诱导模型改为 distinct 来绕过。
            raise StoryCompileError(tuple(_issue('association_merge_scope', 'associate', (),
                '已决关联应用后不能满足正式事实契约；当前协议没有改写生命周期或状态的权限，'
                '不得改为distinct绕过，须明确修订范围：' + str(exc), item=verdict.key)
                for verdict in model.verdicts if verdict.verdict in ('same', 'resolved'))) from exc
    return model


def _apply_associate(book: BookObservation, observation: AssociateObservation) -> dict[str, Any]:
    data = book.story.model_dump(mode='json')
    for supplement in observation.supplemental_evidence:
        data['evidence'].update({ref: segment.model_dump(mode='json')
                                for ref, segment in supplement.evidence.items()})
    unresolved = {item.key: item for item in book.unresolved}
    maps = {'entity_identity': {}, 'proposition_identity': {}, 'thread_identity': {}}

    def root(mapping, key):
        visited = set()
        while key in mapping:
            if key in visited:
                _fail('association_cycle', 'associate', ('verdicts',), '同一性替换出现环')
            visited.add(key)
            key = mapping[key]
        return key

    def merge_issues(code, kind, key, message):
        mapping = maps[kind]
        target = root(mapping, key)
        return tuple(_issue(code, 'associate', ('verdicts', number), message, item=verdict.key)
            for number, verdict in enumerate(observation.verdicts)
            if verdict.kind == 'identity' and verdict.verdict == 'same'
            and unresolved[verdict.key].kind == kind
            and root(mapping, unresolved[verdict.key].left) == target)

    def merge_scope(kind, key, detail):
        message = ('same关联需要超出身份选择的语义修订，当前协议缺少该修订权限；'
                   '不得改为distinct绕过，须明确修订范围：' + detail)
        raise StoryCompileError(tuple(issue.model_copy(update={'path': ()})
            for issue in merge_issues('association_merge_scope', kind, key, message)))

    for verdict in observation.verdicts:
        item = unresolved[verdict.key]
        if verdict.verdict == 'unresolved':
            _fail('association_unresolved', 'associate', ('verdicts', verdict.key), '模型未决不得伪装成已发布事实')
        if verdict.verdict == 'source_unknown':
            data['conflicts'].append({'kind': item.kind, 'statement': item.question,
                                      'source_refs': list(verdict.evidence_refs), 'status': 'source_ambiguous'})
        if verdict.kind == 'temporal_anchor':
            if verdict.verdict == 'resolved':
                data['events'][item.left]['temporal'] = {
                    'frame': verdict.frame, 'anchor_event': verdict.anchor_event}
            continue
        if verdict.verdict == 'same':
            mapping = maps[item.kind]
            left, right = root(mapping, item.left), root(mapping, verdict.right)
            if left != right:
                keep, drop = sorted((left, right))
                mapping[drop] = keep
    # distinct 否决整个候选集；传递同一闭包也不能把其中任一候选重新并回 left。
    # 相关same和distinct一起交回窄修，不能默认哪一条语义判断正确。
    for number, verdict in enumerate(observation.verdicts):
        if verdict.kind != 'identity' or verdict.verdict != 'distinct':
            continue
        item = unresolved[verdict.key]
        mapping = maps[item.kind]
        if any(root(mapping, item.left) == root(mapping, candidate) for candidate in item.candidates):
            message = 'distinct与same的传递同一闭包矛盾，须按证据重判这些关联，不能同时接受'
            raise StoryCompileError((_issue('association_identity_conflict', 'associate',
                ('verdicts', number), message, item=verdict.key),
                *merge_issues('association_identity_conflict', item.kind, item.left, message)))
    entity_map, prop_map, thread_map = (maps[key] for key in ('entity_identity', 'proposition_identity', 'thread_identity'))
    for drop in sorted(entity_map):
        keep = root(entity_map, drop)
        source, destination = data['entities'][drop], data['entities'][keep]
        if source['kind'] != destination['kind']:
            raise StoryCompileError(merge_issues('association_kind', 'entity_identity', drop,
                '不同实体类别不得合并'))
        existing = {surface['literal']: surface for surface in destination['surfaces']}
        for surface in source['surfaces']:
            if surface['literal'] in existing:
                entry = existing[surface['literal']]
                entry['evidence_refs'] = list(dict.fromkeys(entry['evidence_refs'] + surface['evidence_refs']))
            else:
                destination['surfaces'].append(surface)
                existing[surface['literal']] = surface
        del data['entities'][drop]
    for drop in sorted(prop_map):
        keep = root(prop_map, drop)
        if data['propositions'][keep]['world_status'] != data['propositions'][drop]['world_status']:
            merge_scope('proposition_identity', drop, '同一命题的世界地位冲突，身份判定不能选择或改写world_status')
        del data['propositions'][drop]
    for drop in sorted(thread_map):
        keep = root(thread_map, drop)
        data['threads'][keep]['updates'].extend(data['threads'][drop]['updates'])
        # 相同事件、操作和结果是同一事实，稳定合并证据；不同生命周期事实一项不丢。
        updates = {}
        for update in data['threads'][keep]['updates']:
            address = (update['event_ref'], update['op'], update['resolution'])
            if address not in updates:
                updates[address] = update
            else:
                entry = updates[address]
                entry['evidence_refs'] = list(dict.fromkeys(entry['evidence_refs'] + update['evidence_refs']))
        data['threads'][keep]['updates'] = list(updates.values())
        data['threads'][keep]['updates'].sort(key=lambda update: (
            data['events'][update['event_ref']]['chapter_ordinal'], data['events'][update['event_ref']]['order']))
        del data['threads'][drop]
    for key in sorted({root(thread_map, drop) for drop in thread_map}):
        opened, terminal = False, False
        for update in data['threads'][key]['updates']:
            if (update['op'] == 'open' and opened
                    or update['op'] != 'open' and (not opened or terminal)):
                merge_scope('thread_identity', key, '同一悬念合并后出现重复开启或终止后继续的不同事实')
            if update['op'] == 'open':
                opened = True
            if update['op'] in ('resolve', 'abandon'):
                terminal = True
    for event in data['events'].values():
        event['participants'] = list(dict.fromkeys(root(entity_map, ref) for ref in event['participants']))
    for prop in data['propositions'].values():
        prop['entity_refs'] = list(dict.fromkeys(root(entity_map, ref) for ref in prop['entity_refs']))
    for update in data['knowledge_updates']:
        update['holder'] = root(entity_map, update['holder'])
        update['proposition_ref'] = root(prop_map, update['proposition_ref'])
    for exposure in data['exposures']:
        exposure['proposition_ref'] = root(prop_map, exposure['proposition_ref'])
    for slot in data['state_slots'].values():
        slot['subjects'] = list(dict.fromkeys(root(entity_map, ref) for ref in slot['subjects']))
    addresses = set()
    for slot in data['state_slots'].values():
        address = (tuple(slot['subjects']), slot['aspect'], slot['focus'])
        if address in addresses:
            affected = next(subject for subject in slot['subjects']
                if any(root(entity_map, drop) == subject for drop in entity_map))
            merge_scope('entity_identity', affected, '同一人物合并后出现两个相同具体状态槽，须明确其状态修订')
        addresses.add(address)
    for update in data['state_updates']:
        if update['proposition_ref']:
            update['proposition_ref'] = root(prop_map, update['proposition_ref'])
    data['expression']['focalizers'] = list(dict.fromkeys(root(entity_map, ref)
                                                       for ref in data['expression']['focalizers']))
    return data


def compile_source_story(*, index: SourceIndex, chapters: Mapping[int, ChapterObservation],
                         book: BookObservation, names: NamesObservation,
                         associate: AssociateObservation | None = None) -> SourceStory:
    _check_chapters(index, chapters)
    if book.story.source_identity != index.source_identity or book.chapter_digests != {
            ordinal: _digest(value) for ordinal, value in chapters.items()}:
        _fail('input_changed', 'compile', ('book',), '全书综合绑定的源或章观察已变化，不得静默采用')
    if book.unresolved:
        if associate is None:
            _fail('association_required', 'associate', ('unresolved',), '存在明确未决，必须经过对应窄判定')
        association = compile_associate(associate, book=book, index=index)
        data = _apply_associate(book, association)
    else:
        if associate is not None:
            compile_associate(associate, book=book, index=index)
        if associate is not None and associate.verdicts:
            _fail('stray_association', 'associate', ('verdicts',), '没有候选时不得携带关联答案')
        data = book.story.model_dump(mode='json')
    names = _parse(NamesObservation, names, 'names')
    # names 消费 same 执行后的实体闭集；没有第二套人物真相或重做整书综合。
    expected = {key for key, entity in data['entities'].items() if entity['kind'] == 'person'}
    if set(names.people) != expected:
        _fail('name_coverage', 'names', ('people',), '姓名结果不覆盖当前全书人物闭集')
    for key, entity in data['entities'].items():
        if entity['kind'] == 'person':
            if key not in names.people:
                _fail('name_missing', 'names', ('people', key), '人物缺姓名结构')
            name = names.people[key]
            surfaces = {surface['literal'] for surface in entity['surfaces']}
            if set(name.surface_forms) != surfaces:
                _fail('name_surface_changed', 'names', ('people', key), '同一性合并改变称谓闭集，须窄重建该人物姓名结构')
            entity['name'] = name.model_dump(mode='json')
    for number, conflict in enumerate(data['conflicts']):
        if conflict['status'] == 'unresolved':
            _fail('book_unresolved', 'book', ('conflicts', number),
                  '模型未决须回到本综合条目修订，不能伪装成原书不明直接发布')
    story = _parse(SourceStory, data, 'compile')
    issues = validate_source_story(story, index=index)
    if issues:
        raise StoryCompileError(issues)
    return story


def _revision_value(payload: Any, path: Sequence[str | int], owner: str) -> Any:
    for component in path:
        if (isinstance(payload, dict) and component in payload
                or isinstance(payload, (list, tuple)) and type(component) is int
                and 0 <= component < len(payload)):
            payload = payload[component]
        else:
            _fail('source_revision_path', owner, tuple(path), '修订路径不属于已验证输入的真实条目')
    return payload


def resolve_source_revision(source: SourceStory | Mapping[str, Any], *, source_ref: str,
                            path: Sequence[str | int], operation: str,
                            book: BookObservation, book_wire: Any,
                            chapters: Mapping[int, ChapterObservation],
                            chapter_wires: Mapping[int, Any], index: SourceIndex,
                            names_wire: Any = None,
                            associate: AssociateObservation | None = None) -> SourceRevisionAddress:
    """正式事实地址→实际已购wire地址；不读盘、不按证据章归因、不猜编号。

    每次先重编译冻结材料证明其确为当前SourceStory的输入。姓名部件、称谓分段
    只映射真实names wire；编译alias_ref、关联合并复合值或缺原wire均显式scope不足。
    """
    source = _parse(SourceStory, source, 'compile')
    try:
        address = source_revision_owner(source, source_ref, tuple(path), operation)
    except SourceRevisionScopeError as exc:
        _fail(exc.code, 'compile', exc.path, str(exc))
    if compile_book(book_wire, index=index, chapters=chapters) != book:
        _fail('input_changed', 'compile', ('book',), '原book wire不是当前全书观察的真实输入')
    expected = _name_story(book, associate, index=index).model_dump(mode='json')
    actual = source.model_dump(mode='json')
    for entity in actual['entities'].values():
        entity['name'] = None
    if actual != expected:
        _fail('input_changed', 'compile', address.canonical_path,
              '正式事实与当前book/associate材料不一致，不能对失效来源授权窄修')
    domain, relative = address.canonical_path[0], address.path
    raw_book = _parse(BookWire, book_wire, 'book').model_dump(mode='json')
    if address.owner == 'names':
        if names_wire is None:
            _fail('source_revision_scope', 'names', address.canonical_path,
                  '姓名修订必须提供实际已购names wire，不能从正式typed姓名字段反猜原语义输入')
        names = compile_names(names_wire, book=book, associate=associate, index=index)
        if any(names.people[key] != entity.name for key, entity in source.entities.items()
               if entity.kind == 'person'):
            _fail('input_changed', 'names', address.canonical_path, '原names wire不对应当前正式姓名结构')
        raw_names = _parse(NamesWire, names_wire, 'names').model_dump(mode='json')
        name_path = tuple(relative[1:])
        if name_path[0] == 'aliases' and len(name_path) > 1:
            literal = source.entities[source_ref].name.aliases[name_path[1]]
            aliases = raw_names['people'][source_ref]['aliases']
            name_path = ('aliases', aliases.index(literal), *name_path[2:])
        if name_path[0] == 'surface_forms':
            name_path = ('surfaces', *name_path[1:])
        raw_path = ('people', source_ref, *name_path)
        _revision_value(raw_names, raw_path, 'names')
        return address.model_copy(update={'path': raw_path})
    identity_kind = {'entities': 'entity_identity', 'propositions': 'proposition_identity',
                     'threads': 'thread_identity'}.get(domain)
    candidates = {candidate.key: candidate for candidate in book.unresolved}
    if identity_kind and associate is not None and any(
            verdict.kind == 'identity' and verdict.verdict == 'same'
            and candidates[verdict.key].kind == identity_kind
            and source_ref in (candidates[verdict.key].left, verdict.right)
            for verdict in associate.verdicts):
        _fail('source_revision_scope', address.owner, address.canonical_path,
              '该正式记录含同一性合并，不能将复合产物只回派某一个原book记录')
    original = book.story.model_dump(mode='json')
    before = _revision_value(original, address.canonical_path, address.owner)
    after = _revision_value(actual, address.canonical_path, address.owner)
    if before != after:
        _fail('source_revision_scope', address.owner, address.canonical_path,
              '该字段经过关联合并或时间窄判，原book条目不再是唯一writer；须显式扩大scope')
    if domain in SOURCE_REVISION_SELECTORS:
        _revision_value(raw_book, address.canonical_path, 'book')
        return address.model_copy(update={'path': address.canonical_path})
    if address.owner.startswith('ch'):
        source_event = next(key for key, formal in book.event_handles.items() if formal == source_ref)
        chapter_ordinal = source.events[source_ref].chapter_ordinal
        if chapter_ordinal not in chapter_wires:
            _fail('source_revision_scope', address.owner, address.canonical_path,
                  '缺少对应章已购wire，不能从正式句柄反猜原语义键')
        raw_path = chapter_wire_path(chapters[chapter_ordinal], ('events', source_event, *relative),
                                     wire=chapter_wires[chapter_ordinal], index=index)
    else:
        maps = {'entities': book.entity_handles, 'events': book.event_handles,
                'propositions': book.proposition_handles, 'state_slots': book.slot_handles,
                'threads': book.thread_handles}
        mapping = maps.get(domain)
        if mapping is None:
            prefix = {'knowledge_updates': 'ku', 'state_updates': 'su', 'world_rules': 'wr'}[domain]
            mapping = _stable_handles(raw_book[domain], index=index, domain=f'book:{domain}', prefix=prefix)
        keys = [key for key, formal in mapping.items() if formal == source_ref]
        if len(keys) != 1:
            _fail('source_revision_scope', 'book', address.canonical_path,
                  '正式记录没有唯一原book语义键，不按hash或编号猜位置')
        raw_path = (domain, keys[0], *relative)
        _revision_value(raw_book, raw_path, 'book')
    return address.model_copy(update={'path': raw_path})


def validate_source_story(story: Any, *, index: SourceIndex | None = None) -> tuple[StoryIssue, ...]:
    """只报告机械缺陷；返回空集合不代表语义、完整性或文学质量已通过。"""
    try:
        model = _parse(SourceStory, story, 'compile')
        if index is None:
            evidence = model.evidence
            for path, refs in _evidence_refs(model):
                for ref in refs:
                    if ref not in evidence:
                        _fail('unknown_evidence', 'compile', path, f'正式证据表缺少 {ref}')
        else:
            if model.source_identity != index.source_identity:
                _fail('source_identity', 'compile', ('source_identity',), '正式源身份与源索引不一致')
            _check_evidence(model, index, 'compile')
            for ref, segment in model.evidence.items():
                if ref not in index.evidence or segment != index.evidence[ref]:
                    _fail('evidence_changed', 'compile', ('evidence', ref), '正式证据必须逐字等于源索引')
        for key, entity in model.entities.items():
            if entity.kind == 'person' and entity.name is None:
                _fail('name_missing', 'names', ('entities', key), '正式人物必须具备显式姓名结构（纯称谓部件可 null）')
        return ()
    except StoryCompileError as exc:
        return exc.issues


def current_state(story: SourceStory, *, through_event: str | None = None) -> dict[str, str | None]:
    """仅折叠确定作用于当前的实际状态；回忆、预期和未知持续期不冒充当前值。"""
    ranks = {ref: (event.chapter_ordinal, event.order) for ref, event in story.events.items()}
    ranks['story_start'] = (0, 0)
    if through_event is not None and through_event not in story.events:
        raise ValueError('through_event 不在正式事件闭集')
    boundary = ranks[through_event] if through_event else max(ranks.values())
    updates = sorted(enumerate(story.state_updates), key=lambda row: (ranks[row[1].at], row[0]))
    result = {}
    for _number, update in updates:
        if ranks[update.at] > boundary or update.effective_time != 'current' or update.modality != 'actual':
            continue
        if update.persistence != 'stable_until_changed':
            continue  # 临场与未知时长不跨场景外推；其事实仍完整保存在正式更新中。
        if update.op == 'unset':
            result.pop(update.slot_ref, None)
        else:
            result[update.slot_ref] = update.value if update.op == 'set' else None
    return result


__all__ = [
    'StoryCompileError', 'SourceObservationIssueRequired', 'build_source_index', 'chapter_prompt',
    'compile_chapter', 'chapter_wire_path',
    'resolve_source_revision',
    'book_prompt', 'compile_book', 'book_repair_scope', 'apply_book_repair', 'book_repair_prompt',
    'names_prompt', 'compile_names', 'associate_prompt',
    'compile_associate', 'compile_source_story', 'validate_source_story', 'current_state',
]
