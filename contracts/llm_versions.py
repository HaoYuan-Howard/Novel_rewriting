"""LLM 节点执行版本的唯一投影；输入、模型配置与 Git 提交不属于版本轴。

预算只按正式协议、策略、渲染与判据的显式版本升版恢复。契约依赖单向流入
运行时，不读取请求正文猜版本，不因一次返修、换模型或换书重新发放额度。
"""
from __future__ import annotations

import re

from contracts.invariants import NODES
from contracts.artifacts import STR06_ARTIFACT_RC_VERSIONS
from contracts.schema import design, hook, naming, observation, receipt, workflow


def _number(version: str) -> int:
    match = re.search(r'(?:-v|-rc)([1-9][0-9]*)$', version)
    if match is None:
        raise ValueError(f'LLM 节点版本必须有显式正整数版本：{version}')
    return int(match[1])


def llm_node_versions(node: str) -> dict[str, int]:
    """每个付费节点的版本分量；任一分量升版即覆盖该节点全部 scope。"""
    versions = {
        'str06.stage2.book': {
            'canonical': _number(observation.STAGE2_BOOK_PROTOCOL),
            'wire': _number(observation.STAGE2_BOOK_WIRE_PROTOCOL)},
        'str06.stage2.chapters': {
            'canonical': _number(observation.STAGE2_CHAPTERS_PROTOCOL),
            'wire': _number(observation.STAGE2_CHAPTERS_WIRE_PROTOCOL)},
        'str06.stage2.names': {
            'canonical': _number(observation.STAGE2_NAMES_PROTOCOL),
            'wire': _number(observation.STAGE2_NAMES_WIRE_PROTOCOL),
            'variants_wire': _number(observation.STAGE2_NAME_VARIANTS_WIRE_PROTOCOL)},
        'str06.stage2.associate': {
            'wire': _number(observation.STAGE2_ASSOCIATE_PROTOCOL),
            'cross_cause_wire': _number(observation.STAGE2_CROSS_CAUSE_WIRE_PROTOCOL)},
        'str06.stage4.preserve': {
            'wire': _number(workflow.STAGE4_PRESERVE_PROTOCOL),
            'policy': workflow.STAGE4_PRESERVE_POLICY_VERSION},
        'str06.stage4.body_design': {
            'canonical': _number(workflow.STAGE4_DESIGN_PROTOCOL),
            'wire': _number(design.STAGE4_DESIGN_WIRE_PROTOCOL)},
        'str06.stage4.hook_design': {
            'wire': _number(workflow.STAGE4_HOOK_DESIGN_PROTOCOL)},
        'str06.stage5.body': {
            'protocol': _number(receipt.WRITER_PROTOCOL_VERSION),
            'renderer': _number(receipt.RENDERER_VERSION),
            'validator': _number(receipt.BODY_VALIDATOR_VERSION)},
        'str06.stage5.hook': {
            'context': _number(hook.HookContext.model_fields['contract_version'].default),
            'renderer': _number(receipt.RENDERER_VERSION),
            'validator': _number(receipt.HOOK_VALIDATOR_VERSION)},
        'str06.stage7.naming': {
            'canonical': _number(naming.NameMapV5.model_fields['contract_version'].default),
            'validator': _number(naming.NAMING_VALIDATOR_VERSION)},
    }
    paid = {key for key, spec in NODES.items() if spec.paid}
    if set(versions) != paid:
        raise ValueError('全部 LLM 节点必须登记执行版本，确定性节点不得发放模型额度')
    if node not in versions:
        raise ValueError(f'节点不是现役 LLM 节点：{node}')
    return {**versions[node], **{
        f'artifact:{path}': _number(STR06_ARTIFACT_RC_VERSIONS[path])
        for path in NODES[node].produces if path in STR06_ARTIFACT_RC_VERSIONS}}
