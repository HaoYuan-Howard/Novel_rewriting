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


def make_context(root: Path, *, ledger: CostLedger, node: str = 'str06.stage2.source',
                 configured_model: str = 'fake', readonly: bool = False) -> CandidateRunContext:
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
