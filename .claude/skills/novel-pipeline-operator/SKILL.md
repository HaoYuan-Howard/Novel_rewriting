---
name: novel-pipeline-operator
description: 作为受托终审代理，使用本仓 novel.cli 将单本中文小说从原始 txt 或既有 sid 可靠地推进到 stage7/book.txt，并把中间人工闸门、修复裁决、校验读数、授权边界和待审余额汇总为 final_review_{sid}.md。用于跑书、断点续跑、流水线修复、agent 代批以及“用户只审最终成稿”的任务；外部模型调用必须分别具备付费授权与正文外发授权，生成产物只能通过正式 CLI 节点和修复入口改变。
---

# Novel Pipeline Operator

把用户界面收敛为两份最终交付：成书与终审包。自行处理流水线内可委托的人工闸门，保留每次裁决的证据，不把中间正常审批重新抛给用户。

## 接口契约

接受以下输入；从仓库与盘上状态能确定的值自行解析，不重复询问。

| 输入 | 要求 |
|---|---|
| 书 | 原始 `.txt` 路径，或可在 `base` 内解析的既有 `sid` |
| `base` | 新跑使用新的输出根；续跑必须使用原输出根；始终传绝对路径 |
| 付费授权（`authorized`） | `granted` 或 `not_granted`；只控制是否可产生外部调用费用 |
| 正文外发授权（`egress_authorized`） | `granted` 或 `not_granted`；独立控制小说正文及派生上下文能否发送给外部模型；launcher/UI 默认 `granted`，但必须在 prompt 中显式写出 |
| 可选约束 | 用户指定的模型实验、章节深浅分配、停止条件或审阅偏好 |

把“使用本 skill 端到端跑书”视为对 `stage0.approve`、`stage6.approve`、`stage7.approve` 的标准代批委托；这项委托不包含付费授权或正文外发授权。用户缩小委托范围时遵从用户范围。

只产生以下用户交付接口：

- `<novel-dir>/stage7/book.txt`：最终成书，只能由 `stage7.assemble` 生成。
- `<base>/final_review_<sid>.md`：终审包，按 [终审包模板](assets/final-review-template.md) 填写。

使用三种终态，禁止用“命令跑完”冒充“书已完成”：

- `ready`：成书与全部完成条件满足，可直接交用户终审。
- `ready_with_disclosures`：成书可交付，但仍有不影响所选定稿的残红、不可验证项或已知实现缺口；逐项披露。
- `stopped`：安全地停在可恢复位置；终审包必须给出阻塞证据与精确恢复命令。

## 按需加载资源

开始任何跑书或续跑前，完整读取：

- [工作流契约](references/workflow.md)：现行命令协议、14 个主节点、成败判定与断点续跑。
- [代批与终审契约](references/delegated-review.md)：三个闸门的判据、成书对账与终审包要求。
- [运行环境](references/runtime-profiles.md)：只读取与当前宿主相符的交互式或 headless 小节。

遇到非 happy path 时再读取：

- [修复状态机](references/recovery.md)：退出码、`remediation`、允许写入面与停止条件。
- [经验库](references/field-notes.md)：先用节点名、rule id、退出码或报错片段检索，只读取命中的条目；经验不能覆盖当前 finding 与代码契约。

仅在维护本 skill 时读取 [维护协议](references/maintenance.md)，并运行 `scripts/validate_skill.py`。不要在普通跑书任务中加载维护说明。

## 执行主循环

1. 确认仓根、Python 解释器、书、绝对 `base`、两项授权和代批范围。不要打印密钥。
2. 对既有 `sid` 先运行 `state` 与 `inbox`；对新书先预览 `stage0.remove`。从盘上事实决定起点。
3. 在首个动作前创建终审包草稿，随每次授权判断、闸门裁决、修复与异常更新；不要等到最后凭记忆补写。
4. 对当前节点运行 `preview`，逐字执行它给出的全部步骤，包括自动追加的复检；不要凭文档手拼真实 argv。
5. 对每条实际命令和修复命令执行 `mark start`，取得真实子进程退出码后执行 `mark end --exit <rc>`；`preview` 不打点。
6. 一次只运行一个写盘节点。用退出码判断进程结果，用最新 check badge 与 finding 判断质量结果。
7. 命中闸门时按代批契约裁决；命中失败时按修复状态机收敛。每次决定同时记录证据、被否决方案和复验结果。
8. `stage7.assemble` 后执行独立成书对账，再刷新 `state`、`inbox`、活动终态文件与账本摘要。
9. 清除终审包中的占位符，给出 `ready`、`ready_with_disclosures` 或 `stopped`，然后只把成书与终审包交给用户。

## 不可突破的边界

- 不修改仓库代码、历史证据目录、LLM 缓存、账本或 `_rounds/` 来“修好”某本书。
- 不直接编辑正式生成产物或 `book.txt`。只写终审包以及 [修复状态机](references/recovery.md) 明列的人工输入面；让 CLI 完成校验、归档和正式落盘。
- 不并行运行两本书，也不在同一本书上并行两个写盘节点。
- 不覆盖项目默认模型、思考模式、并发或重试配置，除非用户明确要求一次独立实验。
- 不把 `unver`、`skipped`、分母为零或落选候选的状态改写成 `ok`。绿灯必须带分母和 freshness。
- 不估算或换算金额。只报告账本中的调用次数、缓存命中、token 与耗时事实。
- 不因已有付费授权推定正文外发授权，反之亦然；任一缺失时仍运行零成本的 `state`、`inbox` 与 `preview` 以形成恢复证据，但不得执行 preview 中的首个外部模型步骤。
- 不为追求“全绿”改写无关内容。只处理阻塞所选定稿、成书完整性或用户终审判断的事项。

## 完成定义

只有同时满足以下条件才可标记 `ready`：

- `state` 显示主链完成且所有消费中的正式产物 fresh/match；已解释的诊断噪声单列披露。
- `inbox` 的 `invalid`、`rejected`、`stalled`、`upstream`、`approval` 均为零；`human_only` 已逐类审阅并记录。
- 活动目录不存在未解释的 `.INVALID`、`.rejected` 或 `.stalled`；历史 `_rounds/` 不计入活动余额。
- 三次代批均有可追溯依据；所选 stage6 候选和 stage7 人名表均通过各自定稿条件。
- `book.txt` 通过 [代批与终审契约](references/delegated-review.md) 的逐字对账，终审包中的统计与盘上事实一致。

任何一项未满足时只能使用 `ready_with_disclosures` 或 `stopped`，并说明用户审阅时真正需要关注什么。
