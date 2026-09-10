#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 Blueprint 当前模型提示与结构提示。

各响应架构由 novel.stages.stage2.contract 整套选择；字段级修复仍只读取被修复拍。
静态人物提示归 characters，必要知识提示归 knowledge_prompts。本模块不保留
已退役的整书 Global 抽取或 role/fate 重划分定义，避免被再次当作现役入口。
枚举统一经 fill 从 contracts.invariants 渲染，不在调用处复制取值表。
"""
from __future__ import annotations

from typing import Any

from contracts.invariants import (
    FACT_ATOM_KINDS, FACT_ATOM_OBLIGATIONS, HINGE_KINDS,
    NARRATIVE_PERSON, RESPONSE_TIME_LAYERS,
)
from novel.llm.prompts import fill


# ---------------------------------------------------------------------------
# 现役边界预传与字段级修复 prompt
# ---------------------------------------------------------------------------

_BOUNDARY_TPL = """\
你会收到一章逐段编号的小说正文。同一次响应必须先做【召回】，再做【压缩】，两张表都交回。

第一张表 `micro_events`（召回层）：
- 逐段核对新的动作、反应、问题、回答、揭示、决定、阻碍、结果、目标、时空或叙述层；
  每出现一项新推进就记一条微事件，不得直接写章节大纲；
- 「甲行动 → 乙反应 → 新信息改变局面 → 人物作决定」先如实记成多条，完整性优先；
- 每条只交一句忠实的 `event` 与闭区间 `end_para`。

第二张表 `beats`（压缩层）：
- 只能合并相邻 micro_events，`end_para` 必须从 micro_events 的止段里选择；
- 一拍是一个局部因果单元：同一目标下的动作、即时反应与局部结果通常合并；
- 场景/时间/叙述层改变，新目标或决定形成，新障碍出现/解决，新信息改变理解，
  关系或立场转折，或一件事收束后转入另一件独立事时，必须保留边界；
- 对话按议题和作用切：同一问题的追问、回答与即时反应可合并；点破秘密、立场反转、
  作出承诺/命令/拒绝/决定时保留边界；
- 不按字数或段数凑数；不要退回 1~3 条章节大纲，也不要把 micro_events 原样复制一遍。

两张表都从第 1 段自动起步，`end_para` 都必须严格递增并到达本章最后一段。
`beats` 的每条 `event` 概括它合并后的局部因果单元，不要填摘要、人物、地点或 hinge。

只输出 JSON（顶层键 `blueprints`）；不要填摘要、人物、地点或 hinge。"""


#: `--patch` 的两条字段级修复臂不重新抽取整拍；
#: 它们不是抽取，是对一条已知失败的**受限**修复；user 里只有该拍原文与失败上下文。
_HINGE_SELECT_TPL = """\
一条保真材料的短语在它所属的那一段原文里出现了不止一次，压在哪一处定不下来。
下面给你这一拍的原文、原短语和全部候选位置（各带前后文）。
选出这条材料【真正压着】的那一处：按材料种类的定义判断哪一处才是
「改一个字就废掉」的那几个字所在。
只输出 JSON，不要额外解释文字。"""

_HINGE_PHRASE_TPL = """\
一条保真材料的短语在它所属的那一拍原文里【找不到】——上一版给的是转述或编的。
下面给你这一拍的原文和材料种类。从原文里【原样照抄】一段能唯一定位的字作为新短语：
- 必须能在给出的原文里逐字找到，且只出现一次（不唯一就多带几个字直到唯一）；
- 按材料种类的定义选（硬数字抄那个数、要害台词抄那句话、物件抄那个东西的字面、
  因果前提抄交代那个条件的字）；
- 不必长——两三个字能唯一定位就够，不要抄整段。
只输出 JSON，不要额外解释文字。"""

# ---------------------------------------------------------------------------
# 各条现役调用臂的 system —— 渲染结果就是记进 `_provenance.prompts` 的那一份
# ---------------------------------------------------------------------------
#
# 【为什么在模块级就渲染好】它们没有逐本变化的插值点（书名/章号都在 user 消息里），
# 所以「模板」与「渲染结果」在本层合成一件事——同 stage5 的 `WRITE_SYS`。
# ⚠ 指纹算在**渲染结果**上（`_common.md` §一）。

#: 两段式 v2：边界臂只做一个判断；细节臂在冻结边界上填字段。
BOUNDARY_SYS_V2 = fill(_BOUNDARY_TPL)

#: `--patch` 字段级修复的两条小臂（付费，逐条失败一调）。
HINGE_SELECT_SYS = fill(_HINGE_SELECT_TPL)
HINGE_PHRASE_SYS = fill(_HINGE_PHRASE_TPL)


# ---------------------------------------------------------------------------
# 结构提示 —— 告诉模型**产出长什么样**
# ---------------------------------------------------------------------------
#
# ⚠ 全部从 `contracts.invariants` 派生，**一个取值面都不手抄**：
#   手抄的那一份会与载体分家，而分家的表现是「模型照着提示产出、载体当场拒收」。

BOUNDARY_HINT_V2: dict[str, Any] = {
    'blueprints': [{
        'chapter_ordinal': 1,
        'micro_events': [{'end_para': '结束段号(整数)',
                          'event': '逐段召回的一项新推进'}],
        'beats': [{'end_para': '结束段号(整数)',
                   'event': '合并后的局部因果单元'}],
    }],
}

#: `--patch` 两条小臂的结构提示。
HINGE_SELECT_HINT: dict[str, Any] = {'candidate_id': 'c0'}
HINGE_PHRASE_HINT: dict[str, Any] = {'phrase': '原样照抄的那几个字'}

# ── Stage2 原子事实响应面 ────────────────────────────────────────────────────
BLUEPRINT_RULES = """\
只抽取当前章已写出的事实，不补剧情，不评价写法。保留全部有证据的事实，不按重要性删减。
使用阅读序章号与本章可见人物引用；原文没写清就明确 null，不猜测。原文全文保留在 user。
对白原句仅用作 evidence：用自己的话记录语义；逐字写作义务只允许有剧情依据的字面例外。

"""

_MATERIAL_RULE = """\
【材料】每条 hinge 的 kind 只能为 $KINDS；source_fact_refs 必填本章相关 atom_key，至少一项、不得重复。
它们表示该材料必须保留的事实，不能因为同属一个 beat 就关联整拍所有事实。原句仅作后台证据。
要害台词必填 dialogue={speaker,addressees,meaning,speech_act}：主体用 {kind:character,char_id}
或 {kind:group,label}；不明确为 null，明确无特定听者的 addressees 为 []。meaning 用自己的话，
speech_act 说明承诺、请求、拒绝、回答等作用；source_fact_refs 保住对象、条件、时间、数值和关键事实。
字面例外必填 literal={kind,reason}，kind 仅 password/code/riddle/identifier；reason 说明剧情为何
依赖字面本身，证据只选最小必要密码、口令、谜面谜底或识别符号。一般承诺和警告不能归字面例外。
其他 kind 不填写 dialogue/literal；硬数字保值，物件保留在场与作用，因果前提保留命题成立。
"""

_ATOMIC_FACT_RULE = """\
【原子事实契约】`beats` 只划定原文舞台区间；不要再写一条复合 event 摘要。每拍填写
`fact_atoms[]`，每项包含：
- `atom_key`：章内临时引用，按全文出现顺序写 a01、a02……，不得重复或跳号；
- `claim`：一个可独立判断真假的最小命题。一个动作、决定、结果、揭示或状态变化各自拆开，
  不得把“甲说服乙、乙决定留下、关系缓和”塞进同一条；
- `kind`：$FACT_KINDS；`obligation`：$FACT_OBLIGATIONS。`land` 是本章新增且改写必须落地的
  信息，`context` 是本章进入时已经成立、只供场景读取的状态；
  kind 的口径：动作/决定/结果归 event，新获知的客观信息归 information，人物内在或处境变化
  归 character_state，关系变化归 relationship，读者认知变化归 reader_knowledge；
- `evidence_phrase`：该拍区间内能直接支持 claim 的原文原样短语，必须唯一命中；
- `participant_char_ids`：只从 user 的本章可选人物引用复制，不写姓名或清单外编号。

章级另交 `chapter_function`，用一句话说明本章对全书推进的语义功能；再交
`causal_edges[{before,after}]`，只记录颠倒后会改变剧情的因果，不把普通相邻都连边；
`narrative_relations[]` 只记录重构时不可丢失的显式关系，绝不因相邻或同一原文拍自动补边：
- `story_before`：真实故事时间先后；
- `response_to`：哪条 response_fact_ref 回应哪条 stimulus_fact_ref；
- `same_scene`：语义上必须同场的两个事实，端点按 atom_key 升序；
- `observes`：observer_char_id 通过 observation_fact_ref 见证 observed_fact_ref。
模型提出的每条关系都必须写 `enforcement="review"`；只有后续 typed 人审能把明确且高影响的
关系晋升为 hard。普通相邻、仅仅来自同一 beat、含糊眼神或可自由调度的动作不要立关系；
`entry_state_refs[]` 只能引用 context 原子，`ending_state_refs[]` 只能引用 land 原子。
这些引用只用 atom_key。因果图不得自环或成环。事实原子的数组位置是证据位置，不是未来写作顺序。
"""

_ATOMIC_DETAIL_V2 = """\
你只抽取 user 指定的一章。边界已经冻结：必须恰好返回同样多的 beats，并逐项原样返回
end_para。每拍填写 time_layer、place、present_char_ids、fact_atoms 与 hinges。

hinges 的定位字段用 phrase，必须在所属拍内原样且唯一。
`narration` 每项填写 grammatical_person、narrator_char_id、focalizer_char_id、pronoun_surfaces；
两个人物 id 只能引用本章可选人物或 null。

$ATOMIC
只输出 JSON，不要解释。"""

_ATOMIC_DETAIL_V31 = """\
你只抽取 user 指定的一章。先用 micro_events 逐段召回推进，再将相邻推进归入 beats；两组
end_para 都严格递增并到章尾，beats 的边界必须来自 micro_events。每拍填写 time_layer、place、
present_char_ids、fact_atoms 与 hinges。

hinges 的定位字段优先选 candidate_id，没有合适候选时才写 phrase，必须在所属拍内原样且唯一。`narration` 使用
narrator_char_id/focalizer_char_id，只能引用本章可选人物或 null。

$ATOMIC
只输出 JSON，不要解释。"""

_ATOMIC_DETAIL_V32 = """\
你只抽取 user 指定的一章。先在内部逐段召回推进，只把细分后的止段依次写入
micro_event_ends；再将相邻推进归入 beats。两组止段都严格递增并到章尾，beats 的
边界必须来自 micro_event_ends。每拍填写 time_layer、place、present_char_ids、
fact_atoms 与 hinges。

当前章每段行首是 `<段号|证据候选ID>`，标记不属于原文。fact_atoms 的证据只交
evidence_candidate_id；代码会回取该段完整原文与坐标。hinges 仍优先从 user 的候选表
选 candidate_id，没有合适候选时才写 phrase。narration 使用
narrator_char_id/focalizer_char_id，只能引用本章可选人物或 null。

$ATOMIC
只输出 JSON，不要解释。"""

_atomic = _ATOMIC_FACT_RULE.replace('$FACT_KINDS', '|'.join(FACT_ATOM_KINDS)) \
    .replace('$FACT_OBLIGATIONS', '|'.join(FACT_ATOM_OBLIGATIONS))
BLUEPRINT_SYS_V2_REFS = BLUEPRINT_RULES + fill(
    _ATOMIC_DETAIL_V2.replace('$ATOMIC', _atomic + _MATERIAL_RULE))
BLUEPRINT_SYS_V31 = BLUEPRINT_RULES + fill(
    _ATOMIC_DETAIL_V31.replace('$ATOMIC', _atomic + _MATERIAL_RULE))
_atomic_v32 = _atomic.replace(
    '- `evidence_phrase`：该拍区间内能直接支持 claim 的原文原样短语，必须唯一命中；',
    '- `evidence_candidate_id`：只复制当前章该事实证据段行首的候选 ID；不得自造；')
BLUEPRINT_SYS_V32 = BLUEPRINT_RULES + fill(
    _ATOMIC_DETAIL_V32.replace('$ATOMIC', _atomic_v32 + _MATERIAL_RULE))
_ATOM_HINT = {
    'atom_key': 'a01', 'claim': '一个最小事实命题',
    'kind': '|'.join(FACT_ATOM_KINDS),
    'obligation': '|'.join(FACT_ATOM_OBLIGATIONS),
    'evidence_phrase': '所属拍内原样且唯一的证据短语',
    'participant_char_ids': ['char_001'],
}
_HINGE_HINT = {
    'kind': '要害台词', 'source_fact_refs': ['a01'],
    'dialogue': {
        'speaker': {'kind': 'character', 'char_id': 'char_001'},
        'addressees': [{'kind': 'character', 'char_id': 'char_002'}],
        'meaning': '用自己的话描述原话的意思', 'speech_act': '承诺或请求等话语作用',
    },
}
BLUEPRINT_HINT_V2_REFS = {
    'blueprints': [{
        'chapter_ordinal': 1,
        'chapter_function': '本章建立或改变了什么局面',
        'beats': [{'end_para': '冻结的结束段号(整数)',
                   'time_layer': '|'.join(RESPONSE_TIME_LAYERS),
                   'place': 'str|null', 'present_char_ids': ['char_001'],
                   'fact_atoms': [_ATOM_HINT],
                   'hinges': [{**_HINGE_HINT,
                               'phrase': '本拍内原样且唯一的字'}]}],
        'causal_edges': [{'before': 'a01', 'after': 'a02'}],
        'narrative_relations': [{
            'kind': 'response_to', 'enforcement': 'review',
            'stimulus_fact_ref': 'a01', 'response_fact_ref': 'a02',
        }],
        'entry_state_refs': [], 'ending_state_refs': ['a02'],
        'narration': [{'grammatical_person': NARRATIVE_PERSON[0],
                       'narrator_char_id': 'char_001|null',
                       'focalizer_char_id': 'char_001|null',
                       'pronoun_surfaces': ['我']}],
    }],
}
BLUEPRINT_HINT_V31 = {
    'blueprints': [{
        'chapter_ordinal': 1,
        'chapter_function': '本章建立或改变了什么局面',
        'micro_events': [{'end_para': '结束段号(整数)', 'cue': '一项新推进'}],
        'beats': [{**BLUEPRINT_HINT_V2_REFS['blueprints'][0]['beats'][0],
                   'hinges': [{**_HINGE_HINT,
                               'candidate_id': 'h001'}]}],
        'causal_edges': [{'before': 'a01', 'after': 'a02'}],
        'narrative_relations': [{
            'kind': 'response_to', 'enforcement': 'review',
            'stimulus_fact_ref': 'a01', 'response_fact_ref': 'a02',
        }],
        'entry_state_refs': [], 'ending_state_refs': ['a02'],
        'narration': [{'grammatical_person': NARRATIVE_PERSON[0],
                       'narrator_char_id': 'char_001|null',
                       'focalizer_char_id': 'char_001|null',
                       'pronoun_surfaces': ['我']}],
    }],
}
BLUEPRINT_HINT_V32 = {
    'blueprints': [{
        'chapter_ordinal': 1,
        'chapter_function': '本章建立或改变了什么局面',
        'micro_event_ends': [1, 3, 5],
        'beats': [{
            **BLUEPRINT_HINT_V2_REFS['blueprints'][0]['beats'][0],
            'fact_atoms': [{
                **_ATOM_HINT,
                'evidence_candidate_id': 'e001',
            }],
            'hinges': [{**_HINGE_HINT,
                        'candidate_id': 'h001'}],
        }],
        'causal_edges': [{'before': 'a01', 'after': 'a02'}],
        'narrative_relations': [{
            'kind': 'response_to', 'enforcement': 'review',
            'stimulus_fact_ref': 'a01', 'response_fact_ref': 'a02',
        }],
        'entry_state_refs': [], 'ending_state_refs': ['a02'],
        'narration': [{'grammatical_person': NARRATIVE_PERSON[0],
                       'narrator_char_id': 'char_001|null',
                       'focalizer_char_id': 'char_001|null',
                       'pronoun_surfaces': ['我']}],
    }],
}
# v3.2 的 fact atom 不允许同时携带旧自由短语；结构提示必须与 StrictModel 同构。
del BLUEPRINT_HINT_V32['blueprints'][0]['beats'][0]['fact_atoms'][0][
    'evidence_phrase']
