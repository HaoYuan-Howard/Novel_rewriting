"""str06_facilities —— F7a-2 候选链测试支撑：候选上下文与付费边次的显式构造。

测试与产品入口共用同一套显式依赖（候选上下文 → repository → 付费边界）；
本模块只集中构造细节，不带业务断言。epoch 每次调用新开（测试不跨调用复用）。

v13 wire 助手（质量改进批次二）：拍的三类转移＋场景边界＋时间模式的确定性
最小构造——各测试文件不各自抄一份 wire 形状（形状变迁时只改一处）。
"""
from __future__ import annotations

from pathlib import Path

from novel.llm.paid_requests import open_candidate_epoch
from novel import prov
from novel.llm.ledger import CostLedger
from novel.llm.paid_requests import CandidateRunContext


def candidate_epoch():
    return open_candidate_epoch(code_version=prov.code_version(), task_id='task-1',
                                session_run_id='run-1', created_at=1759000000)


def make_context(root: Path, *, ledger: CostLedger, node: str = 'str06.stage2.book',
                 configured_model: str = 'fake', readonly: bool = False) -> CandidateRunContext:
    """默认节点是 Stage2 书级观察 owner（B2 五节点重建后 `str06.stage2.source`
    已退役；断言具体节点账的测试显式传 `node=`）。"""
    return CandidateRunContext(
        root=root, node=node, epoch=candidate_epoch(), code=prov.code_version(),
        ledger=ledger, root_sha1='a' * 40,
        configured_model=configured_model, readonly=readonly)


def v13_effect(target: dict, key: str, value, *, op: str = 'set',
               modality: str = 'actual',
               reader_visible: bool = True,
               basis: tuple[dict, ...] | None = None) -> dict:
    """v13 effect（R10a a1 起带 modality 与逐字 basis；CS-S 1b 起无
    scope／disclosure／proposition——作用域读时按登记表派生）。basis 缺省给
    e01_01 一条（引用方保证段文本含该引文）。"""
    return {'target': target, 'key': key, 'op': op, 'value': value,
            'modality': modality,
            'basis': list(basis) if basis is not None
            else [{'segment': 'e01_01', 'quote': '夫君藏进床底'}],
            'reader_visible': reader_visible}


def v13_beat(beat_id: str, event: str, *, participants: tuple[str, ...] = (),
             source_refs: tuple[str, ...] = ('e01_01',), effects: tuple = (),
             cognition: tuple = (), loops: tuple = (), boundary: str = 'new',
             temporal: str = 'single') -> dict:
    """v13 拍：三类转移＋场景边界＋时间模式（默认全空转移的最小拍）。"""
    return {'beat_id': beat_id, 'event': event, 'participants': list(participants),
            'source_refs': list(source_refs), 'scene_boundary': boundary,
            'temporal_mode': temporal, 'effects': list(effects),
            'cognition': list(cognition), 'loops': list(loops)}


def v13_chapter(*beats: dict, causal_edges: tuple = ()) -> dict:
    """v13 章 wire：拍列表＋因果边。"""
    return {'beats': list(beats), 'causal_edges': list(causal_edges)}


def names_v6_wire(structure: dict) -> dict:
    """canonical NameStructure dump → 现役结构 wire（历史函数名仅供测试夹具）：
    剥 people 的 surface_forms（构词由程序派生）与顶层 resolutions（同一性
    走窄调用）；form_overrides 默认空（派生结果应与原 surface_forms 一致——
    不一致的夹具会在断言处暴露）。"""
    people = {}
    for slug, person in (structure.get('people') or {}).items():
        people[slug] = {key: value for key, value in person.items()
                        if key != 'surface_forms'}
        people[slug]['additional_surfaces'] = []
    from contracts.schema.observation import STAGE2_NAMES_WIRE_PROTOCOL
    return {'protocol_version': STAGE2_NAMES_WIRE_PROTOCOL,
            'families': structure.get('families') or {},
            'people': people}


def name_map_of(characters) -> object:
    """命名前移批（rc26）测试替身：对给定 Characters 的**全部保留** NameMapV6。

    覆盖应改名集合（name-bearing 称谓面／化名／given 非空的人物）并逐条给
    keep_reason——`compile_name_map` 的闭合核对因此满足。不关心目标姓名的
    测试用最简形态；关心改名的测试自行构造 entries。
    """
    from contracts.schema.naming import NameAssignment, NameMapV6
    structure = getattr(characters, 'name_structure', None)
    entries = []
    if structure is not None:
        for slug, person in structure.people.items():
            bearing = any(form is not None for form in person.surface_forms.values())
            if not (bearing or person.aliases or person.given):
                continue          # 纯称谓人物不进应改名集合
            entries.append(NameAssignment(
                char_ref=slug, name=None, aliases={},
                keep_reason='测试替身：全部保留'))
    if not entries:
        # 无名称结构或无人需要改名：至少一条显式保留，满足 min_length=1。
        slug = next(iter(characters.characters))
        entries.append(NameAssignment(
            char_ref=slug, name=None, aliases={}, keep_reason='测试替身：全部保留'))
    return NameMapV6(entries=tuple(entries))


def hook_naming_map() -> object:
    """hook 夹具 naming wire 的等价 NameMapV6（yun_niang→柳婉，fu_jun→顾）。

    与 `tests/test_str06_hook._full_chain_wires()['naming']` 同一批改名事实；
    只读复检／自闸测试用它得到**真实旧名闭集**（全部保留替身没有旧名可拦）。
    """
    from contracts.schema.naming import NameAssignment, NameMapV6
    return NameMapV6(entries=(
        NameAssignment(char_ref='fu_jun', name={'family': '顾', 'given': None},
                       aliases={}, keep_reason=None),
        NameAssignment(char_ref='yun_niang', name={'family': '柳', 'given': '婉'},
                       aliases={}, keep_reason=None),
    ))


def anchor_stub(handle: str = 's0_01') -> tuple:
    """R10a a2 fixture：手工构造 checkpoint 的最小 anchors（不经 assemble_preserve
    的下游测试统一引用；正式 anchors 由 assemble 从投影句柄类型化拷贝）。"""
    from contracts.schema.grounding import ObligationAnchor
    return (ObligationAnchor(handle=handle, source_kind='initial_state',
                             address='char:stub.stub', value='stub',
                             quotes=('依据',)),)


# ─── B1 付费边界测试接线（与 `novel/stages/chain.py::execute` 的真实闭包同形） ───

def json_ask(session, *, model='fake', max_tokens=1000):
    """ChainContext.ask 形状的 JSON 付费闭包：ask_json 接受循环＋显式引用。

    ``review_targets``（CS-H H1b）：免费判定要求窄复核的目标，原样转交
    `ask_json`（与 `execute` 的生产闭包同一通道）。``baseline``／``narrow``
    （CS-H H2）：带基底起步与窄请求组装，同样原样转交。
    """
    from novel.llm.paid_requests import ask_json
    from novel.llm.transport import ChatRequest
    from novel.stages.common import SYS, semantic_revision_rules

    def ask(owner, prompt, validate, patch=None, review_targets=None,
            baseline=None, narrow=None, downstream_preview=None):
        request = ChatRequest(model=model, system=SYS, user=prompt,
                              reasoning_effort='high', thinking=True,
                              json_mode=True, max_tokens=max_tokens)
        return ask_json(session, owner, request, validate, patch=patch,
                        revision_rules=semantic_revision_rules(owner),
                        review_targets=review_targets, baseline=baseline,
                        narrow=narrow)
    return ask


def text_ask(session, *, system='楔子写手', model='fake', max_tokens=1000):
    """ChainContext.ask_text 形状的文本付费闭包（空回答计失败 attempt 留证）。"""
    from novel.llm.paid_requests import ask_text_once
    from novel.llm.transport import ChatRequest

    def ask_text(owner, prompt, validate=None):
        request = ChatRequest(model=model, system=system, user=prompt,
                              reasoning_effort='high', thinking=True,
                              json_mode=False, max_tokens=max_tokens)
        text, errors, answer = ask_text_once(session, owner, request, validate)
        return text, errors, answer.ref
    return ask_text


def body_ask(session, *, model='fake', max_tokens=4000):
    """ChainContext.ask_body 形状的正文付费闭包（AnsweredText 带显式引用）。"""
    from novel.llm.paid_requests import ask_text_once
    from novel.llm.transport import ChatRequest
    from novel.stages.stage5.body import AnsweredText

    def ask_body(owner, prompt, validate=None, system=None):
        request = ChatRequest(model=model, system=system or '正文写手', user=prompt,
                              reasoning_effort='high', thinking=True,
                              json_mode=False, max_tokens=max_tokens)
        text, _errors, answer = ask_text_once(session, owner, request, validate)
        return AnsweredText(text=text, finish_reason=answer.finish_reason,
                            identity=answer.request_sha256, cached=answer.cached,
                            ref=answer.ref)
    return ask_body


def synthetic_ref(owner: str, prompt: str = '', model: str = 'fake',
                  request_sha256: str = 'wire-adoption'):
    """不落 Runtime 的合成显式引用（wire 假 ask 与 provenance 断言用）。"""
    from novel.llm.paid_requests import AdoptionRef
    return AdoptionRef(owner=owner, model=model, prompts={'user': prompt},
                       request_sha256=request_sha256)


def carry_forward_ask(session, *, model='fake', max_tokens=1000):
    """ChainContext.carry_forward 形状的免费沿用闭包（与 `execute` 的生产实现同形）。

    判定已由 `run_body_design` 的纯函数完成；本闭包只做采用凭证：按当前请求身份
    核验原 attempt（`session.adopt_carried_forward`）＋落 carried_forward 修订记录
    （V2）＋返回候选 wire 与指向原 attempt 的采用引用。
    """
    from novel.llm.transport import ChatRequest
    from novel.stages.chain import carry_forward_candidate
    from novel.stages.common import SYS

    def carry_forward(owner, prompt, decision):
        request = ChatRequest(model=model, system=SYS, user=prompt,
                              reasoning_effort='high', thinking=True,
                              json_mode=True, max_tokens=max_tokens)
        return carry_forward_candidate(session, session.repository, owner, request, decision)

    return carry_forward


def republish_checkpoint(repository, mutate, *, owner='preserve'):
    """改一处已发布 Preserve checkpoint 并重新发布（换 seal＝上游新版本）。

    测试用：直接改 canonical 模型再走正式 publish——不碰盘上旧证据、不伪造
    Preserve 购买；`mutate(checkpoint)` 返回新 checkpoint。发布前先清来源边窗口
    并按 Preserve 的真实输入（characters＋blueprints）重读，避免把测试读取的
    无关产物记成 checkpoint 的上游（ freshness 会据此误判过期）。
    """
    from novel.store import PublishItem
    context = getattr(repository, 'context', None)
    if context is not None:
        context.begin_run()
    checkpoint = repository.read_model('runtime/preserve_checkpoint.json')
    repository.read_model('stage2/characters/characters.json')
    repository.read_model('stage2/blueprints/chapter_blueprints.json')
    updated = mutate(checkpoint)
    repository.publish((PublishItem(rel='runtime/preserve_checkpoint.json', value=updated,
                                    owners=(owner,),
                                    refs=(synthetic_ref(owner, '测试上游修订'),)),))
    return updated


# ─── W1 选章：正文 runner 的测试替身 ───

def complete_associate_fixture(book, chapters, observation):
    """为其他 compiler 反例补齐无关候选的明确否决；保留传入的全部判定及缺陷。

    只用于不测试候选生成/完整性的最小基座：无关身份答 distinct，因果答 none。
    不删除候选外行、不改已有 verdict，不能用于缺项或候选召回本身的测试。
    """
    from contracts.schema.observation import AssociateObservation
    from novel.stages.stage2 import (
        project_association_space, project_identity_candidates, project_cause_candidates,
    )
    wire = observation.model_dump(mode='json')
    space = project_association_space(book, chapters)
    candidates = project_identity_candidates(space, book)
    for kind, fields in (
            ('same_proposition', ('left', 'right')),
            ('loop_pairing', ('open', 'close')),
            ('scene_continuation', ('left', 'right'))):
        rows = wire[kind]
        answered = {(row[fields[0]], row[fields[1]]) for row in rows}
        if kind == 'same_proposition':
            answered |= {(right, left) for left, right in answered}
        for candidate in getattr(candidates, kind):
            pair = (getattr(candidate, fields[0]), getattr(candidate, fields[1]))
            if pair not in answered:
                rows.append({fields[0]: pair[0], fields[1]: pair[1], 'verdict': 'distinct'})
    identity = AssociateObservation.model_validate(wire)
    answered_causes = {(row['from_beat'], row['to_beat']) for row in wire['cross_chapter_cause']}
    for candidate in project_cause_candidates(space, chapters, book, identity):
        pair = (candidate.from_beat, candidate.to_beat)
        if pair not in answered_causes:
            wire['cross_chapter_cause'].append({
                'from_beat': pair[0], 'to_beat': pair[1], 'kind': 'none'})
    return AssociateObservation.model_validate(wire)


def stub_body_runner(monkeypatch):
    """把 `str06.stage5.body` 的 runner 换成如实报告零提交的替身（W1/D2）。

    替代已退役的 `body_chapter_limit=0`：那个开关让目标集合退化成空前缀，
    「选了零章」不是一次说得通的执行。替身不进付费路径、不碰正文
    盘面，`body_complete=False` 如实报告；`run_body` 的选章规则、集合恢复与
    逐章留证由 `tests/test_str06_body.py` 自己覆盖。调用方原有的 `ask_body`
    抛错守卫保留——真被调用说明替身没装上。
    """
    from novel.stages import chain

    def stub(_ctx):
        return {'body_chapters': (), 'body_complete': False,
                'length_disclosed_chapters': []}

    monkeypatch.setitem(chain.NODE_RUNNERS, 'str06.stage5.body', stub)


def current_design_wire(coordinates: dict, brief) -> dict:
    """按测试的正式 Brief 将完整坐标投影为当前 wire 短引用，不接受旧协议。

    只构造引用字面，不填语义字段、不修坏选择；闭集外引用保留给真实判据拒绝。
    """
    from copy import deepcopy
    from contracts.schema.design import STAGE4_DESIGN_WIRE_PROTOCOL
    if coordinates.get('protocol_version') != STAGE4_DESIGN_WIRE_PROTOCOL:
        raise ValueError('测试构造器只支持当前 Design 协议')
    aliases = {item.kp_id: item.kp_id[:11] for item in brief.propositions}
    aliases.update({item.kf_id: item.kf_id[:11] for item in brief.fact_locks})
    result = deepcopy(coordinates)

    def project(value):
        if isinstance(value, list):
            for item in value:
                project(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in ('key', 'lock') and isinstance(item, str):
                    value[key] = aliases.get(item, item)
                else:
                    project(item)
    project(result)
    return result
