# 修复机制（旧称「反哺」）

校验结论按 `remediation` 分四类处置，各走各的修复路由。**权衡编码在策略常量里，
不在运行时现场判断**——轮次有上限、准入有筛子、成本有上界，无论驱动者是人、
CLI 还是将来的 agent，回路的行为完全相同。

标准形态（2026-08-14 已拍板，源出 `docs/E2E_VALIDATION_20260813.md` §10.4-3）：

```
首轮生成 → 机械 check → 按 remediation 路由：
  deterministic：跑 Rule.fix 的免费路由，先修
  llm：仅准入「唯一、受约束」(repair=='UNIQUE')，且 unresolved_rounds <
       STOP_REWRITE_AFTER(=2)；连续 2 轮未消 ⇒ 判根因不在写手，主动跳过 → 转 human
  human：进「待人工」六类清单，等 --apply-edit / --pick / --wontfix
  none：metric 读数，无需处置
→ 每轮修后全规则机械回归；语义校验（stage5/stage6）只在机械干净且 fresh 后运行
```

## 路由表

| `remediation` | 谁修 | 路由 | 成本上界 |
|---|---|---|---|
| `deterministic` | 代码 | `Rule.fix` 指的免费 (节点, flag)（如 `--migrate` / `--drop`），注册期第 ⑧ 条校验保证它真跑得起来 | 一次命令，0 成本 |
| `llm` | 写作层 | 准入两道：`Rule.repair=='UNIQUE'`（满足路径唯一才进付费自救；密度/比率/档位一律转人工——最省力的满足路径是删内容或编内容）＋ `unresolved_rounds < STOP_REWRITE_AFTER`；指令文本唯一落点是 `Rule.directive`，语义泄露只下发证据片段、不把 secret 答案喂给写手 | 每 (章, 判据) ≤ 2 轮付费 |
| `human` | 人 | 进「待人工」六类清单（`invalid` / `rejected` / `stalled` / `human_only` / `upstream` / `approval`）；0 成本口：`--apply-edit`（各层均已接线）· `--pick`（stage6 换候选）· `--wontfix`（豁免**不删结论**，单独计数） | 无自动成本 |
| `none` | 无 | `metric` 读数，无需处置 | — |

⚠ 「没问题」不是第五类处置——它是报告行的 `status=ok`；`unver`（查不了）与
`skipped`（没跑）单独可见，**不许洗成任何一类**。四类处置与 `check_kind`
（机械/语义）、`sev`（影响多大）三轴正交，见 `docs/schema/_report.md` §2.1。

## 唯一落点

| 东西 | 落点 |
|---|---|
| 路由算法（四类怎么派生） | `novel/report/builder.py::remediation_of` |
| llm 准入的四道筛 | `novel/stages/stage5/write.py::revise_targets` |
| 轮次上限 | `contracts/invariants.py::STOP_REWRITE_AFTER` |
| 收缩回归护栏（删坏了整轮弃用） | `novel/stages/stage5/checks.py::trim_regressions` |
| human 落面（六类清单生产者） | `novel/views/inbox.py::inbox_view` |
| 六类的取值面与纪律（`unver` 永不入列） | `contracts/invariants.py::INBOX_KIND` |

## 实测账

stage5 可修复条目 **36 → 9（转人工 27 条，75%）**——准入筛子把「花钱也修不好的」
在进回路之前就筛掉了。轮数失控在结构上不可能：筛子 + 上限 + 主动跳过，
最坏情形的付费次数在发起前就算得出来。
