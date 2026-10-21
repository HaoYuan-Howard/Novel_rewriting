"""当前 LLM 节点的显式执行版本；旧协议不参与额度或缓存决策。"""
from __future__ import annotations

from contracts.invariants import NODES

# 每个分量只随对应公开协议/政策变化升版；换模型、换请求、窄修均不重置额度。
STORY_LLM_VERSIONS = {
    'str06.stage2.chapters': {'wire': 2, 'validator': 2},
    'str06.stage2.book': {'wire': 1, 'validator': 1},
    'str06.stage2.associate': {'wire': 1, 'validator': 1},
    'str06.stage2.names': {'wire': 1, 'validator': 1},
    'str06.stage4.naming': {'wire': 1, 'validator': 1},
    'str06.stage4.preserve': {'wire': 1, 'policy': 1, 'validator': 1},
    'str06.stage4.body_design': {'wire': 1, 'validator': 1},
    'str06.stage4.chapters': {'wire': 1, 'validator': 1},
    'str06.stage4.hook_design': {'wire': 1, 'validator': 1},
    'str06.stage5.body': {'wire': 1, 'renderer': 1, 'validator': 1},
    'str06.stage5.hook': {'wire': 1, 'renderer': 1, 'validator': 1},
}


def llm_node_versions(node: str) -> dict[str, int]:
    paid = {key for key, spec in NODES.items() if spec.paid}
    if set(STORY_LLM_VERSIONS) != paid:
        raise ValueError('全部 LLM 节点必须登记执行版本，确定性节点不得发放模型额度')
    if node not in STORY_LLM_VERSIONS:
        raise ValueError(f'节点不是现役 LLM 节点：{node}')
    return dict(STORY_LLM_VERSIONS[node])
