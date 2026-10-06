# Stage 5 当前契约

Stage5 在新链（STR-06，F8a 激活）有两个付费节点：`str06.stage5.body`（按章串行
成文）与 `str06.stage5.hook`（Hook 单产成文）。旧 `stage5.write`／`stage5.review`
两相、typed edit、Reviewer rewrite 与 trim 人工面随旧链退役；生产正文 Judge 已取消，
语义质量一律 unver 披露。

正文按章成文：每章一次首次生成＋同输入有限重写（attempt 进请求身份），章级机械验
全过才算合法候选；确定性候选选择（硬检查全过优先，耗尽后按相对目标偏差最小、平局
取较晚）。提交顺序＝原响应耐久 → 检查 → 候选 → 原子写正文 → 原子写章回执。恢复按
已提交目标章集合装载（`committed_set_recovery`，选章见 `--body-chapters`），回执绑定
当前 IR／前文／正文字节三方核对。Hook 只消费
HookContext（正式输入确定性重建），不读源 hook 文案。

## 现役产物

本层产物**都是 schema v1**（三级路径是切换时新建的命名空间；形状破坏史由模型
`contract_version` 字面量与候选产物表的 RC 标签承载，见 `docs/BACKEND_CONTRACT.md`
`R-2.5`／`R-5.11b`）。

| 路径 | 模型 | 生产节点 | 内容 |
|---|---|---|---|
| `stage5/body/ch{nn}.txt` | 正文（text） | `str06.stage5.body` | 逐章改名前正文（prose） |
| `stage5/acceptance/body/ch{nn}.json` | `ChapterReceipt` | `str06.stage5.body` | 章级接受回执（绑定章序、全书 IR、实际前文、正文 hash 与请求身份） |
| `stage5/hook/hook.txt` | 正文（text） | `str06.stage5.hook` | Hook 成文（prose） |
| `stage5/hook/receipt.json` | `HookReceipt` | `str06.stage5.hook` | Hook 回执（绑定 HookContext 身份、正文 hash 与请求身份） |

`runtime/body_attempts_ch{nn}.json`、`runtime/hook_attempts.json` 等 attempts 记录
是逐请求动态文件，由付费边界与 runner 各自的契约治理，不进版本表。

## 机械验边界

章级机械验（`novel/stages/stage5/body_checks.py`）覆盖：非空／编码／叙述外壳、
未释放专名（硬失败，重写耗尽后无合法候选即停止，不披露放行）、篇幅带（超限只有
篇幅带可披露）。Hook 机械验（`novel/stages/stage5/hook_writer.py`）覆盖：POV 一致、
起笔事实时序、禁止集零兑现、withheld 字面不出现。机械通过不证明文学质量、章内
揭示位置或逐场义务兑现——那些维度保持未评披露。

## 判据表

| 判据 | sev | 要求 |
|---|---|---|
| `body_chapter_complete` | hard | 每章正文完整（非空、编码合法、无叙述外壳） |
| `body_withheld_literals` | hard | 未释放专名不得出现；无合法候选即停止 |
| `body_required_literals` | hard | 必保揭名的承载章正文必现该称谓字面（缺字面即拒，段落级修句） |
| `body_length_band` | hard | 章篇幅落在配置篇幅带内；超限只有篇幅带可披露 |
| `chapter_receipt_binding` | hard | 章回执绑定章序、IR、前文、正文与请求身份 |
| `body_semantics_unver` | unver | 正文文学质量未经语义评审（Judge 已取消） |
| `body_length_reading` | metric | 逐章实际篇幅与预算偏差读数 |
| `hook_mechanical` | hard | Hook 机械验通过 |
| `hook_receipt_binding` | hard | Hook 回执绑定 Context 身份、正文 hash 与请求身份 |

当前注册表共 **9 条**：**7 hard、1 metric、1 unver**。唯一注册表为
`contracts/judges/str06.py`（F8c 起分流至本层判据模块）。
