#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reviewer_contract.py —— 每个 reviewer blocker 随 prompt 下发的裁决契约摘录。

运行时权威是本模块。`.workflow/skills/novel-pipeline-operator/references/delegated-review.md`
用 ``<!-- reviewer-contract:<id>:begin/end -->`` 锚块镜像同一段文字（同一 id 多个锚块按空行拼接），
`tests/test_reviewer_contract.py` 逐字比对，两边任一漂移即红。这样 headless Commander 不必再读
整份 operator skill：它每轮只看自己 blocker 的判据，而那正是它过去每轮多花五次往返去读的东西。

这里只有裁决判据，没有 CLI 命令、调度或收尾指令；`reviewer_tasks.build_task` 的静态守卫同样
扫这段文字。
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from novel.orchestrator import queue

CONTRACT_ANCHOR = re.compile(
    r'^<!-- reviewer-contract:([a-z0-9-]+):(begin|end) -->$', re.M)

#: 所有 blocker 共用的裁决纪律；不重复 build_task 已写的输出格式要求。
COMMON = '''裁决纪律：
- 每个结论都要能回到工作包里的具体 code、rule id、主体 SHA-256 或逐字材料；不写"看起来没问题""全绿"。
- "不知道""没运行""缺失""空结果""没问题"必须分开；证据不足时保持 UNVER 或模板的关闭式默认值，不猜 PASS/accept。
- INFO、metric、UNVER、skipped 只披露，不制造付费动作；只提交 submission_options 允许的动作与取值。
- 本工作包就是本轮唯一材料面：不读取 operator skill、仓库源码、测试或盘上产物，也不运行命令取证。'''

#: 契约 id → 裁决判据正文；与 delegated-review.md 锚块逐字一致。
CONTRACT_TEXT: Mapping[str, str] = {
    'stage0': '''1. 逐段判断被剔除内容是否确为广告、平台尾巴、元数据或非正文；任何情节、人物行动、对白、章节正文被剔除都先修复。
2. 核对总码点、保留码点、剔除段数与保留率；异常低的保留率必须解释，不能只看 HARD。
3. 检查正文开头、结尾和剔除边界，避免截断句子或把正文前言误删。
4. 确认除待批准项外无 HARD，所有 warning 均已人工判断。
5. 需要修改时只走 `stage0.edit` 的正式编辑入口，复检后再批准。

1. 只读工作包按登记版本扫描平台尾巴/广告模式，并记录**扫了多少类、各命中几处**；
   不再为每本书临时拼词表，也不把这些审阅模式搬进生产剔除规则；
2. 每一个命中都要逐条读上下文再定性——现代题材里社交软件、人群、群聊这类词是正文自然用词，
   凭词表命中就判广告会把正文当垃圾删掉；
3. 抽查首尾边界：首行是否为正文首句（而非标题/元数据残留），尾行是否为完结标记或正常收束，
   有无被截断的句子；
4. 顺带核一次结构分母（总行数/非空行、独占数字行的章号集合、第一个章号之前是否为文案区）。''',
    'stage2': '''1. `PASS`：主体与原文逐项一致，且集合完整性已做正向与反向核对；scope 总理由与逐项例外理由都写明分母和核验位置。
2. `FAIL`：存在可指向的冲突、错抽或漏抽；当前增量里的问题必须一次列全。事实确有错误时从 `stage2.global --emit-edit-template` 取得当前 typed GlobalEdit。字段 patch 只能修改模板已有行，行数、顺序、subject id 与字段集合全部绑定；集合完整性的新增、删除或拆分只填写模板给出的 `collection_edits[].operations`，不得改 `collection` 或 `expected_collection_sha256`。Stage2 每个 Global/MainText 主体最多四席 Commander：一席完整审阅、至多两席定向订正、一席只读独立终审；每席使用新的 logical/provider session，同席内因网络中断的 resume 不占新席。订正席的包只含仍未闭合或因依赖变化重开的 subject cards；终审席读全部 blocking 项（含已 PASS 的理由与证据）、整书正文与 Blueprint 工作包，只能 confirm 或以自己为 actor 把指定项 reopen 为 FAIL/UNVER，不得提交任何修改，也不得为通过而复述前席理由。`post_edit_review` 绑定修前 workpack、source 与完整旧未闭合 scope：本 edit 新打开的项才走默认自审，未修改且 hash 存活的旧 FAIL/UNVER 必须在 `decisions[]` 显式复判。不能重复提交同一 review，也不能用“已知问题”把 FAIL 当成披露放行。
3. `UNVER`：证据不足以安全判断；绝不改写成 PASS。只有当前 submission option 明确给出 `quarantine_group`、且该 kind 的 `generation_impact=quarantine_or_fix` 时，才可在阅读完整组后填写隔离理由：状态仍为 UNVER，原始事实与审计保留，下游写作视图删除完整组。`generation_impact=must_fix` 只有在证据支持 PASS 或能指出真实事实错误并用 GlobalEdit 修正时才能收口；材料仍不足就保持 UNVER 并以 `stopped` 结束，不为追求全绿臆造编辑。`disclose` 项不阻断生成但继续披露。''',
    'stage4-design': '''1. 策略只决定 `accept / request_replan`。只有策略本身无法指导全书结构重构时才拒绝；单章问题不得
   借策略决定放大成整书重跑。
2. 每章决定 `accept / request_repair`。拒绝理由必须指出该章可核证的问题；约束只选服务端枚举的
   repair code，并填写属于本章 subject 的 `scene_refs / opaque_aliases` 或与 Stage2 同构的 typed
   关系。每轮 repair 都携带当前问题与全部尚未关闭的历史约束；已关闭约束显式保留状态，不得静默
   遗忘。不得凭空补证据，不得把自然语言 reason 当自由 prompt。约束语义与合法示例形状以工作包
   `submission_options.allowed_values` 的 `semantics / shapes` 为准，不必再读源码或测试。
3. 已接受且 subject 未变的历史章可由模板携带 `source_decision_sha256` 复用，但仍须核对并对当前
   整书主体原子提交；策略、truth/root/alias/review contract 变化使全章复用失效，单章变化只失效该章。
4. 提交后只认 `design_review.state`。`repair_requested` 时执行服务端 action 中的
   `stage4.design --review-token <opaque>`；单章耗尽只走该章新 repair epoch 或上述 0-wire
   完整章 edit。`strategy_replan_requested` 时才执行 `stage4.design --replan`，并携带累计失败反馈；
   effective strategy/input digest 未变的 accepted 章保留 checkpoint，不得重发 wire。模型修订仍需
   重新 preview/付费确认，修订 A 后还要再提交一份当前完整 A 审阅。headless AgentRun 在 typed
   决定落盘后由 RunSupervisor 精确执行该 action，不再调用 Commander；交互式操作者仍显式
   preview/确认，但不得自行改 node、flags 或 token。typed approval 决定一经 loader 通过就按内容寻址
   持久化到 `_service/edits/<sid>/`；代码/skill 漂移、崩溃或重启后，同一 blocker 先零模型重放该
   决定，不重复唤起 Commander。''',
    'stage4-structure': '''1. `retain` 章的声明变化必须为空；人工理由要能指出强线性、因果锁定、证据受限或本章本就没有可靠
   重组空间中的具体一种，不能只写“保留更稳”。
2. `reconstruct` 章先核对声明的变化种类，再读代码复算的分区、披露顺序、非连续重组和首尾锚点。
   至少能明确指出场景入口、事实组合或揭示顺序中的真实变化；低文字重合、场数变化或 goal 的自报
   不能单独充当结构变化证据。
3. 不为顺叙、倒叙或距离读数发明自动阈值。因果边约束故事成立，不等于必须按因果顺序披露；
   `unver`、零分母或不可比较 span 保持原态，不能洗成通过。
4. 核对 B 只给冻结的 A 候选绑定 stage/material/participant 证据，没有改场景顺序、事实分组、
   开尾目标或 retain/reconstruct 决定；该不变量若被破坏，应由正式计划闸关闭式拒绝，不靠人工宽免。
5. 逐条阅读 `binding_concerns`。它们是非阻断语义担忧，不是自动失败或正式 infeasible proof；接受章的
   理由应说明 concern 为何不破坏本章结构，无法确认时保留 `request_replan`，不得把 concern 隐藏掉。''',
    'stage5-writing': '''Stage5 写作授权与两次 Stage4 审阅都是独立的人类决定。只有当前最终 typed 结构决定覆盖全书且全部
`accept`、并已单独生成 current/fresh WriterContext，才能生成写作授权模板；Commander 不得从 s4
绿灯、付费总授权或正文外发授权推导这枚决定。

模板携带当前 Stage1 / Stage4 计划 / WriterContext 与 measurement contract 所绑定的完整 subject SHA-256。
`apply writing` 只接受 typed JSON 的 actor、reason 与完整 current subject；旧 UI 的空 body 兼容入口
不是 Commander 入口。结构、计划或测量主体漂移后旧授权必须显示为 non-current，不能靠历史
`writing=true` 继续写作。''',
    'stage5-closure': '''`closure.workpack` 精确绑定 fresh Review、全部 canonical 章、当前规则指纹、must-appear finding、
fact/unver 分组与未销账 trim。状态为 `review_required` 或 `blocked` 时先按 `next_step` 收口，不能填写
模板硬过。`ready` 模板仍是关闭式起点：逐条核对 must-appear，只有正文证据确实存在时选
`confirmed_present`；需要人工接受例外时选 `accepted_exception` 并写出可复核理由。fact/unver 整组
确认必须按完整 items/digest 审阅，不能用“看过”代替。每项都要把 `placeholder=true` 改为 false、
清空 `template_token`，并填写真实 actor/reason；workpack、Review、章稿或规则任一漂移都会拒绝旧模板。''',
    'stage6': '''1. 排除所选候选自身的任何机械 HARD。**这是服务端唯一会替你否决候选的一道闸。**
2. 合并决议在**类型上恒是** `UNVER`：服务端不替人否决任何候选，矛盾线索只呈现不阻断。
   ⇒ 收口一律是逐项人工核验后记 `human_verification`（带 actor、reason 与完整
   subject SHA-256），没有第二种依据可写，也没有任何自动 PASS 可用。
   ⚠ **责任在你这一侧**：一条实质矛盾不会被机制拦住，只会被读判官证据的人拦住。
3. `judges` 两侧各自的 verdict 与逐字证据才是可读的信号。缺审判、coverage 不足、
   A/B 分歧或缺证据，读的是它们分别说了什么，不是合并那一格。
4. 在可定稿候选中优先选择 info 命中较少、质量排名较高者。

   先确认这份 `UNVER` 是 **双盲判官产出的带证据结论**，还是**根本没有 semantic 产物**——
   两者在报告上长得像，处置完全不同：后者应先补跑 `stage6.semantic`（见
   [经验库](field-notes.md) FN-013），再据判官的匿名质量排序与逐候选证据裁决。
   在零语义产物的情况下直接写人工核验结论，等于把审美偏好写成客观通过。

   ⚠ 合并决议**恒是** `UNVER`，所以不要按它筛候选——要读的是 `judges` 两侧各自说了什么。
   判官两侧都给不出可用信号、排名并列，或候选机械读数完全打平时，这一步没有可用信号，
   **仍不得直接跳到审美理由**。改用可复制的事实密度判据：把每个候选的每一条事实性断言
   回**成书正文**逐字检索（⚠ 参照系是重构后的成书，不是原作——文案预告的是前者），
   记录命中数/分母与无据意象条数，优先选择无据意象为零、事实密度最高者；检索结果与分母写进终审包。
5. 仍并列时，比较它与 blueprint 开篇冲突、原作文案体裁和正文首章气质的一致性，并写出审美理由。
6. 对所有落选候选记录淘汰原因；总报告中的落选残红可保留，但不能混写成所选候选残红。

同一文本出现相互矛盾的语义判断时，先核对完整 SHA-256、轮次、evaluator 指纹和输入
freshness。确为同输入非确定性时采信更严格的一轮，不用后一次翻绿覆盖前一轮风险；
把冲突本身写入终审包。''',
    'stage7': '''1. 要求本层 HARD 全部清零，并逐角色核对旧 canonical、aliases、是否改名、新 canonical、理由和替换次数。
2. 审阅 `rn_kept_contains_mapped`。保留串内嵌已改旧名时，展开该角色的映射，不能让 kept 保护旧名进入成书。
3. 保留数字、排行、辈分、关系和官职语义标记；`rn_semantic_marker_changed` 命中时换用含相同标记的新名。
4. 单字别名不要全局替换。把不改的单字明确登记为 `old == new`，用长度至少 2 且有上下文的 `extra_subs` 处理真实昵称。
   ⚠ **这一条自带一个只有走 `extra_subs` 才会踩的坑**：任一 `extra_subs` 键的真后缀，
   不得等于映射表中**任一**键（含 registry 下发的完整称呼）的真前缀——命中 `rn_overlap`（hard），
   理由是单次扫描会在交叠处切出半截残串。判据只查人工补充那一侧（registry 的键都是完整称呼，
   不会有这个形状）。解法是给其中一个键左扩或右扩一个边界字符（引号、标点），例如
   `景，你说我` → `「景，你说我`。踩中的代价不只是时间：白跑的那一轮 `--apply-edit`
   会开新的人工修订轮并把上一轮归档进 `_rounds/`，所以提交前先自查这一条。
5. 全文补扫人物表不一定覆盖的 surface：顿号拆名、未登记简称、家族/门派/宅邸简称、外文家名和单字昵称。
   先读 `rn_derived_alias_candidate`：它会把「旧名字尾字 + 关系后缀」（如 `箬姐姐`）与
   无歧义建议列出来，但只是 INFO 候选；逐条核对后才可写入 `extra_subs`，不能自动采纳。
6. 对 `rn_residual_stem` 逐条读上下文；普通词假阳可以保留并解释，高置信人名残留必须修复。
   修法有两条，**先判能不能走自动的那条**：`stage7.rename --adopt-residual high` 显式采纳
   probe 判为可安全应用的 high residual 建议并重放确定性链（0 成本），需要逐条判断或
   probe 没给建议时才手写 `name_map_edit.json`。⚠ 它**重写 `stage7/name_map.json`、清旧审批、
   退休现有成书**，与 `--apply-edit` 同样要重批重拼。
   ⇒ **顺序是先修后批**：本层残留在 `stage7.rename` 追检时就已全部可见，把采纳放在
   `stage7.approve` / `stage7.assemble` **之前**做完，重批重拼那一整轮就根本不会发生。
   先批后修不是错，只是白费一轮批准、一次组装和一次逐字对账。
   采纳后必须重新取决策面 SHA-256 再提交批准（旧值已随 name map 作废）。
   判断建议本身可不可信，看两样：`suggest` 的新姓与 `surname_map` 是否一致，
   以及那条 `unit` 是不是真的人物称呼——**只采纳 high**。低置信档里混着该姓字的普通词用法
   （判据自己说这类假阳靠调阈值消不掉，中文没有词边界），照单全收会把正文改坏。
7. 先运行 `stage7.rename --emit-edit-template` 取当前注册的 `NameMapEdit` 骨架；人工编辑
   只写书目录内 `stage7/name_map_edit.json`。提交前先跑
   `stage7.rename --validate-edit <绝对路径>`；它与 apply 共用完整构建/判定链但不归档、不覆盖、
   不退休成书。预检达到预期后再经 `stage7.rename --apply-edit <绝对路径>` 正式落盘。''',
}

#: reviewer blocker → 契约 id。Stage4 三个 design blocker 共用 A 段；Stage2 三个 blocker（审阅/订正/终审）共用同一段。
CONTRACT_FOR_BLOCKER: Mapping[str, str] = {
    'stage0.approve': 'stage0',
    queue.WAITING_ON_STAGE2_REVIEW: 'stage2',
    queue.WAITING_ON_STAGE2_EDIT_CORRECTION: 'stage2',
    queue.WAITING_ON_STAGE2_FINAL_REVIEW: 'stage2',
    queue.WAITING_ON_STAGE4_DESIGN_REVIEW: 'stage4-design',
    queue.WAITING_ON_STAGE4_BINDING_RESOLUTION: 'stage4-design',
    queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN: 'stage4-design',
    queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW: 'stage4-structure',
    queue.WAITING_ON_WRITING_APPROVAL: 'stage5-writing',
    queue.WAITING_ON_STAGE5_CLOSURE: 'stage5-closure',
    'stage6.approve': 'stage6',
    'stage7.approve': 'stage7',
}


def for_blocker(waiting_on: str) -> str:
    """本 blocker 随 prompt 下发的契约全文；未登记 blocker 抛 KeyError，不发泛化文本。"""
    return COMMON + '\n\n' + CONTRACT_TEXT[CONTRACT_FOR_BLOCKER[waiting_on]]


def parse_anchored_blocks(text: str) -> dict[str, list[str]]:
    """解析 markdown 里的锚块；配对/嵌套错误关闭式抛错，供测试与 skill 校验共用口径。"""
    blocks: dict[str, list[str]] = {}
    open_key: str | None = None
    open_pos = 0
    for match in CONTRACT_ANCHOR.finditer(text):
        key, kind = match.group(1), match.group(2)
        if kind == 'begin':
            if open_key is not None:
                raise ValueError(f'reviewer-contract 锚块嵌套：{open_key} 未关闭又打开 {key}')
            open_key, open_pos = key, match.end()
            continue
        if open_key != key:
            raise ValueError(f'reviewer-contract 锚块不配对：end {key}，当前打开 {open_key}')
        body = text[open_pos:match.start()].strip('\n')
        if not body.strip():
            raise ValueError(f'reviewer-contract 锚块 {key} 为空')
        blocks.setdefault(key, []).append(body)
        open_key = None
    if open_key is not None:
        raise ValueError(f'reviewer-contract 锚块 {open_key} 没有 end')
    return blocks


__all__ = [
    'COMMON', 'CONTRACT_ANCHOR', 'CONTRACT_FOR_BLOCKER', 'CONTRACT_TEXT',
    'for_blocker', 'parse_anchored_blocks',
]
