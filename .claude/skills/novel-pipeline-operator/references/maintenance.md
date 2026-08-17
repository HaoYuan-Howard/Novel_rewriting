# 维护协议

## 目标

把本 skill 当作有接口、实现模块、知识库、模板和测试的软件维护。Git 负责回答“何时改过”；skill 文件只保留当前有效行为。

## 文件职责

| 文件 | 负责 | 不负责 |
|---|---|---|
| `SKILL.md` | 触发说明、输入/输出、稳定主循环、权限边界、完成定义 | 节点细节、事故史、宿主版本、日期日志 |
| `workflow.md` | 当前 CLI 工作流和状态解释 | 个案修复、审美裁决 |
| `delegated-review.md` | 闸门政策、候选选择、成书对账、终审包 | CLI 注册表 |
| `recovery.md` | 稳定修复状态机和正式人工写入面 | 未验证经验 |
| `runtime-profiles.md` | 宿主差异 | 流水线质量判据 |
| `field-notes.md` | 经验证或明确标态的经验 | 稳定公共接口、流水账 |
| `assets/final-review-template.md` | 用户最终审阅界面 | 运行逻辑 |
| `scripts/validate_skill.py` | 结构、链接、节点顺序和经验格式的漂移检查 | 代替真实跑书 |

所有 reference 必须由 `SKILL.md` 直接链接，保持一层渐进披露。不要增加 README、CHANGELOG、quick reference 或同义副本。

## 事实来源

遇到冲突时先辨别问题类型：

- 产品语义与用户体验：用户裁决和现行产品契约优先；改变它们前先取得决定。
- 可执行能力：当前代码、schema、CLI preview 和测试优先；文档声称存在但代码不可达时标为缺口。
- 单次行为：原始产物、报告、日志、缓存键和复现步骤优先；不能用回忆替代。
- 经验推断：永远低于上述事实，必须带状态与验证方法。

不要把现有代码自动当成正确，也不要把旧 E2E 文档自动当成当前事实。发现冲突时先复现，再决定修代码、修文档或请求产品裁决。

## 变更分类

按变更影响更新最小完整集合：

| 变更 | 必改 | 同步检查 |
|---|---|---|
| 输入、授权、代批范围、终态、交付物 | `SKILL.md` | headless prompt、API/UI 文案、测试；语义变化先裁决 |
| 节点顺序、默认主跑参数、follow-check | `workflow.md` | `PIPELINE_STEPS`、`NODES`、CLI tests、校验脚本 |
| 闸门判据或候选选择 | `delegated-review.md` | report/schema、终审模板、真实失败样本 |
| 修复入口或写入权限 | `recovery.md` | `NODES.flags`、终态文案、schema、真实消费路径 |
| 新宿主 | `runtime-profiles.md` | launcher 权限、等待/停止机制、日志与恢复测试 |
| 新实战现象 | `field-notes.md` | 先复现和标态，不立即改稳定契约 |
| 用户终审需求 | 模板 + `delegated-review.md` | 真实终审包是否一屏可读、事实是否可追溯 |

外部消费者不要引用 `§3`、`§10` 之类易漂移的章节号。引用 skill 名和资源职责，或直接要求读取 `SKILL.md` 的“按需加载资源”。

## 经验生命周期

新增经验时按以下顺序：

1. **捕获**：保留原始 finding、产物 sha1、命令、退出码和最小复现；不要先写结论。
2. **核证**：对照当前代码、测试和源文，区分 contract、实现缺口、宿主行为与偶发模型输出。
3. **泛化**：删除书名、日期、金额和一次性措辞，改写为“触发—判断—处置—验证”。
4. **标态**：只使用 `contract-confirmed`、`observed`、`open-gap`。
5. **去重**：先检索节点、rule id 和错误片段；补充既有条目优先于新增近义条目。
6. **晋升**：只有代码/测试已钉住且会影响所有运行的规则，才从经验库晋升到 workflow/recovery/review。
7. **退役**：实现修复并验证后删除过时条目，或把仍有价值的机制重写为 current contract；不要保留“已修复”墓碑。
   ⚠ **FN 编号不重排、不复用、退役即留空号**——与 `docs/REQUIREMENTS.md` 的 TODO 同一条纪律，
   理由也一样：`novel/`、`contracts/`、`tests/` 的注释在按号引用这些条目。复用一个号不会报错，
   它只会让读的人查到另一件事，然后按自己的猜测把文档读通。删除那一侧由“仓内引用必须存在”
   的断言兜住；**复用那一侧没有守卫，只能靠这条纪律**。

每条经验固定包含：

```text
### FN-NNN · 中性标题
- 状态：contract-confirmed | observed | open-gap
- 触发：可检索的节点/rule/错误
- 判断：如何区分相似根因
- 处置：正式入口与成本边界
- 验证：怎样证明收口
- 依据：代码、测试、缺口编号或原始证据类型
```

## 校验

每次修改后从仓根运行：

```text
<PY> -X utf8 .claude/skills/novel-pipeline-operator/scripts/validate_skill.py
<PY> <SKILL_CREATOR>/scripts/quick_validate.py .claude/skills/novel-pipeline-operator
```

项目校验器必须至少保证：

- frontmatter 只有 `name` 与 `description`；
- `SKILL.md` 无版本日志/日期残留且少于 500 行；
- 所有本地链接存在，所有 reference 均从 `SKILL.md` 直接可达；
- workflow 的节点顺序与 `PIPELINE_STEPS` 完全一致；
- 经验 ID 唯一且六个字段齐全；
- 终审模板的必需章节存在；
- headless 消费提示词不依赖已删除的章节编号；
- **行为断言**（这一组测的是「文档声称的 CLI 行为与代码是否一致」，结构检查测不到）：
  `INBOX_KIND` 的每一类在 `recovery.md` 与 `SKILL.md` 完成定义里都有交代；
  文档写出的每个双横线 flag 都在注册表或 CLI 选项里，**且**每个挂在节点上的 `PAID_FLAGS`
  都至少被 skill 提过一次（漏一条现役付费路径的后果是每本书白丢那一层）；
  `workflow.md`「结果判定」的退出码集合等于 `novel/cli/exit_codes.py`。

“同一个 flag 在不同层行为不同”这类事实**静态测不出来**，只能留在经验库并靠实跑复现。

随后运行与修改面相称的项目测试。只改文档仍需运行 skill 校验；改节点、flag、prompt 或 schema 时必须运行对应单测与真实 `preview`。

## 前向测试

重大改版后至少做两种无泄漏验证：

1. 给一个全新执行者 `SKILL.md` 路径、sid/base 和明确授权，让其仅凭 skill 说出下一安全动作与所需证据。
2. 给一个带 `.INVALID`、落选候选 HARD 或 stage7 二次编辑的原始状态，让其完成修复决策和终审包片段。

不要告诉验证者预期答案或旧缺陷。对照其实际读取的资源、命令、停机边界和产物；“验证者说文档没写”也要回查文件，不能直接堆入重复说明。
