#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""str06_semantic_cases —— R10 关键语义反例／正例登记构造器（R10-0.5）。

【是什么】R10a/b 各子批红绿案例的唯一登记面：每例写明合成源材料、观察
陈述形态、期望 typed 结果与「由哪个子批在哪个真实判据入口转红／绿」。
本文件只登记案例数据与最小合成样例——不使用付费回答或真实正文做
fixture（tests/AGENTS.md）；判据入口由各子批测试引用本表接到真实判据。

【怎么用】R10a a1（Stage2 引文／时态／命题纯度判据）、a2（Preserve 锚定／
series）、a3（证据冲突回派）、a4（kp/kf 锁与 Design v5 校验）、a5（锁位置
Guard）、R10b（背景投影收窄与 renderer v7）的测试从 `CASES`／
`CASES_BY_BATCH` 取例：反例必须在真实判据入口红、正例必须绿。机械不可查
的案例（`mechanical=False`）归冻结关键案例（Golden_Set_v2.1）与人工通读，
不进机械判据，也不得因无法机检而删除登记（防作弊分母）。

【合成原则】源句样例全部为合成最小样例（自造人名与情境），不复制任何
真实源书正文；`expected` 以自然语言＋结构化要点表达，R10a 新 schema
（EvidenceQuote／anchors／kp/kf）落地后由各子批把期望接到真实模型与
判据——本文件不前向依赖未落地 schema。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SemanticCase:
    """一个关键语义反例（C*）或合法正例（P*）。

    · `source_sample`：合成源句（最小样例，模拟 Stage1 句段内容）；
    · `observation`：观察／wire 层对同一内容的问题形态（反例＝错误观察长
      什么样；正例＝合法观察长什么样）；
    · `expected`：期望 typed 结果（R10a 后由真实 schema 表达）；
    · `red_in`／`green_in`：判据落点子批——反例在该子批的真实判据入口
      必须红；正例在判据不拦（绿）；
    · `mechanical`：是否可机械判定；False 归关键案例与通读；
    · `mechanical_face`：可机械判定时的判据面描述。
    """

    case_id: str
    title: str
    source_sample: tuple[str, ...]
    observation: str
    expected: str
    red_in: str
    green_in: str
    mechanical: bool = True
    mechanical_face: str = ''
    note: str = ''


#: 判据落点子批闭集（与 R10a／R10b 批次一致，见 docs/DECISIONS.md 2026-10-01 行；read-only＝只读数不阻断；
#: golden＝冻结关键案例与通读）。
BATCHES = frozenset({
    'R10a-a1', 'R10a-a2', 'R10a-a3', 'R10a-a4', 'R10a-a5',
    'R10b', 'read-only', 'golden',
})

CASES: tuple[SemanticCase, ...] = (
    # ═══════════════════ 反例 C1–C11（R9c 首轮核读与 B6 反例的抽象）═══════════════════
    SemanticCase(
        case_id='C1', title='眼神猜测被升格为他人确知＋读者确认',
        source_sample=('沈青觉得表哥像是看穿了什么，却始终没说破。',),
        observation='wire 把「觉得像是」写成 cognition.mode=known，并在后拍'
                    '追加读者确认（reader confirms 同一命题）。',
        expected='mode 保持 suspected（认识限定只进 mode，不进命题）；读者'
                 '确认只能落在授权揭示的 known 锁位置。',
        red_in='R10a-a4', green_in='R10a-a2',
        mechanical_face='a4：kp 键持有者无锁或模式越权（known 只能来自 '
                        'known 锁）；a2：mode 由 compiler 从 cognition 拷贝',
        note='R9c 实例：b06_21 yun_niang suspects → Design 写 known＋读者确认'),
    SemanticCase(
        case_id='C2', title='预计时长被写成已发生',
        source_sample=('他说此去约莫半个月便回。',),
        observation='wire 把「约莫半个月」记为 effect.modality=actual 的既成'
                    '时长（如同期已过去半月）。',
        expected='effect.modality=expected（计划／预计≠确知≠已发生）；时态锁 '
                 'kf 在 Brief 派生，写手措辞为「计划／预计，尚未发生」。',
        red_in='R10a-a4', green_in='R10a-a4',
        mechanical_face='a1：modality 闭集给 expected 独立槽位（限定词只进 fold '
                        '读数不进门，P3 正例要求不得按关键词拦）；'
                        'a4：kf 键 modality 与锁一致',
        note='R9c 实例：预计约半个月 → 已发生时长'),
    SemanticCase(
        case_id='C3', title='两个时点被合并为一次',
        source_sample=('三日后，家里请了大夫来诊脉。',
                       '又过了一个月，大夫才诊出喜脉。',),
        observation='wire 把两次求医（请大夫／诊出）合并为一次事件与单个效应。',
        expected='两个独立兑现时点分别登记；同一机制的多次发生用 series '
                 '（members≥2）登记，成员绑定不同拍。',
        red_in='R10a-a2', green_in='R10a-a5',
        mechanical_face='a2：series 成员闭集且互斥；a5：series 成员同拍 → '
                        'series_collapsed',
        note='R9c 实例：诊孕时点提前／两时点合并'),
    SemanticCase(
        case_id='C4', title='引文错位（不在所引句段／句段越界）',
        source_sample=('她攥紧了帕子，指节发白。',),
        observation='wire 的 basis 引文写成「他攥紧了帕子」（与句段原文不符），'
                    '或引用了不属于该拍 source_refs 的句段。',
        expected='quote 逐字存在于所引句段内，且句段属于该拍 source_refs；'
                 '失败走 missing_literal／unknown_segment → revise_self。',
        red_in='R10a-a1', green_in='R10a-a1',
        mechanical_face='resolver 逐字判定＋段归属判定（不归一化、不模糊匹配）'),
    SemanticCase(
        case_id='C5', title='多次意外现身被合并',
        source_sample=('庙会上，她又瞧见了那道身影。',
                       '夜市散了，那身影第三次出现在巷口。',),
        observation='wire 把两三次现身合并为一次「反复出现」事件。',
        expected='同一机制的每次发生独立登记并以 series 关联；Design 不得'
                 '把 series 成员绑定到同一拍。',
        red_in='R10a-a2', green_in='R10a-a5',
        mechanical_face='a2：series 登记；a5：series_collapsed 判据'),
    SemanticCase(
        case_id='C6', title='线索拍被强加读者确认',
        source_sample=('袖口那点洗不净的血迹，让她起了疑。',),
        observation='Design 在仅有怀疑（线索 open）的拍写了 reader confirms。',
        expected='线索／怀疑拍合法地没有状态变化（空 target_delta 允许）；'
                 '读者确认只用于已授权的揭示（known 锁位置）。',
        red_in='R10a-a4', green_in='R10a-a5',
        mechanical_face='a4：kp 键无 known 锁不得 confirms；'
                        'a5：确认早于授权位置 → epistemic_lock_order'),
    SemanticCase(
        case_id='C7', title='背景历史回声（无消费者的状态向后投影）',
        source_sample=('那年冬天，灶上的热汤凉了又热了几回。',),
        observation='只写一次且无任何正文消费者（声明读取／确认目标／'
                    '锁键）的拍状态，被逐章投影进背景分区。',
        expected='背景只投书级初态立足点＋后文仍被消费的当前状态；无消费者'
                 '的拍状态留在其拍「必须写出」与 Machine IR。',
        red_in='R10b', green_in='R10b',
        mechanical_face='背景投影消费者判定（正文声明读取／确认目标／锁键）'),
    SemanticCase(
        case_id='C8', title='书级表达举了后段具体情节',
        source_sample=('全书以器物细节回扣前情。',),
        observation='书级表达／风格／声线材料里出现后段具体（如「第 9 章的'
                    '断簪要在第 2 章先提」）。',
        expected='书级表达只写机制，不举后段具体；具体后段情节只送到获准'
                 '位置（expression_refs 落具体拍）。',
        red_in='R10b', green_in='R10b',
        mechanical=False,
        mechanical_face='仅称谓时点部分机械可查（surface_introductions vs '
                        '释放位置）；表达举后段具体归规则＋通读',
        note='机械面有限：正例由 DESIGN_RULES 与通读保障'),
    SemanticCase(
        case_id='C9', title='原文未知被补答',
        source_sample=('没人知道那年他去了哪里，也没有信回来。',),
        observation='源悬念未收束（loop open），Design 却 set 了答案（如'
                    '「他去了南边投军」）。',
        expected='原文未回答锁（compiler 从 canonical loops 判定）从承载位置'
                 '起进禁区；原文未说明的 kf 键不得 set。',
        red_in='R10a-a4', green_in='R10b',
        mechanical_face='a4：原文未说明的 kf 不得 set；R10b：未回答锁进章包禁区'),
    SemanticCase(
        case_id='C10', title='生命周期残留（临时状态向后投影）',
        source_sample=('那几日她咳得厉害，入了春便好了。',),
        observation='已解除（unset）或临时性的身体状态仍留在后续章的背景'
                    '投影里。',
        expected='unset／局部条件停止向后投影（≠宣布故事中解除，留在 Machine '
                 'IR）；残留进 fold／packet_metrics 读数。',
        red_in='read-only', green_in='R10b',
        mechanical=True,
        mechanical_face='只读数不阻断：fold 生命周期读数＋被剔除历史回声数',
        note='R9c 实例：待执行计划／双人选状态／搬家计划残留'),
    SemanticCase(
        case_id='C11', title='救人动机遗漏（关键因果未进必保）',
        source_sample=('当年是那孩子背着弟弟冒雪求医，她才动了恻隐之心。',),
        observation='Preserve 义务集漏掉动机因果（只保了救人行为，没保「为何'
                    '救」），下游无从正确展开。',
        expected='必保义务覆盖动机因果（自然语言条件）；机械不可查——由冻结'
                 '关键案例与通读判定，不设关键词闸。',
        red_in='golden', green_in='golden',
        mechanical=False,
        note='机械不可查，归 Golden_Set_v2.1 关键案例；防作弊分母同时报义务'
             '总数与必保比例'),

    # ═══════════════════ 合法正例 P1–P6（判据不得误拦）═══════════════════
    SemanticCase(
        case_id='P1', title='跨章证据支持同一次揭示',
        source_sample=('第三章里，她发现账册缺了一页。',
                       '第七章里，管事招认是他撕的。',),
        observation='ch07 的 confirms 命题引用 ch03 的疑点（跨章证据）。',
        expected='同一命题的授权揭示可由跨章证据支持；跨章 anchors 合法，'
                 '不因跨章而拒。',
        red_in='R10a-a2', green_in='R10a-a4',
        mechanical_face='a2：跨章锚点闭集可解析；a4：known 锁由对应义务授权'),
    SemanticCase(
        case_id='P2', title='先怀疑、后新证据确认',
        source_sample=('她起初只当他藏着隐情。',
                       '直到亲眼见他把银镯塞进包袱，她才信了。',),
        observation='cognition 先 suspects、后 confirms（同一持有者同一命题）。',
        expected='模式沿授权路径升级（suspected→known 经新证据锁），判据'
                 '不得把合法升级拦成越权。',
        red_in='R10a-a4', green_in='R10a-a4',
        mechanical_face='a4：模式属该持有者被授权的模式集合'),
    SemanticCase(
        case_id='P3', title='「原以为／后来证实」不被关键词拦',
        source_sample=('她原以为丈夫只是性子冷，后来终于证实他心里有她。',),
        observation='命题 statement 含「原以为／证实」等限定与转折词。',
        expected='命题纯度判据不得按关键词拦（限定词只进读数不进门）；'
                 '「以为＋证实」序列按 P2 的授权升级处理。',
        red_in='R10a-a1', green_in='R10a-a1',
        mechanical_face='a1：限定词只是 fold 读数（命题含限定词比例），不是闸'),
    SemanticCase(
        case_id='P4', title='第三人称叙述中的对白自称',
        source_sample=('她说：「我不去。」',),
        observation='第三人称限知叙述，对白内出现第一人称「我」。',
        expected='对白自称不改变叙述人称；validator 与写手协议均不得拦。',
        red_in='R10b', green_in='R10b',
        mechanical_face='写手协议 v7：人称约束限定叙述层；'
                        'material_identities 不把对白「我」当身份泄漏'),
    SemanticCase(
        case_id='P5', title='先叙述确诊、再回顾此前监测（合法倒叙）',
        source_sample=('大夫确诊的那天，她反而松了口气。',
                       '此前两个月的种种监测，此刻都成了注脚。',),
        observation='目标设计把确诊场景放在回顾监测场景之前（讲述顺序≠'
                    '故事发生顺序）。',
        expected='信息披露顺序由锁位置 Guard 验证；故事发生顺序是 requires '
                 '因果前提语义，不机械映射阅读序——合法倒叙不得拦。',
        red_in='R10a-a5', green_in='R10a-a5',
        mechanical_face='a5：epistemic_lock_order 只查知情升级／读者确认的'
                        '授权位置，不查阅读序'),
    SemanticCase(
        case_id='P6', title='空状态变化拍',
        source_sample=('两人沿着河堤慢慢走了一段，谁也没说话。',),
        observation='某拍无 effects／cognition／loops（纯氛围或过渡）。',
        expected='允许空 target_delta／空转移拍；不要求每拍新增读者确认，'
                 '也不为满足阶段规则新增确认拍或故事事件。',
        red_in='R10a-a4', green_in='R10a-a4',
        mechanical_face='a4：target_delta 允许为空（schema 与校验不强制）'),
)

#: 反例／正例分组与按子批索引（R10a/b 测试取例入口）。
COUNTEREXAMPLES: tuple[SemanticCase, ...] = tuple(
    c for c in CASES if c.case_id.startswith('C'))
POSITIVE_CASES: tuple[SemanticCase, ...] = tuple(
    c for c in CASES if c.case_id.startswith('P'))
CASES_BY_BATCH: dict[str, tuple[SemanticCase, ...]] = {
    batch: tuple(c for c in CASES
                 if batch in (c.red_in, c.green_in))
    for batch in sorted(BATCHES)
}

#: 登记自洽：子批名合法、id 唯一、可机械判定的反例必须写明判据面。
assert all(c.red_in in BATCHES and c.green_in in BATCHES for c in CASES)
assert len({c.case_id for c in CASES}) == len(CASES)
assert all(c.mechanical_face for c in CASES if c.mechanical)
