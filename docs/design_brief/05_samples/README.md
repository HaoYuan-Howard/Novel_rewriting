# 样本索引

19 份**真实 API 响应**，抓自现役后端（一本 8 章的书，`sid=7615069877019300633`，已跑完 stage1~stage7）。
原始字节，未经修改。用法见 `../00_START_HERE.md`。

| 文件 | 来自 | 用在 | 里面有什么（真实数量） |
|---|---|---|---|
| `enums.json` | `/api/enums` | 全局 | **36 个动作**（带 `paid`）、12 个 job kind、各类枚举 |
| `novels.json` | `/api/novels` | S-01 | **3 本书**，各带 10 份产物的状态 |
| `novel_overview.json` | `/api/novels/:sid` | S-02 | 一本书的逐产物状态 + `provenance` |
| `stage1_report.json` | `…/stage1/report` | **不上屏了** | 24 个字段；`warnings` 为空、`notes` 3 条。<br>**别照它做界面**——S-03 已砍掉报告块，产物本身也瘦到 11 个字段（`docs/schema/stage1.md`）。<br>文件留着是因为它是原型的真实抓包，删了就少一份实测证据 |
| `stage1_chapters.json` | `…/stage1/chapters` | S-03 | 8 章 × {章号, 标记原文, 字数, 预览}。S-03 的侧栏章表 |
| `stage1_hook_copy.json` | `…/stage1/hook_copy` | S-03 | 原作的开篇文案段 |
| （没有 mock） | `…/stage1/segments` | S-03 | **S-03 的主体**。原型上没有这个 endpoint（那一屏是新加的）。<br>形状照 `reading_ch3.json` 的 `segments[]`，`kind` 换成 `hook`/`chapter`/`dropped`/`end_marker`/`hole` |
| `reading_ch3.json` | `…/reading/3` | S-04 | 原文 1461 字切成 **19 块** ↔ **9 个 beat** |
| `stage2_global.json` | `…/stage2/global` | S-05 | 15 人物 / 15 关系 / 6 世界规则 / **secrets 是空数组** |
| `stage3_style.json` | `…/stage3/style` | S-06 | 8 条 voice / 5 条写作习惯 / 6 个维度的档位词（stored vs now） |
| `stage4_ch3.json` | `…/stage4/3` | S-07 | 全书策略 + 8 章索引 + 本章 **8 拍**的逐拍材料 |
| `stage5_ch3.json` | `…/stage5/3` | S-08 | 源文 + 材料 + 1 份成稿；**锚点 3/8 拍、切成 3 块** |
| `review_ch3.json` | `…/review/3` | S-09 | 全书 **61 条结论、其中 41 条是 `metric`**；**本章只有 5 条** |
| `inbox.json` | `…/inbox` | S-10 | **6 条待人工**（本例全是 `human_only`）、`wontfix` 计数 |
| `stage6_hook.json` | `…/stage6` | S-11 | **3 个候选文案** + 13 条判据 + 定稿状态 |
| `stage7_names.json` | `…/stage7` | S-12 | 15 个新旧人名 + **2 条漏网叫法候选** + 成书状态 |
| `stage7_ch3.json` | `…/stage7/3` | S-12 | 同上 + 本章 **76 行**替换前后对照 |
| `check_stage2.json` | `…/check/stage2` | S-13 | **`badge: "fail"`**，2 条 hard / 28 条 info / 24 条 ok |
| `check_stage5_review.json` | `…/check/stage5_review` | S-13 | **`badge: "amber"`**，0 hard / **2 条 unver** |
| `jobs.json` | `/api/jobs` | P-01 | **空数组**——空态照它设计 |

## 几个刻意保留的「不好看」的真实情况

设计时**不要把它们当异常**，它们是常态：

- `stage2_global.json` 的 `secrets` 是 **空数组**
- `jobs.json` 的 `jobs` 是 **空数组**
- `stage5_ch3.json` 里一章只有 **1 份成稿**（另一条臂没写这一章），而字段是数组
- `stage5_ch3.json` 的锚点覆盖率只有 **0.375**——大部分块是「盖了好几拍、没有验证过的对应」
- `check_stage2.json` 是 **红的**（2 条 hard）——一本已经跑完出书的书，报告仍然是红的，这很正常
- `review_ch3.json` 的 `diff_rows` 是**空数组**（这一章没有第二轮稿可比），而 `findings` 只有 5 条
- `stage7_names.json` 里有人 `renamed: false`，带 `not_renamed_reason`
