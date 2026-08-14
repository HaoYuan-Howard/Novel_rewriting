#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 7 当前判据注册表：33 条人物替换与成书规则。

分布为 15 HARD、8 INFO、3 metric、7 unver。`rn_*` id 同时用于报告、人工豁免与来源账；
模型决定和人工 `extra_subs` 使用分开的碰撞规则，避免把人工确认扩进模型 HARD 取值面。

本层不开放付费自动返工。有 HARD 时 `--approve` 拒绝定稿、`--assemble` 拒写 `book.txt`；
7 条 `unver` 常驻输出，因此无 HARD 命中时报告为 `amber` 且 `passed=True`。
"""
from __future__ import annotations

from contracts.rules import REGISTRY, Rule

STAGE = 'stage7'

#: 人工修订路径全表共用；`repair='NONE'`，因此不会误导为写作层自动返工。
_HUMAN_FIX = ('〔人能 0 成本改掉：`--apply-edit` 收完整决策集 JSON，人直接把映射改对；'
              '还有 HARD 时 `--approve` 拒绝定稿、`--assemble` 拒写 txt〕')


def _hard(rule: str, item: str, requirement: str, *, owner: str = STAGE, **kw) -> Rule:
    """`owner` 可覆盖；成书前置条件的根因可属于 Stage 5。"""
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='hard',
                                  who_checks='HARD', requirement=requirement,
                                  owner=owner, **kw))


def _info(rule: str, item: str, requirement: str, *, owner: str = STAGE, **kw) -> Rule:
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='info',
                                  who_checks='INFO', requirement=requirement,
                                  owner=owner, **kw))


def _metric(rule: str, item: str, requirement: str, **kw) -> Rule:
    """度量：**无条件报出**，`owner=None`（它不是一条结论，没有「根因第一嫌疑层」）。"""
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='metric',
                                  who_checks='METRIC', requirement=requirement, **kw))


def _unver(rule: str, item: str, requirement: str, *, who_checks: str,
           owner: str | None = None, **kw) -> Rule:
    """查不了的：**永远输出**，不是「还没写完」。

    ⚠ `who_checks` 必须显式给，因为这 7 条分**两族**，而它们指向的需求行记号不同：
      · `HUMAN` —— 真正由人把关的那 5 条（`R-10.11`/`R-10.16`/`R-10.18`/`R-10.7b` 标着 `人`）；
      · `INFO`  —— 判定**前提**不成立时出声的那 2 条（`R-10.9`/`R-10.14` 那一族）。
    合成一个默认值，其中一族就会指着一个说着别的话的需求行。
    """
    return REGISTRY.register(Rule(rule=rule, stage=STAGE, item=item, sev='unver',
                                  who_checks=who_checks,          # type: ignore[arg-type]
                                  requirement=requirement, owner=owner, **kw))


# ── 映射表自身的结构：十条 HARD/INFO，全是 `RENAME_SYS` 六条规则的后置条件 ──────
#
# ⚠ 这一族的需求行 `R-10.13` 是 2026-08-10 补的：原型有 13 条判据在守这两族，
#   而需求文档**一条落点都没有**——`R-10` 从前只写了替换器与探针，
#   没写「模型该交出什么」。最刺眼的是 ③⑤：「新名不重名、不与原作名字相同」
#   下给模型的规则里写得清清楚楚，**三条 HARD 在守，而需求 0 处落点**。

_hard('rn_llm_shape', '模型给每个角色都交出了决定与理由', 'R-10.13',
      note='`R-10.13①`。长度必须与 registry 一一对应，多一个少一个都算。' + _HUMAN_FIX)

# 【为什么「漏了」必须是 HARD 而不是静默保留】模型少列一个称呼，
# 与它明说「这个不改」在产物里长得一模一样，而前者是漏、后者是决定。
_hard('rn_map_coverage', '下发的每一个称呼在映射表里都有明确去向', 'R-10.13',
      note='`R-10.13②`。「漏了」与「决定不改」必须分得开。' + _HUMAN_FIX)

_hard('rn_new_collide', '新名全书唯一，没有两个角色共用一个新名', 'R-10.13',
      note='`R-10.13③`。⚠⚠ **取值面只有模型写的 `new_canonical` 那一半**——'
           '人手补的 `extra_subs` 与 `aliases[].new` 都不进。盘上（書1）'
           '`祈颜→叶翎`（模型）与 `祈辕→叶翎`（人工）同时在 `subs` 里，'
           '而本条打的是 `[OK]`。**不扩它的取值面**：那一次撞名恰好是对的'
           '（原作对同一个人有两种写法），硬拦它的唯一出路是撤销一个对的决定。'
           '⇒ 从那扇门进来的由 `rn_new_collide_human` 报。' + _HUMAN_FIX)

# 【人裁决过的那些另立一条】同 `rn_shared_surface_human` 的形状。
_info('rn_new_collide_human', '人手补的映射撞上别人的新名时，报出它撞上了谁', 'R-10.13b',
      note='**F2 新增**（`stage7.md` 事实⑤）。`extra_subs` 恰恰是唯一一条'
           '**人手打新名、且没有模型的全局视野**的通道 ⇒ 撞名在这里最可能发生，'
           '而上面那条 HARD 结构上够不到这扇门。'
           '⚠ 它是提示不是门：盘上那一次撞名**是对的**，'
           '「有人看过并决定这么做」与「没人看过」必须分得开')

_hard('rn_ambiguous', '每个旧称呼只映射到一个新称呼', 'R-10.13',
      note='`R-10.13④`。⚠ 实测触发过一次：registry 的 `address` 重复收了 canonical，'
           '模型于是同时交出 `new_canonical` 与一条同名 alias，被判成「一旧名两新名」。'
           '⇒ **canonical 不得在 `aliases` 里再列一遍**。' + _HUMAN_FIX)

# 「书童」实测同时挂三个 char_id、「姐姐」挂两个——称呼→角色是**多对多**。
_hard('rn_shared_surface', '被替换的称呼没有同时挂在多个角色身上', 'R-10.3d',
      note='三道防线之③。换掉一个多主称呼＝顺手换掉了别人的称呼。' + _HUMAN_FIX)

# 【人裁决过的那些另立一条】多主有一类是**情节本身**（顶名 / 替身 / 女扮男装）：
# 某本 `谢昭阳` 既是 char_001 的 canonical、又是 char_002 从 ch12 起的 address。
# 那种书里「不换」＝主角真名进成书，而旧口径下 hard + `--approve` 拒绝定稿 +
# `extra_subs` 也过不了这道门 ⇒ **整本书没有任何一条路能出成书。**
_info('rn_shared_surface_human', '多主称呼只在人明写时才替换，且报出它还碰到了谁', 'R-10.3b',
      note='这条否决防的是**静默**误伤，不是「多主一律不能换」。'
           '「有人看过并决定这么做」与「没人看过」必须分得开')

_hard('rn_surface_short', '要替换的称呼都不短于两个字', 'R-10.3c',
      note='三道防线之②，阈值 `invariants.MIN_SURFACE`。'
           '⚠ 它管**替换面**不管**屏蔽面**——`kept` 里有单字条目是对的。' + _HUMAN_FIX)

# 【为什么这条只查【替换目标】，另立一条 info 查原文】
# 原文不参与组装，新名撞上它不会破坏任何东西，硬拦它就是把合法情况排除在外。
_hard('rn_already_present', '每个新称呼在替换目标（成稿+文案+书名）里出现 0 次', 'R-10.13',
      note='`R-10.13⑤` 的前一半。撞上了就是把两个人写成同一个人。' + _HUMAN_FIX)

_info('rn_new_in_source', '每个新称呼在原作正文里也没有出现过', 'R-10.13',
      note='`R-10.13⑤` 的后一半。**原文不参与组装** ⇒ 这里只提示不阻断。' + _HUMAN_FIX)

_hard('rn_title_dropped', '称谓与亲属后缀在新形里原样保留（职位与关系不变）', 'R-10.13',
      note='`R-10.13⑥`。「许总」→「林总」合格，「许总」→「林昭」把职位丢了。'
           '⚠ 它是「关系与职位不变」唯一能确定性验的**一半**：另一半（排行）'
           '实测漏过——模型把「七郎」改成「六郎」，后缀还在，本条全绿，'
           '而人物排行已经被改写。' + _HUMAN_FIX)

_hard('rn_semantic_marker_changed', '数字、排行、辈分、关系与官职语义标记原样保留',
      'R-10.12b',
      note='比较改名前后的结构化 marker，而不是名字相似度：阿九→玄九合格、阿九→阿七失败；'
           '七郎→六郎失败；李将军→沈将军合格。新增一个原本没有的职位也会出声。'
           + _HUMAN_FIX)

_hard('rn_kinship_touched', '通用亲属/敬称一个都没被替换', 'R-10.5',
      note='⚠ 判定走 `core.naming.is_generic_appellation`，而它用**本书的姓氏表**'
           '豁免——亲属字表里的 `孙`/`公`/`子`/`女`/`母` 同时是常见姓氏。'
           '本条曾误判某本的 `孙大娘`（那本 `孙` 在姓氏映射里、「孙子」原文 0 次），'
           '**按它去修数据会让一个原作的姓氏活着进入「原创」成品**。' + _HUMAN_FIX)

_hard('rn_surname_split', '同一姓氏族群的成员共用同一个新姓', 'R-10.4',
      note='族群判据只有「字面同姓且 ≥2 人」（关系边那条已删：它只造成错误合并）。'
           '⚠ 它曾**恒不触发**：调用点漏声明 `surname_map` 是必需键，缺席时查表全 `None`'
           '⇒ 候选集恒空。新骨架 `require` 从 `model_cls` 派生，结构上不可能再漏。'
           + _HUMAN_FIX)

_hard('rn_overlap', '人工补充的映射之间没有跨界重叠', 'R-10.13c',
      note='`a` 的真后缀 == `b` 的真前缀 ⇒ 单次扫描会切出半截残串。'
           '⚠ 只查人工补充那一侧：registry 来的键都是完整称呼，不会有这个形状。'
           + _HUMAN_FIX)

# ── 替换器的后置条件（红了＝代码坏了或产物是旧的，**不是数据坏了**）────────────

_hard('rn_residual_key', '替换后没有任何一条映射的键残留', 'R-10.2b',
      note='`R-10.2`（单次 alternation）的后置条件。'
           '⚠ 判定**不许用 `str.count`**：走 `subs.apply_subs` 返回的 `claimed` 区间'
           '（**含恒等条目认领的那一半**）。某个被映射的称呼是保留称呼的子串时'
           '（「阿禾」被换、「阿禾姊姊」被留），保留下来的那段里必然还有它——'
           '**那不是残留，是那条保留决定的直接后果**。' + _HUMAN_FIX)

# ── 组装的前置条件（红了一律**拒写 txt**：这是交付物，不是审阅稿）──────────────
#
# ⚠ 原型的 markdown 组装器对缺章只在抬头写一条横幅——**txt 里没有地方写横幅**。

_hard('rn_book_incomplete', '每一章都有且只有一份成稿', 'R-10.14', owner='stage5',
      note='⚠ owner 是 `stage5`：缺章的根因在写作那一层，不在映射表 ⇒ '
           '**它不在那 13 条「人能 0 成本改掉」里**，人改映射表救不了它')

_hard('rn_txt_stale', '盘上的 book.txt 与产物记的指纹一致', 'R-10.9',
      note='⚠ 原型这一条**只在复检里现算、从不进 `book[]`**，而收尾循环给'
           '「没开过口的 hard/info」补打 `[OK]` ⇒ 同一份报告里会**同时**出现'
           '`[FAIL] 不符` 与 `[OK] 一致`。新骨架 `build()` 遍历注册表，'
           '**这条结构上不可能再发生**。' + _HUMAN_FIX)

# 「排除了 3 份废稿」与「一份废稿都没有」必须分得开。
_info('rn_invalid_drafts', '没有被拦下的废稿需要排除', 'R-10.14b', owner='stage5')

# ── 漏网：**本节点唯一能发现「白名单不够」的东西** ─────────────────────────────
#
# ⚠⚠ 原型这一组下面并列着两条，而其中一条**全仓 0 行实现**（见文件末尾的退役登记）。
#    读的人会以为漏网有两道防线，**其实只有一道**。这条注释是那次误导的解药。

_info('rn_residual_stem', '旧姓字在成稿里没有替换面之外的残留', 'R-10.6c', owner='stage2',
      note='⚠ owner 是 `stage2` 不是 stage7：根因在**人物表收得够不够**。'
           '盘上 **3/3 命中**——它是本层唯一一条真的在报东西的 hard/info。'
           '⚠ 报出来的**只是候选**：剩余里必然混着固有假阳（「许久」「谈话」），'
           '靠调阈值消不掉（中文没有词边界）⇒ 它**永远是 info**，不许拦住交付。'
           '⚠ 覆盖率与 `confidence` 打架时以后者为准：比率有自我压低的反馈回路')

_info('rn_no_reason', '每个不改名的角色都说明了为什么不改', 'R-10.13',
      note='`R-10.13①` 的另一半。⚠ 只查 `renamed=false` 那一侧；'
           '`renamed=true` 时盘上同时存在 `null`（9 个）与 `\'\'`（23 个）两种「没有」，'
           '**另一侧的两种拼法谁也没在看**——那一格由载体守。' + _HUMAN_FIX)

# 【这条是 `rn_residual_key` 让出来的位置】把「保留串里的子串」从残留里剔除是对的
# （那是保留决定的直接后果，不是替换器坏了），但那个不一致**本身**是真问题：
# 「阿禾」换成「阿舒」而「阿禾姊姊」原样留着 ⇒ 成品里半改不改。没有这一条就没人管它。
_info('rn_kept_contains_mapped', '保留下来的称呼里没有夹着已经改掉的名字', 'R-10.13d',
      note=_HUMAN_FIX)

# ── 恒等基线（`R-10.7` / `R-10.7b`，F2 新增）─────────────────────────────────

_info('rn_identity_baseline', '恒等基线跑过，而且逐字节无损', 'R-10.7',
      note='⚠ **不做成 HARD**：HARD 的 `skipped` 进 `fail`，而盘上三份产物的 '
           '`identity` 都还是 `null`（那**不是迁移没填，是实况**：原型只 `print`）'
           '⇒ 做成 HARD 会让三本全红，而它们并没有任何证据表明替换器坏了。'
           '**「没跑过」要出声，但不该阻断。**'
           '⚠ 结论里必须带**量了几章**：`roundtrip_ok=True` 而 `chapters=0` 是一句空话')

_unver('rn_identity_blind', '恒等基线验不了的三样已经与它的结论印在一起', 'R-10.7b',
       who_checks='HUMAN',
       note='取值面 `invariants.IDENTITY_BLIND`。⚠ 它是**常量不是现算**，'
            '而它必须跟结论存在一起——分开放，下一个人就会把基线跑绿了'
            '读成「替换是安全的」。那三样由 pytest 的对抗性合成映射覆盖，'
            '**不由这条 CLI**')

# ── 三条度量（永远输出，不是告警；度量不许挂在「没问题」那一支上）────────────────

_metric('rn_stem_noise', '新姓字在原文里另有多少次非人名用法', 'R-10.17',
        note='⚠⚠ **取值面与原型不同，而这是被实测逼出来的。** 原型取 `subs` 的'
             '**新值的第一个字**，假定它是新姓——三本上这个假设全破：'
             '`姓苏的→姓容的` 取到 `姓`(3 次) · `东街张媒婆→东街薛媒婆` 取到 `东`(**12 次**) · '
             '`胭脂铺赵婶→胭脂铺程婶` 取到 `胭`(3 次)。于是报告写着'
             '「新姓字在原文里的非人名用法次数：`{\'东\': 12, …}`」——**`东` 与任何新姓都无关**。'
             '⇒ 取值面改成 `surname_map` 的**值**（那才是新姓），'
             '叠字小名与描述性称呼不进这个统计。'
             '**名字先于证据定下来，读报告的人就会按这个名字去解释数字。**')

_metric('rn_report', '替换总账：几条映射、换了几处、篇幅变化、几个族群多少人', 'R-10.17',
        note='⚠ `applied.chapters{*}.chars_before/after` 与 `_groups` 已被新契约裁掉'
             '（唯一读者就是本条，而替换不改篇幅：3/3 都是 1.000×~1.001×）'
             '⇒ 这几个数**一律现算**，不许读产物')

_metric('rn_style_report', '成书写法密度（正文／文案／原作三列）', 'R-10.17',
        note='⚠ **「文案」那一列全链只有这里量**——逐章判定够不到它，'
             '因为文案不属于任何一章。'
             '⚠⚠ 原型这一条的「原作」列**永远不输出**：它读 `data["_source_text"]`，'
             '而装载器从来没设过这个键（实际存在的是 `_orig`，而且是 str 不是 dict）'
             '⇒ 那一列恒空而没有任何东西出声。F2 修掉。'
             '⚠ **文案那一列的密度不稳，因为分母只有几十到两百字**：实测三本新文案'
             '各含 1 处破折号（原作文案 5/5 都是 0 处），密度显示成语料 p95 的 2~4 倍，'
             '而绝对量就是一处。**读这一列看绝对处数，不看倍率** ⇒ 永远不给它阈值')

# ── 七条查不了的：**永远输出**，不是「还没写完」 ─────────────────────────────
#
# 这 7 条与 16 条 HARD 加在一起才是本层真实的防线图：**能查的都查了，查不了的写在脸上。**
# 省略掉，读报告的人会默认它们守住了。

_unver('rn_taste_unver', '新名的时代感与雅俗合适', 'R-10.11', who_checks='HUMAN',
       note='`R-10.11` 那一格是 `人`（见 `B-22`）——**现役闸门是人定稿**。'
            '没有结构不变量：一个名字「串不串味」量不出来')

_unver('rn_coverage_unver', '替换面覆盖了正文里所有指称人物的字面', 'R-10.18', who_checks='HUMAN',
       note='⚠ **`R-10.10` 原样保留「无守卫 → `B-21`」**，本条指的是新补的 `R-10.18`'
            '（「这件事查不了」本身要常驻列出）——同 stage6 补 `R-9.18` 那一笔。'
            '够不着的理由是结构性的：只能替换人物表里有的称呼，'
            '**人物表漏掉的角色这一层结构上够不着**。'
            '残留探针只是候选探针，**不是覆盖率证明**')

_unver('rn_title_unver', '书名里的人名替换正确', 'R-10.16', who_checks='HUMAN',
       note='⚠ `title_new` 已裁（现算 0 成本，盘上 3/3 与原书名逐字节相同）'
            '⇒ 本条报的是**现算**的结果与换了几处。'
            '⚠ 三本书名都不含 registry 专名 ⇒ 这一条**从来没有过失败样本**，'
            '只能常驻报出，不能当绿灯读')

# ── 判定本身的前提可能不成立 ───────────────────────────────────────────────

_unver('rn_not_approved', '有人给这份人物表定过稿', 'R-10.11', who_checks='HUMAN',
       note='本条由复检产出，不落产物。⚠ 与 stage6 不同：本层的定稿是'
            '**给整份人物表盖章**，没有候选序号——`stage7.approve` 不带参数。'
            '盘上三本都已定稿 ⇒ 0 命中')

_unver('rn_body_stale', '判的是照盘上这些成稿做的替换', 'R-10.9', who_checks='INFO',
       note='⚠ 口径同 stage6 的 `hook_body_stale`（两层同形，合成一套）：'
            '`judged[].consumed`（字段投影）判过期、整文件 `sha1` **只作审计**；'
            '**没有 `consumed` ⇒ 说不清**，不是 fresh 也不是 stale。'
            '`sha1` 是整文件指纹，而新契约裁过 stage5 产物的字段 ⇒ 迁移后它必然对不上')

_unver('rn_hook_missing', '开篇文案已定稿，可以拼进成书', 'R-10.14', who_checks='INFO',
       note='⚠ **根因第一嫌疑层其实是 stage6**（文案没定稿是上一层的账），'
            '而 `owner` 仍然留 `None` —— 全链的口径是 **`owner is None` ⟺ 它不是一条结论**'
            '（`metric`/`unver` 恒 `None`，由规则注册器统一校验）。'
            '给一条 `unver` 填 owner 会让那条口径出现第一个例外，而它买不到任何东西。'
            '它是 `R-10.14` 四类拒写里的一类，而**正因为这道闸挡在前面**，'
            '`book_txt.has_hook=false` 结构上不可达 ⇒ 那个字段已按待决④ 裁掉')
