# -*- coding: utf-8 -*-
"""必要全局知识的唯一请求说明；静态人物由独立 producer 提供。"""
from __future__ import annotations

from contracts.invariants import KNOWLEDGE_SUBJECT_KINDS, RELATION_VISIBILITY_KINDS, WORLD_RULE_KINDS
from novel.llm.prompts import fill


SYSTEM = fill("""通读源文案与全书证据，提取原文明示且影响事实、因果、状态或披露的必要知识。
只陈述原作事实，不作文学分析、改写建议、情节分期、高潮或结局解读，不限制事实数量。
源文未明确支持的内容不猜补；无法确定开篇范围或文案事实时使用相应 unresolved 分支。

【人物输入】人物引用已由 stage2.characters 冻结，canonical/surfaces/role 只用于识别原文。
不得新增人物 ID、改静态身份、返回人物表或重复称谓材料。人物缺失、误合并或身份错误
应由人物 producer 修复，不可通过改知识事实掩盖。所有人物引用只能取本次输入的 char_id。

【entities】
character_states 用数组返回：每项 char_id、fate、fate_evidence。只列原文明示、影响必要
动机/因果/结局披露的经历或状态；未列入不表示人物没有经历，不为每个人补传记。
relations 用 rel_01…标识，subject_id/object_id 均是登记人物；predicate 陈述有向关系。
predicate_evidence 支持关系；valid_from_chapter 与 valid_through_chapter 是世界内关系
成立的首章和末章（末章含，持续至结尾填 null）。读者知情单独用 visibility：public +
from_chapter，或 via_secret + 当前 secret_id；不能用成立章猜揭示章。

【canon】
setting 给时代背景与世界观及各自证据。world_rules 只列结构性世界设定、能力体系、
法律后果或核心社会约束，kind 从 $RULE_KINDS 选。人物个案和普通剧情不作世界规则。
规则正文只在 versions：按 effective_from_chapter 排列，每版写 desc 与 desc_evidence。
写清触发条件、运作、表现、代价与后果；不以具体人物举例，不重复一份根部 desc。
secrets 每项 secret_id、fact、fact_evidence、knowledge_events、setup、first_reveal_chapter。
fact 是被隐瞒的原子真相；揭示后会改变对前文事件或动机的理解，不能用揭示场面代替真相。
逐一检查暗中谋划、伪装动机、隐秘关系和事后点明的安排；先收全秘密，再找埋点。
setup 是 cold，或 foreshadowed + chapters（仅取读者首次揭示前确有埋点的章）。
first_reveal_chapter 是第一次向读者点破的阅读序章号；第一章直接明牌的普通背景不列秘密。
knowledge_events 记录世界内主体最早知情的章及证据；subject 是 character + char_id，
或 group + 原文无名群体 label。同一主体只记一次，不为群体编 ID。
世界内知情、读者揭示、事实发生三者分开，不根据其中一项推测另一项。

【source_blurb】
源文案就是原作者放在正文章前吸引读者的开头钩子。完整读取，facts 用 hook_001…
覆盖可核验主张：supported 给 claim 与正文证据；钩子明确给出而正文未重复的信息
用 hook_supported + claim + hook_evidence:{locator:原文案内≤160字的唯一定位}。
仅钩子支持的信息只用于文案创作，不要求正文补写，不据此新增人物动态、关系、世界规则
或秘密。正文未重复不等于冲突；来源不明、歧义或正文明确矛盾用 unresolved + reason，
不伪造证据或删掉问题，修辞不强作事实。
opening 给从第 1 章连续到 through_chapter 的开篇范围：proposed 须附 rationale 与
终点章 boundary_evidence；不明确用 unresolved + reason，不能默认第 1 章或按篇幅猜测。
范围不是秘密披露授权；必要原文与事实完整性仍由既有 Commander 核验。

正文证据只填本次表内 evidence_candidate_id（e000001…），不抄 locator、章号或 SourceSpan；
仅 hook_supported 的 hook_evidence 填源文案定位，不造第 0 章或第 1 章证据。
代码从同一表回取原文。只通过声明的结构化出口返回 entities/canon/source_blurb。
""")

# legacy transport 的当前知识示例；agent 原生 schema 不重复展开。
# 不从退役的整份 Global 人物模板删字段拼接，知识 producer 自己声明完整响应面。
_EVIDENCE = {'evidence_candidate_id': 'e000001'}
HINT: dict[str, object] = {
    'entities': {
        'character_states': [{'char_id': 'char_001', 'fate': '原文明示的必要动态事实',
                              'fate_evidence': _EVIDENCE}],
        'relations': [
            {'relation_id': 'rel_01', 'subject_id': 'char_001', 'object_id': 'char_002',
             'predicate': '原文明示的有向关系', 'predicate_evidence': _EVIDENCE,
             'visibility': {'kind': RELATION_VISIBILITY_KINDS[0], 'from_chapter': 1},
             'valid_from_chapter': 1, 'valid_through_chapter': None},
            {'relation_id': 'rel_02', 'subject_id': 'char_002', 'object_id': 'char_001',
             'predicate': '另一条有向关系', 'predicate_evidence': _EVIDENCE,
             'visibility': {'kind': RELATION_VISIBILITY_KINDS[1], 'secret_id': 'secret_01'},
             'valid_from_chapter': 2, 'valid_through_chapter': 3},
        ],
    },
    'canon': {
        'setting': {'时代背景': '时代背景', 'era_evidence': _EVIDENCE,
                    '世界观': '世界观', 'worldview_evidence': _EVIDENCE},
        'world_rules': [{'rule_id': 'wr_01', 'kind': WORLD_RULE_KINDS[0],
                         'versions': [{'effective_from_chapter': 1, 'desc': '规则与后果',
                                       'desc_evidence': _EVIDENCE}]}],
        'secrets': [{'secret_id': 'secret_01', 'fact': '改变读者理解的隐瞒真相',
                     'fact_evidence': _EVIDENCE,
                     'knowledge_events': [
                         {'subject': {'kind': KNOWLEDGE_SUBJECT_KINDS[0], 'char_id': 'char_001'},
                          'known_from_chapter': 1, 'evidence': _EVIDENCE},
                         {'subject': {'kind': KNOWLEDGE_SUBJECT_KINDS[1], 'label': '在场士兵'},
                          'known_from_chapter': 2, 'evidence': _EVIDENCE},
                     ],
                     'setup': {'kind': 'foreshadowed', 'chapters': [1]},
                     'first_reveal_chapter': 2}],
    },
    'source_blurb': {
        'facts': [{'kind': 'supported', 'claim_id': 'hook_001',
                   'claim': '源文案中可在正文核验的事实', 'evidence': _EVIDENCE}],
        'opening': {'kind': 'proposed', 'through_chapter': 1,
                    'rationale': '连续开篇为何在此收束', 'boundary_evidence': _EVIDENCE},
    },
}
