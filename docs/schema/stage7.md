# Stage 7 当前契约

Stage7 在新链（STR-06，F8a 激活）有两个节点：`str06.stage7.naming`（NameMap v5
赋值，读名称结构与 Blueprint 的只读投影）与 `str06.stage7.assemble`（改名替换与
成书装配，`executor_content_limit=0` 结构性禁付）。旧 `stage7.rename`／
`stage7.assemble` 与 approve／adopt-residual 人工面随旧链退役。

ApplyNames 是替换的唯一应用者：章回执链核对 → 单遍替换 → 残留检查 → TXT 先落盘 →
manifest 最后提交。Stage7 不改写 Stage5 任何字节；交付可确定性重放，同输入同字节。
NameMap 是交付变换契约，接受正文、回执与已购证据独立保护。

成书按阅读序逐章加「第N章」一行（章号由系统添加；写手协议据此约定不写章号与标题），
引子放最前、不加标题。交付身份对**分章**敏感：有序的 `(chapter_ordinal,
prose_sha256)`、Hook 成文 hash、改名映射与保留字面、`ASSEMBLY_FORMAT_VERSION`
共同派生——拼接相同但分章不同的两份书是不同的交付。章回执与前文链仍绑定不含章号的
`prior_body_text`。

## 现役产物

本层产物**都是 schema v1**（三级路径是切换时新建的命名空间；形状破坏史由模型
`contract_version` 字面量与候选产物表的 RC 标签承载，见 `docs/BACKEND_CONTRACT.md`
`R-2.5`／`R-5.11b`）。

| 路径 | 模型 | 生产节点 | 内容 |
|---|---|---|---|
| `stage7/naming/name_map.json` | `NameMapV5` | `str06.stage7.naming` | 名称位赋值（char_ref → 目标名／保留） |
| `stage7/final/{title}_rewriting.txt` | 成书（text） | `str06.stage7.assemble` | 最终成书（按书名命名；TXT 先落盘） |
| `stage7/final/delivery_manifest.json` | 自由形态派生记录 | `str06.stage7.assemble` | 交付 manifest（逐章篇幅读数、全书 ±10% 偏差、hash 账；最后提交） |

`runtime/delivery/` 下的改名 Hook 与 assembly 报告是审计记录，由唯一 writer 治理，
不进版本表。

## 权限分界

Naming 只消费名称结构与稳定身份的只读投影；ApplyNames 是替换的唯一应用者——
投影不落交付、Apply 不改映射。残留判断遵守 claimed/kept：目标名含旧字面不误报，
kept 含映射键不误报。换 NameMap 只影响交付，Hook 窄改不使无关正文接受失效。

## 判据表

| 判据 | sev | 要求 |
|---|---|---|
| `name_map_compile` | hard | NameMap v5 编译闭合（名称位一致、无姓氏冲突） |
| `naming_residual` | hard | 单遍替换后旧名零残留（遵守 claimed/kept） |
| `assembly_chapter_chain` | hard | 章回执链核对（逐章写手视图／验证输入摘要＋正文 hash；Machine 层摘要仅审计不参与） |
| `delivery_manifest` | hard | TXT 先落盘、manifest 最后提交、hash 逐字一致 |
| `delivery_replayable` | hard | 同输入交付字节一致（零新购重放） |
| `delivery_chapter_markers` | hard | 成书按阅读序加「第N章」、交付身份对分章敏感 |
| `naming_quality_unver` | unver | 新名的阅读质量未经语义评审 |
| `book_length_deviation` | metric | 全书相对书级目标的偏差读数（±10%） |

当前注册表共 **8 条**：**6 hard、1 metric、1 unver**。唯一注册表为
`contracts/judges/str06.py`（F8c 起分流至本层判据模块）。
