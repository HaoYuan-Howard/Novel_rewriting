"""同根升级闭集；登记的是已证明的消费边界，不是通用版本迁移。

rc22→rc23 不转换旧 Hook 语义。旧组织与期待无法确定性补出，必须重建
（emphasis/transition/reader_expectation/local_focus 是设计决定，机械补出
违反 I-21——故本边**不登记**换算器，走重建）；
正文真源形状未变，免费重编译后以逐章写手／验证身份决定恢复范围。

2026-10-10 裁决「升级清账」：登记 `refund_nodes` 的升级在 apply 时，把对应
付费工作单元的可用轮次恢复到基础额度（历史轮与费用证据保留）。rebuild 与
refund 的差集是有意为之：stage5.body 的章产物不随本升级归档，其单元轮次
不作废，不返还。
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
    #: 轮次返还的付费节点闭集（裁决「升级清账」）；确定性/免费节点不进此表。
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
    #: 只返还付费产物被本升级归档的节点；stage5.body 章产物保留，不返还。
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

REGISTERED_UPGRADES: Final = (HOOK_UPGRADE,)
