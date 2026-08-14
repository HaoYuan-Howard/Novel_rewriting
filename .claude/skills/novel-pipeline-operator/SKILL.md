---
name: novel-pipeline-operator
description: 驱动 novel.cli 把一本中文小说从原始 txt 端到端跑成 book.txt 的操作规程：逐节点命令序列、付费确认纪律、成败判定、断点续跑、修复路由、闸门代批与终审包汇编。适用于任何 agent；agent 无产物直接编辑权（写路径只有 stage 生成节点 / 修复机制 / --apply-edit 决策文件），人只在付费授权与终审处出现。
---

# novel-pipeline-operator —— 端到端跑书操作规程

> v1（2026-08-15，经书1《前任赘夫逃跑后》全链退火）。
> 2026-08-15 用户裁决：全部人工闸门可交 agent 代批，用户只审最终成书
> （终审包 + book.txt）。**每次代批的依据与判据读数必须逐条进终审包。**

## §1 环境与不变量

- Python 环境：conda env `novel_refactor`（**不是 base**）。命令一律
  `D:/Miniconda/envs/novel_refactor/python.exe -X utf8 -u -m novel.cli <子命令> …`
  （`conda run -n novel_refactor python -X utf8 -u -m novel.cli …` 等价，但多行 `-c`
  内联脚本会被 conda 包装器弄坏——脚本一律直接用环境里的 python.exe）。
- **cwd 必须在仓根**（`-m novel.cli` 吃 cwd）；`--base` 恒用**绝对路径**。
- UTF-8 由 `-X utf8` + CLI 入口重配兜住，不要再包编码转换。
- ⚠⚠ **管道会吃退出码**：`cmd | tail` 后 `$?` 是 tail 的。要退出码就
  `cmd > /tmp/out.txt 2>&1; echo rc=$?` 再看文件，或用 `${PIPESTATUS[0]}`。
- 长节点后台执行（agent 前台命令有约 10 分钟上限）。实测短书（≈1.2 万字 19 章）
  每个节点都在 10 分钟内（见 §3 耗时列），但 stage2/stage5 随书变长，保守全后台。
  **一次只跑一个写盘节点，绝不并行两本书**（账本/缓存写交错）。
- 测试语料开关（只在跑 pytest 时需要）：`NOVEL_OUTPUT=<现行契约产物目录>`；
  不设则语料测试整批 skip（skip≠绿）。
- 输出目录纪律：**永不写历史证据目录**（`outputs/e2e_20260813/`、
  `outputs/smoke_v2_20260814/` 等）；新一轮跑书另开 `outputs/<run名>_<日期>/`，
  多本书**共用同一个 base**（inbox/state/账本的多书面貌只有共用才真实）。

## §2 开跑前检查清单

1. `.env` 里 `DEEPSEEK_API_KEY` 在位（**不打印值**，`grep -c '^DEEPSEEK_API_KEY=sk-' .env`）。
2. `.llm_cache/` 存在（付费中断后同参数重跑的免费保险）。
3. 源 txt 在位；sid 会从文件名尾部 `_数字` 推导。
4. base 目录：新 run 用新目录；断点续跑用原目录（见 §6）。
5. 付费授权状态明确（见 §4）。

## §3 逐节点命令序列（14 步）

`$RUN` = `D:/Miniconda/envs/novel_refactor/python.exe -X utf8 -u -m novel.cli`，
`$B` = base 绝对路径。**每步先 `preview` 拿两步计划再照跑**（preview 与真跑共用
`build_argv`，R-11.8——不凭记忆拼命令）：

```
$RUN preview <node> <sid> [flags] --base $B
```

⚠ CLI `run` 只执行单步；preview 里的第二步 `--check-only` 要**自己补跑**。

| # | 节点与命令 | 贵/免 | 实测耗时* | 跑完补跑 | 成功判据 |
|---|---|---|---|---|---|
| 1 | `run stage0.remove --source <txt> --base $B`（省略 sid） | 免 | 秒级 | `stage0.remove <sid> --check-only` | run exit 0；**check 在批准前必然 exit 1**（s0_approval HARD 命中 pending 属设计）——判据是除 s0_approval 外全 OK |
| 2 | `run stage0.approve <sid> --base $B` | 免 | 秒级 | — | **代批闸门**：先读剔除报告——剔除段逐段核语义、正文保留率、HARD 全 OK 才批；依据进终审包 |
| 3 | `run stage1.partition <sid> --base $B` | 免 | 秒级 | `--check-only` | exit 0；重号/缺号读 `s1_chapter_labels` 的 scanned 行（§11.1） |
| 4 | `run stage2.global <sid> --full-extract --base $B --yes` | 付 | ~9 分钟 | blueprint 与 style 两条 `--check-only` | exit 0 + 两段 badge 无 HARD |
| 5 | `run stage4.plan <sid> --base $B --yes` | 付 | ~2 分钟 | `--check-only` | exit 0；flat_chapter 等 info 命中属常态 |
| 6 | `run stage5.write <sid> --deep 1,2,3 --shallow-rest --base $B --yes` | 付 | ~5 分钟 | — | exit 0；deep+shallow 章数=源章数；内部重试属正常（§11.4） |
| 7 | `run stage5.review <sid> --base $B` | 免 | 秒级 | — | 全 HARD OK；风格漂移族 info 命中属常态 |
| 8 | `run stage5.semantic <sid> --base $B --yes` | 付 | ~8 分钟 | — | exit 0；review 里 semantic_secret_leak 由 unver 转判定 |
| 9 | `run stage6.hook <sid> --base $B --yes` | 付 | ~2 分钟 | `--check-only` | exit 0；产 3 候选 |
| 10 | `run stage6.semantic <sid> --base $B --yes` | 付 | ~8 分钟 | — | exit 0；**候选泄底会出 HARD——这不拦定稿**（§9.1） |
| 11 | `run stage6.approve <sid> --pick N --base $B` | 免 | 秒级 | — | **代批**：判据序见 §9.1，理由进终审包 |
| 12 | `run stage7.rename <sid> --base $B --yes` | 付 | ~2 分钟 | `--check-only` | exit 0 落表；**exit 2 + `.INVALID` = 表被 HARD 拦下，走 §11.2** |
| 13 | `run stage7.approve <sid> --base $B`（无参数） | 免 | 秒级 | — | **代批**：HARD 全 OK + 新旧对照/漏网叫法无未解释项；依据进终审包 |
| 14 | `run stage7.assemble <sid> --base $B` | 免 | 秒级 | — | exit 0；`stage7/book.txt` 落盘，做 §5.4 成书三查 |

*实测基准：1.2 万字 19 章短书、32 并发、v2 上下文；全链含一次修复回路约 55 分钟。
章数翻倍者按线性放大预估并全部后台。

随时可用读屏：`state <sid> --base $B`（跑到哪了）、`inbox <sid> --base $B`（待人工六类清单）。

## §4 付费确认纪律

- 默认纪律（Q-10/R-11.8/R-13.3）：**花钱前必须用户确认**。标准形态 = 开跑前用
  preview 把该书全部付费命令摊开，一次性请用户预确认；此后逐条执行不再打断。
- 用户可给出**显式总授权**（如「本 session 默认允许付费调用」）——有总授权即不再问，
  但每条付费命令仍逐条留痕（账本自动记）。**没有明说的授权不存在**：
  「不知道」必须倒向「要问」。
- `--yes` 的语义是「这一次会花钱且已获确认」，不是万能跳过；免费跑法不用带。
- **不显示预估花费**——不编造金额、不换算价格；只报调用次数 / token / 耗时（Q-10）。

## §5 成败判定

**五值退出码**（唯一落点 `novel/cli/exit_codes.py`）：

| exit | 含义 | 下一步 |
|---|---|---|
| 0 | 做完了 | 补跑 check 步看 badge；**exit 0 ≠ 无命中**（info 命中照样 0） |
| 1 | 跑完了，判定不通过（badge=fail） | 读 HARD 明细，走 §7 修复路由 |
| 2 | 显式终态：按设计走不下去（不是 bug，无栈） | 照报错原文处置（缺上游/未批准/HARD 拦表…） |
| 3 | 结构性拒绝：代码自相矛盾（是 bug，有栈） | 停下报告用户，不重试 |
| 70 | 未捕获异常：崩了，**结论未知**（是 bug，有栈） | 先 `state` 判位置再决定（§11.5） |

1. **第二段 `--check-only` 报告的 badge 是唯一判官**——exit 0 只说明进程善终，
   残缺产物也能 exit 0（F-087）。
2. 判「这层过没过」只看 **HARD**：info/metric 命中与 unver 是常态读数不是失败。
3. **ok / unver / skipped 三态分开读，绿灯必须带分母**：分母为 0 或全 unver 的「0 问题」不是绿。
4. 成书三查：book.txt 字数/章数与源书同量级（比值应 ≈1.0）；抽查旧名零残留
   （旧姓+旧名逐个 `count`）；开篇是已定稿文案。

## §6 断点续跑

`state <sid> --base $B` 输出逐层逐产物的**四态**：

| 态 | 含义 | 动作 |
|---|---|---|
| `skip`（✓ fresh/match） | 干净且新鲜 | 跳过 |
| `run`（无产物） | 该跑还没跑 | 照 §3 跑 |
| `redo`（stale） | 在盘但过期 | **停下问用户**——重来=重新付费（有总授权时可自行判断，判断依据进终审包） |
| `unknown`（?） | 说不清 | **停下问用户**（同上） |

- 中断后从 state 找第一个非 skip 节点接续；`.llm_cache/` 让同参数重跑实付 0。
- ⚠ 已知诊断噪声：`stage4/writer_context.json` 常挂 `stale/consumed_changed`
  而 provenance 里 file 指纹与盘上逐一相同（BE-06 族）——不阻塞、不影响成书，
  **不要**因它重跑 stage4；记终审包即可。

## §7 修复路由（四类处置）

| remediation | 谁修 | 路由 |
|---|---|---|
| deterministic | 代码 | 报告里 `fix` 指的免费 (节点,flag)，直接跑 |
| llm | 写作层 | 仅 `repair=='UNIQUE'` 且轮次 < STOP_REWRITE_AFTER(=2)：`stage5.write --revise`（付费）；连续 2 轮未消自动转 human |
| human | 人/受托 agent | `inbox <sid>` 看六类清单；0 成本口：`--apply-edit`（决策文件）/ `--pick` / `--wontfix`（stage5.review，豁免不删结论） |
| none | 无 | metric 读数，无需处置 |

免费修复 flag 速查：`--check-only`（复检不写盘）· `--revalidate`（`.INVALID` 改好后提升）·
`--recheck`（刷指标重拼 draft）· `--apply-edit <文件>`（人产决策落新一轮，旧轮归档）。

inbox 六类：invalid（首跑被拦）/ rejected（自救被驳，带 `_rounds/` 指针）/ stalled（2 轮未消转人工）/
human_only（筛子级告警，几十条属常态，无一阻断）/ upstream（根因在别层）/ approval（闸门未盖章）。
**unver 永不入列；筛子级条目可留给终审裁量，不必逐条处理。**

## §8 闸门代批

`HUMAN_NODES` = 全部 `*.approve` + `stage0.edit`。有用户的委托裁决时由 agent 代批，
纪律三条：

1. **先读数后盖章**：stage0 看剔除报告（剔除段逐段核语义、保留率、HARD）；
   stage6 看两份（机械+语义）候选判据；stage7 看 HARD 与新旧对照。
2. **每次代批的依据、判据读数、被否决项的缺陷逐条进终审包**——用户终审可整体推翻。
3. 没有委托时照原样停机等人，一字不代批。

## §9 代批判据序

### 9.1 stage6.approve --pick N

① 只在**零 HARD 命中**的候选里选（`hook_semantic_spoiler` 判到谁谁出局）；
② 并列时取 info 命中最少者；③ 仍并列按与 blueprint 开篇钩子的一致性择优，**写出理由**。
⚠ 泄底判据按拍板判**全部**候选：落选候选的 HARD 残红留在报告里**不拦定稿也不拦成书**
（assemble 只拦「未定稿」）；清红是可选项——`stage6.hook --apply-edit` 改写泄底候选后
重跑 `stage6.semantic`——记终审包待人裁，不默认执行。

### 9.2 stage7.approve

前提：本层 HARD 全 OK + 逐条核对新旧映射与「漏网叫法候选」无未解释项。
`rn_residual_stem`（info）报旧姓字在「四周/方才」类通用词里的残留属常态，记录即可。
⚠⚠ **`rn_kept_contains_mapped`（info）命中必须先处理再批**（书2 实例）：保留串里夹着
已改名的名字（如「柔姨娘的兄长」renamed=false 而「柔姨娘→妍姨娘」），保留串会护住
内嵌旧名**直接进成品**。修法：`character_patches` 把该角色 renamed=true、new_canonical
做内嵌名同步展开（关系词逐字不动）——kinship 判据的逃逸门（内嵌名展开）天然豁免它。
成书三查随之加一项：**新旧名混名扫描**（旧 canonical/别名逐个 count 必须为 0）。

## §10 终审包汇编模板

每本书一份 `final_review_<sid>.md`，落 **base 根**（不进书目录、不进 `_service/`）。
七节：①总览（链路一句话、book.txt 字数/章数 vs 源书、退出码序列、state 异常边说明）
②各层校验读数表（badge 带分母、HARD 明细、wontfix 计数）③待人工余额（inbox 首行照印+分类摘要）
④agent 代批决策（每闸门：依据、读数、被否决项缺陷、残红说明）⑤账本事实读数
（按 sid 过滤 `_service/ledger.jsonl`，字段 `node/cached/prompt_tokens/completion_tokens/reasoning_tokens`，
过滤 `outcome=='ok'`；表列=节点|实付|缓存命中|prompt|completion(reasoning)；**不写金额**）
⑥待人工事项（出处+恢复路径命令）⑦指针（产物目录、`_rounds/`、人产输入文件、账本）。

## §11 异常路径手册（书1 实战全案）

1. **重号/缺号章**：`s1_chapter_labels` 的 scanned 行报「N 个原始章号 · 重号 [x] · 缺号 [y]」；
   同号 < NOISY_DUPLICATE_MIN(=3) 次不触发告警，stage1 自产修正序 1..N——**读法不是修法**，
   确认下游 chapter_ordinal 唯一即放行；≥3 次会出 noisy_duplicate 警告（疑似固定标记误判成章号），需人工核对原文标记规律。
2. **付费节点 exit 2 + `.INVALID`**（书1 实例 `rn_kinship_touched`）：HARD 拦表不落正式稿。
   处置模板：读 `.INVALID` 里的 evidence → 复算判据定位条目（判据函数可直接 import 复跑）→
   若是**判据保守面误报**（如角色名简称首字撞亲属字表），修法 = ①正文里该字面的裸用先用
   `stage5.write --mode <臂> --apply-edit <chNN.txt>` 改掉（编辑稿从**文件名**认章号，给**全章全文**）；
   ②`stage7.rename --apply-edit <书内 stage7/name_map_edit.json>`，用 `character_patches`
   把该别名改为 old==new（保留原样，**不要删条目**——删了裸用字面就残留进成品）；
   ③重跑本层 check 确认过闸。⚠ 编辑正文后 stage5 语义按 sha1 过期成 unver ⇒ 重跑
   `stage5.semantic`（未动的章命中缓存，实付只有改动章+对齐相）。若改的是 ch1-3 还会
   过期 stage6 的 hook_body_stale ⇒ 视情重跑 stage6.semantic。
3. **钱花了没产物**（账本先写产物后写，崩在中间不自愈）：**同参数原命令重跑**，
   `.llm_cache/` 命中 ⇒ 实付 0。**禁改参数**（换参数=缓存失效=二次付费）。
4. **stage5.write 调用数 > 章数**属正常（写作重试+长度收缩，按 2× 章数打底预算）。
5. **exit 70**：崩了、结论未知。先 `state` 判位置，产物完整则继续下一节点，
   残缺则同参数重跑；两次同点崩 ⇒ 停下报告用户。
6. **连接类错误**（账本 error_kind 可查）：退避后同命令重试；设计内已有 4 次重试阶梯。
7. **stage6 语义 HARD 残红**（落选候选泄底）：见 §9.1——不是故障，是拍板设计。
8. **`--full-extract` 中途被落盘闸门拦下**（书2 实例 `secret_setup_order`）：global 落
   `.INVALID` 时 blueprint/style 压根没跑（两条 check 均 exit 2「不在盘上」）。处置：
   ①看判据有无 `fix`——`(hard · mechanical/deterministic)` 一族直接跑它指的免费路
   （如 `stage2.global --renormalize`）；②修复过闸后**分步补跑剩余节点**
   `stage2.blueprint --yes` → `stage3.style --yes`（各自补 check），**不要重跑
   `--full-extract`**（global 已是正式稿，整跑是重复付费面）。
9. **stage4 `style_words` 拦表**（书2 实例）：计划文本混进风格词（「张力」等，词表
   `invariants.STYLE_WORDS`）。终态原文直接指路：**手改 `.INVALID` 那几行**（改成内容
   语义等价、不含风格词的说法）→ `stage4.plan --revalidate` 提升（0 成本，不必再付费）。
   ⚠ 各层人工路不同形：stage2/4 = 直接改 `.INVALID` 后 `--revalidate`；
   stage5 = 全章编辑稿 `--apply-edit <chNN.txt>`；stage7 = 决策文件
   `--apply-edit stage7/name_map_edit.json`——**照终态/inbox 的 how 行走，别混用**。
   ⚠⚠ stage4 `--revalidate` 提升后**不会**同批重建 writer_context（run_plan 才会）——
   开写前先免费跑 `stage4.context <sid>`，否则写手吃的是旧 context（书2 实测：
   delta 仅一个词时判据仍全绿，但这是运气不是设计）。
10. **semantic 泄底红行的根因两分**（书2 实例 `semantic_secret_leak`）：先回**源书对应章**
    核证据——(a) 写手真把后文内容写进了前章 ⇒ 走 llm 修复（`--revise`，≤2 轮）或人工改稿；
    (b) **源书该章原文本就含此揭示**（判据证据与源文逐句对得上）⇒ 根因是 stage2 把
    `reader_reveal_events` 章标晚了，**不改写手稿**。⚠ (b) 当前无法收口：语义结论不落
    review 产物 ⇒ `--wontfix` 挂不上（「不给不存在的结论发豁免」）；stage2 secret 的人工
    裁决口（旧 `--drop`）已删。诚实处置 = 残红如实保留 + 终审包记根因 + REQUIREMENTS 登记。
11. **semantic 之后不要无谓重跑 `stage5.review`**：semantic 产物按
    `mechanical_review_seq` 绑定当时最新的 review——多跑一次 review 就顶掉绑定，
    语义行退回 unver，只能再付费重跑 semantic 重绑（claims 全缓存、实付仅对齐相，
    但纯属浪费）。review 只在**正文变了**之后跑。
12. **修法由 finding 原文分流**（书2 实例 `hinge_anchor_void`+`verbatim_failed`）：
   同是 stage2 落盘拦下，锚点类失败按**根因四分流**，判据文本会直接说哪条路——
   「锚点不在本章原文（是转述或编的）」= 根因在模型，**明写不要 `--renormalize`**，
   正确修法是定向补章 `stage2.blueprint --chapters <N> --yes`（付费，强制刷缓存，
   一次重生成该章并整表重判落盘）。**先读 finding 原文再选修法，不背对照表。**

## §12 禁止事项

- 不直接编辑任何产物 JSON/txt——写路径只有 stage 生成节点、修复机制、`--apply-edit`
  决策文件（stage7 的编辑文件**必须**落书内 `stage7/name_map_edit.json`；stage5 编辑稿
  路径任意但文件名必须含 `chNN`）。
- 不往历史证据目录写一个字节；不复用历史 run 的 base。
- 不编造金额、不换算价格；成本只报次数/token/耗时。
- 不把 unver/skipped 洗成 ok；绿灯必须带分母。
- 不绕付费确认；没有明说的授权不存在。
- `redo`/`unknown` 不自作主张重跑付费节点（除非有总授权且把判断依据写进终审包）。
- 两本书不并行；一个写盘节点没封口不开下一个。
