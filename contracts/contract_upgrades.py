"""同根升级闭集；登记的是已证明的消费边界，不是通用版本迁移。

rc22→rc23 不转换旧 Hook 语义。旧组织与期待无法确定性补出，必须重建
（emphasis/transition/reader_expectation/local_focus 是设计决定，机械补出
违反 I-21——故本边**不登记**换算器，走重建）；
正文真源形状未变，免费重编译后以逐章写手／验证身份决定恢复范围。

2026-10-11 裁决：全部 LLM 节点正式执行版本升版均恢复基础可用额度，
不再以产物归档或本表 refund_nodes 为适用条件。本表只登记产物升级时调用
统一同步的范围；历史返还记录只读，节点同版本重放不能再次恢复额度。

rc23→rc24 只改 HookContext 形状（篇幅包络三字段）与请求字节：PresentationGraph
（rc23）与 design wire v3 不变，已发布 Hook 选择继续有效；旧上下文按契约表
漂移拒读，免费重编译即得 rc24 形状。stage5.hook 的请求身份随交付要求变化，
旧稿不沿用（付费重写，轮次按节点升版恢复）；正文与姓名映射原样保留。

rc24→rc25 引入逐次身份声明。旧 Design 与正文不能机械补出 name/text 语义，
因此从 body_design 重建并归档其消费链；保留原始上游、Preserve、姓名映射及
已购账。Names v4 的集中只读适配不补造缺失称谓证据。
"""
from typing import Final, TypedDict


class ContractUpgradeSpec(TypedDict):
    id: str
    source_identity: str
    target_identity: str
    source_version: str
    target_version: str
    rebuild_nodes: tuple[str, ...]
    archive_patterns: tuple[str, ...]
    #: 本产物升级调用版本同步的范围；全部 LLM 升版另由统一入口覆盖。
    refund_nodes: tuple[str, ...]

HOOK_UPGRADE: Final[ContractUpgradeSpec] = {
    'id': 'hook-rc22-rc23',
    'source_identity': '9a7e1067bbbcca471d237f52ee2df63fc75020ed2166ff0aceb4272b2a51932d',
    'target_identity': '2c833f1a715a4fcee944b5377cd5b12e8d2b7d84f77262f9c9261ec8861682ff',
    'source_version': 'str06-rc22',
    'target_version': 'str06-rc23',
    'rebuild_nodes': (
        'str06.stage4.hook_design', 'str06.stage4.compile',
        'str06.stage5.hook', 'str06.stage5.body', 'str06.stage7.assemble',
    ),
    #: 本产物升级同步的节点；其余 LLM 升版仍由统一版本入口覆盖。
    'refund_nodes': ('str06.stage4.hook_design', 'str06.stage5.hook'),
    # 输出组完整失效；IR 重编译不等于章稿重写。正文／姓名映射原样保留。
    'archive_patterns': (
        'stage4/design/presentation_graph*.json',
        'stage4/compiled/execution_plan*.json',
        'stage4/context/hook/hook*.json',
        'stage4/ir/writer_book_ir*.json',
        'stage4/acceptance/guard/*.json',
        'stage4/acceptance/packet_metrics*.json',
        'stage5/hook/*', 'stage7/final/*', 'runtime/delivery/*',
        'runtime/workflow_status.json',
    ),
}

#: rc24 篇幅包络：design/graph 不失效（形状与 wire 均未变），hook_design 不
#: 重建、不返还；compile 免费重派生 rc24 上下文，hook 付费重写并返还轮次。
HOOK_ENVELOPE_UPGRADE: Final[ContractUpgradeSpec] = {
    'id': 'hook-rc23-rc24',
    'source_identity': '2c833f1a715a4fcee944b5377cd5b12e8d2b7d84f77262f9c9261ec8861682ff',
    'target_identity': '5a61ed782cd59035cee8d386bc03c3ed1dbee998fb204df688d3772b7442056c',
    'source_version': 'str06-rc23',
    'target_version': 'str06-rc24',
    'rebuild_nodes': (
        'str06.stage4.compile', 'str06.stage5.hook', 'str06.stage7.assemble',
    ),
    'refund_nodes': ('str06.stage5.hook',),
    # 设计与正文／姓名映射原样保留；交付面随楔子换稿失效。
    'archive_patterns': (
        'stage4/context/hook/hook*.json',
        'stage5/hook/*', 'stage7/final/*', 'runtime/delivery/*',
        'runtime/workflow_status.json',
    ),
}

IDENTITY_TEXT_UPGRADE: Final[ContractUpgradeSpec] = {
    'id': 'identity-text-rc24-rc25',
    'source_identity': '5a61ed782cd59035cee8d386bc03c3ed1dbee998fb204df688d3772b7442056c',
    'target_identity': '9bb731fa6a2a8198163baafcba1998b905c1120427586223e806e2d6cb7f3900',
    'source_version': 'str06-rc24',
    'target_version': 'str06-rc25',
    'rebuild_nodes': (
        'str06.stage4.body_design', 'str06.stage4.hook_design', 'str06.stage4.compile',
        'str06.stage5.hook', 'str06.stage5.body', 'str06.stage7.assemble',
    ),
    'refund_nodes': ('str06.stage4.body_design', 'str06.stage4.hook_design',
                     'str06.stage5.hook', 'str06.stage5.body'),
    # 旧正文没有逐次身份声明，程序不能从字面猜补；保留原始上游、已购答和成本。
    'archive_patterns': (
        'stage4/design/*', 'stage4/grounding/semantic_binding*.json',
        'stage4/compiled/*', 'stage4/ir/*', 'stage4/context/hook/*',
        'stage4/acceptance/*', 'stage4/acceptance/guard/*',
        'stage5/body/*', 'stage5/acceptance/body/*', 'stage5/hook/*',
        'stage7/final/*', 'runtime/delivery/*', 'runtime/workflow_status.json',
    ),
}

REGISTERED_UPGRADES: Final = (HOOK_UPGRADE, HOOK_ENVELOPE_UPGRADE, IDENTITY_TEXT_UPGRADE)
