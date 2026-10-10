# Stage 5 当前契约

Stage5 在新链（STR-06，F8a 激活）有两个付费节点：`str06.stage5.body`（按章串行
成文）与 `str06.stage5.hook`（Hook 单产成文）。旧 `stage5.write`／`stage5.review`
两相、typed edit、Reviewer rewrite 与 trim 人工面随旧链退役；生产正文 Judge 已取消，
语义质量一律 unver 披露。

正文按章成文：每章一次首次生成＋同输入有限重写（attempt 进请求身份），章级机械验
全过才算合法候选；确定性候选选择（硬检查全过优先，耗尽后按相对目标偏差最小、平局
取较晚）。提交顺序＝原响应耐久 → 检查 → 候选 → 原子写正文 → 原子写章回执。恢复按
已提交目标章集合装载（`committed_set_recovery`，选章见 `--body-chapters`），回执绑定
当前章包摘要（写手视图＋验证输入）与正文字节，不绑定全书 IR 与前文链。Hook 只消费
HookContext（正式输入确定性重建），不读源 hook 文案。

Hook 不沿用源引子字数，取消固定 target／hard_max 和按字数拒稿；字符数保留为观察，
输出 token 预算与有限修订属于执行边界。写手在正式材料边界内安排对白、即时反应、
语言与节奏，每段应推进局面、人物认识或阅读期待，背景只解释到足以理解当前行动。
人物声线、全书风格与正式表达决定同源，材料场声线标明适用材料编号；表达决定
只投影已选机制，不让写手重作决定。局部表现策略不能新增前史、动机、事件或结局。
破折号、问句和环境描写不设数量闸，是否服务具体情境由阅读验收判断。

本轮保留单个正式 Hook、不新增生产审稿节点，不新增 Stage5 段落 patch。
已有 Stage4 条目窄修保留；成文局部修复是否增加，依后续可定位失败与修复效果决定。
首答质量以固定样本的“楔子＋第 1 章”联合阅读验证：冲突清晰度、因果承接、人物声线、
阅读期待、简洁与开章新增推进分别评价，机械通过不代签文学质量。

rc21 材料批的正文 renderer v11／写手协议 v9：选中的可改写细节只进创作提示，
系列概括所覆盖的目标实例只在概括拍进不可违背分区。写手遵守必要动机与铺垫，
不能用主题／情绪解释替代保存条件，也不能补造过去的实例。

纯未释放专名字面失败走段落窄修：validator 给原文精确命中区间，runner 只发送
受影响行、前后非空只读邻行与本章合法已释放称谓；补丁只能替换受影响行。
请求绑定完整基文 hash 与章包写手／验证身份，未发送的正文变化也会换身份。
坏 JSON、重复键、越界行或修改邻行仍留在原窄修臂；合并后以原校验器重验整章。
与篇幅等其它失败混合时维持现役整章修订，首次＋最多两次修订的额度不变；
合法补丁的接受记录绑定合并后的正文，丢回执仍经既有免费恢复入口重建。

## 现役产物

本层产物**都是 schema v1**（三级路径是切换时新建的命名空间；形状破坏史由模型
`contract_version` 字面量与候选产物表的 RC 标签承载，见 `docs/BACKEND_CONTRACT.md`
`R-2.5`／`R-5.11b`）。

| 路径 | 模型 | 生产节点 | 内容 |
|---|---|---|---|
| `stage5/body/ch{nn}.txt` | 正文（text） | `str06.stage5.body` | 逐章改名前正文（prose） |
| `stage5/acceptance/body/ch{nn}.json` | `ChapterReceipt` | `str06.stage5.body` | 章级接受回执（绑定章序、章包写手视图与验证输入摘要、正文 hash 与请求身份；Machine 层摘要仅审计，各章独立有效） |
| `stage5/hook/hook.txt` | 正文（text） | `str06.stage5.hook` | Hook 成文（prose） |
| `stage5/hook/receipt.json` | `HookReceipt` | `str06.stage5.hook` | Hook 回执（绑定 HookContext 身份、正文 hash 与请求身份） |

`runtime/body_attempts_ch{nn}.json`、`runtime/hook_attempts.json` 等 attempts 记录
是逐请求动态文件，由付费边界与 runner 各自的契约治理，不进版本表。

## 机械验边界

章级机械验（`novel/stages/stage5/body_checks.py`）覆盖：非空／编码／叙述外壳、
未释放专名（硬失败，重写耗尽后无合法候选即停止，不披露放行）、篇幅带（超限只有
篇幅带可披露）。Hook 机械验（`novel/stages/stage5/hook_writer.py`）覆盖：非空、编码、
包装行及 withheld 专名字面。POV、起笔时序和披露引用在设计／Context 构建期检查，
固定字数不再是成文硬闸。机械通过不证明文学质量、章内
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
