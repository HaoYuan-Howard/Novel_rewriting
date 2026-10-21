"""story-v1 三层链的机械判据目录。

目录不实现第二套判定。schema、纯 compiler、正文/补丁 validator、repository
发布校验和最终装配才是执行入口。tests/hard_cases.py 对每条 HARD 真跑红绿样本。
付费修订由当前请求协议与预算治理，不从 severity 推导许可或语义覆盖率。
"""
from __future__ import annotations

from contracts.rules import REGISTRY, Rule


def _hard(rule: str, stage: str, item: str, requirement: str) -> None:
    REGISTRY.register(Rule(rule=rule, stage=stage, item=item, sev='hard',
                           who_checks='HARD', requirement=requirement, owner=stage))


def _unver(rule: str, stage: str, item: str, requirement: str, *, disclosed=False) -> None:
    REGISTRY.register(Rule(rule=rule, stage=stage, item=item, sev='unver',
        who_checks='INFO', requirement=requirement, owner=stage,
        disclosable_unver=disclosed))


_hard('story_source_contract', 'stage2',
      'SourceStory 当前版本、闭合字段与非空章集合；缺失、旧版与未知字段拒绝', 'R-2.1')
_hard('story_source_identity_evidence', 'stage2',
      '源身份与逐字证据必须匹配当前 SourceIndex', 'R-2.9')
_hard('story_source_references', 'stage2',
      '实体/事件/命题/认知引用闭合，误信与世界真值、读者确认分开', 'R-2.12')
_hard('story_source_state_address', 'stage2',
      '状态地址按主体、方面与具体焦点唯一，拒绝重址和悬空引用', 'R-2.14')
_hard('story_source_threads', 'stage2',
      '整合后线程有序开合，终止后不得继续操作', 'R-2.11')
_hard('story_names_structure', 'stage2',
      '名称结构覆盖全人物实体，名称组成与原文字面可机械还原', 'R-3.1')
_unver('source_semantics_unver', 'stage2',
       '原作理解的完整性、因果归因与命题粒度未经语义验收', 'R-3.3')

_hard('story_target_chapters', 'stage4',
      '事件恰分配一次且章集合等于源章集合，局部展开不得制造跨章事实', 'R-5.2')
_hard('story_target_realization', 'stage4',
      '必保约束有目标承载，依赖有序且已声明因果与时间图无环', 'R-5.3')
_hard('story_target_knowledge', 'stage4',
      '知情读取按持有者与模式成立，确知不得指向已否定事实', 'R-5.12')
_hard('story_packet_projection', 'stage4',
      '章/Hook 包只投影授权动作、知情与显示名，六分区形状闭合', 'R-6.1')
_hard('story_artifact_seal', 'stage4',
      '正式产物有覆盖当前字节的封存证明，损坏或未封存拒读', 'R-6.2')
_hard('story_target_naming', 'stage4',
      '目标命名覆盖源人物，名称组成、保留理由与家族对应关系闭合', 'R-10.1')

_hard('story_prose_complete', 'stage5',
      '回答协议、finish_reason、非空与篇幅带通过机械校验', 'R-7.1')
_hard('story_prose_receipt', 'stage5',
      '章回执绑定真实调用来源、章包、前章短尾、正文及修订链摘要', 'R-7.4')
_hard('story_opening_patch_scope', 'stage5',
      '窄修仅替换前八个非空自然段，句柄闭合且未授权字节不变', 'R-7.8')
_hard('story_hook_receipt', 'stage5',
      'Hook 正文与回执绑定当前 HookPacket、请求与正文摘要', 'R-8.1')
_unver('body_semantics_unver', 'stage5',
       '逻辑、人物转变、场景衔接、兑现与文学质量未经语义验收', 'R-7.6', disclosed=True)

_hard('story_delivery_chapters', 'stage7',
      '交付按源阅读序核对完整章包、正文与回执集合', 'R-10.3')
_hard('story_delivery_bytes', 'stage7',
      'TXT/manifest 可从当前接受产物免费逐字重建并核对摘要', 'R-10.4')
_unver('naming_quality_unver', 'stage7',
       '目标名称的阅读质量与正文使用效果未经语义验收', 'R-10.6', disclosed=True)
