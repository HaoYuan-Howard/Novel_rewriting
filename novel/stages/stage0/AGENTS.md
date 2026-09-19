# Stage0 局部架构

本文件维护 Stage0 的局部不变量。跨 Stage 硬闸见根 `AGENTS.md` 的 I-\* / S-\*；
产品行为与 R 编号见 `docs/BACKEND_CONTRACT.md`；字段形状见 `docs/schema/stage0.md` 与
`contracts/schema/stage0.py`。

心智公式：Scanner = detect → Compiler = reconstruct → Canonical = transform →
Projection = clean_text → Validator = enforce。全程 **0-LLM**。

## 局部不变量（S0-\*）

| 编号 | 规则 |
|---|---|
| S0-1 | **机械 admission**：闸门是 `admission.status=passed` 且 `scope=mechanical`，不是语义 `approved`。资格唯一落点 `eligibility.is_stage0_executable`。 |
| S0-2 | **可重建性**：在可重建前提下，正文权威由「归一化原文 − removed spans」确定；`clean_text.txt` 是物化投影，JSON 只存 `clean_sha1` / `body_code_points`。 |
| S0-3 | **幂等重跑**：同一原文 + 同一剔除算法结论 ⇒ 盘面字节不变；算法结论变化才整份替换 transform。 |
| S0-4 | **唯一输出**：权威 JSON 是一次 `Stage0Transform`（路径仍 `stage0/revisions.json`）；无 revision 链、无候选、无 pending、无人工编辑入口。 |
| S0-5 | **可解释剔除**：每段 `RemovedSegment` 须带 kind / marker_line / span / origin=machine；保留区间字符不得修改。 |

## 现役公开面

| 符号 | 职责 |
|---|---|
| `removal.run_source` | 摄入与落盘入口 |
| `revisions.make_transform` / `commit` / `ensure_materialized` | transform 构造、写盘、物化自愈 |
| `checks.run_check_only` | 纯复检，不改正文 |
| `eligibility.is_stage0_executable` | stage1 / 付费链资格唯一谓词 |

v2 revision 链产物拒读；恢复路径是 `stage0.remove --source` 重建，不做通用迁移。
