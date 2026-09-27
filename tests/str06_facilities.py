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


def v13_effect(target: dict, key: str, value, *, op: str = 'set', scope: str = 'book',
               visibility: str = 'on_page', known_by: tuple[str, ...] = (),
               proposition: dict | None = None) -> dict:
    """v13 effect：必填 scope 与 disclosure；concealed 必声明已知者并带命题。"""
    eff: dict = {'target': target, 'key': key, 'op': op, 'value': value, 'scope': scope,
                 'disclosure': {'visibility': visibility}}
    if visibility == 'concealed':
        eff['disclosure']['known_by'] = list(known_by)
        eff['proposition'] = proposition or {
            'ref': f'p_{key}', 'statement': f'{key} 的隐藏事实'}
    elif proposition is not None:
        eff['proposition'] = proposition
    return eff


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


# ─── B1 付费边界测试接线（与 `novel/stages/chain.py::execute` 的真实闭包同形） ───

def json_ask(session, *, model='fake', max_tokens=1000):
    """ChainContext.ask 形状的 JSON 付费闭包：ask_json 接受循环＋显式引用。"""
    from novel.llm.paid_requests import ask_json
    from novel.llm.transport import ChatRequest
    from novel.stages.common import SYS

    def ask(owner, prompt, validate):
        request = ChatRequest(model=model, system=SYS, user=prompt,
                              reasoning_effort='high', thinking=True,
                              json_mode=True, max_tokens=max_tokens)
        return ask_json(session, owner, request, validate)
    return ask


def text_ask(session, *, system='楔子写手', model='fake', max_tokens=1000):
    """ChainContext.ask_text 形状的文本付费闭包（空回答计失败 attempt 留证）。"""
    from novel.llm.paid_requests import ask_text_once
    from novel.llm.transport import ChatRequest

    def ask_text(owner, prompt):
        request = ChatRequest(model=model, system=system, user=prompt,
                              reasoning_effort='high', thinking=True,
                              json_mode=False, max_tokens=max_tokens)
        text, errors, answer = ask_text_once(session, owner, request)
        return text, errors, answer.ref
    return ask_text


def body_ask(session, *, model='fake', max_tokens=4000):
    """ChainContext.ask_body 形状的正文付费闭包（AnsweredText 带显式引用）。"""
    from novel.llm.paid_requests import ask_text_once
    from novel.llm.transport import ChatRequest
    from novel.stages.stage5.body import AnsweredText

    def ask_body(owner, prompt):
        request = ChatRequest(model=model, system='正文写手', user=prompt,
                              reasoning_effort='high', thinking=True,
                              json_mode=False, max_tokens=max_tokens)
        text, _errors, answer = ask_text_once(session, owner, request)
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
