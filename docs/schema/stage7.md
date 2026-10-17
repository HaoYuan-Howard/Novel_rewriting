# Stage 7 当前契约

Stage7 在新链（STR-06，F8a 激活）只有一个节点：`str06.stage7.assemble`（改名替换与
成书装配，`executor_content_limit=0` 结构性禁付）。命名前移批（2026-10-18，rc26）起
`str06.stage7.naming` 退役——NameMap v6 赋值迁至 `str06.stage4.naming`
（产物 `stage4/naming/name_map.json`，见 [stage4.md](stage4.md)），目标姓名进入
Preserve 目标简报与设计链。旧 `stage7.rename`／`stage7.assemble` 与 approve／
adopt-residual 人工面随旧链退役。

ApplyNames 是替换的唯一应用者：章回执链核对 → 身份提及与映射核对 → 按区间改名 → TXT 先落盘 →
manifest 最后提交。Stage7 不改写 Stage5 任何字节；交付可确定性重放，同输入同字节。
NameMap 是交付变换契约，接受正文、回执与已购证据独立保护。

成书按阅读序逐章加「第N章」一行（章号由系统添加；写手协议据此约定不写章号与标题），
引子放最前、不加标题。交付身份对**分章**敏感：有序的 `(chapter_ordinal,
prose_sha256)`、Hook 成文 hash、改名映射与保留字面、两类身份声明表及
`ASSEMBLY_FORMAT_VERSION` 共同派生——拼接相同但分章不同的两份书是不同的
交付。章回执绑定本章改名前正文及当前章包，不绑定前文链。

## 现役产物

命名前移批（2026-10-18，rc26）后本层没有进 `ARTIFACT_SPECS` 的 JSON 产物：
`stage7/final/{title}_rewriting.txt` 是正文、`delivery_manifest.json` 是自由形态派生
记录，形状由唯一 writer 契约治理（三级路径是切换时新建的命名空间；形状破坏史由模型
`contract_version` 字面量与候选产物表的 RC 标签承载，见 `docs/BACKEND_CONTRACT.md`
`R-2.5`／`R-5.11b`）。NameMap v6 随 `str06.stage4.naming` 迁至 [stage4.md](stage4.md)。

| 路径 | 模型 | 生产节点 | 内容 |
|---|---|---|---|
| `stage7/final/{title}_rewriting.txt` | 成书（text） | `str06.stage7.assemble` | 最终成书（按书名命名；TXT 先落盘） |
| `stage7/final/delivery_manifest.json` | 自由形态派生记录 | `str06.stage7.assemble` | 交付 manifest（逐章篇幅读数、全书 ±10% 偏差、hash 账；最后提交） |

`runtime/delivery/` 下的改名 Hook 与 assembly 报告是审计记录，由唯一 writer 治理，
不进版本表。

## 权限分界

Naming（现住 Stage4）只消费名称结构与稳定身份的只读投影；ApplyNames 是替换的唯一
应用者——投影不落交付、Apply 不改映射。`compile_bound_replacements` 按 char_id 编译
登记构词；`apply_bound_names` 只改回执声明区间，普通义与表外文字保持原样。同字面
不同人物不再由全局字符串替换顺序决定。换 NameMap 只影响交付，Hook 窄改不使无关
正文接受失效。

## 判据表

| 判据 | sev | 要求 |
|---|---|---|
| `naming_residual` | hard | 身份提及、区间与映射闭合；零表外改动 |
| `assembly_chapter_chain` | hard | 章回执链核对（逐章写手视图／验证输入摘要＋正文 hash；Machine 层摘要仅审计不参与） |
| `delivery_manifest` | hard | TXT 先落盘、manifest 最后提交、hash 逐字一致 |
| `delivery_replayable` | hard | 同输入交付字节一致（零新购重放） |
| `delivery_chapter_markers` | hard | 成书按阅读序加「第N章」、交付身份对分章敏感 |
| `book_length_deviation` | metric | 全书相对书级目标的偏差读数（±10%） |

当前注册表共 **6 条**：**5 hard、1 metric**。唯一注册表为
`contracts/judges/str06.py`（F8c 起分流至本层判据模块）。

身份文本协议、回执两类区间及旧稿重建边界见 [name_text.md](name_text.md)。交付身份同时绑定两类声明表。
命名判据（`name_map_compile`／`naming_quality_unver`）随节点迁至 [stage4.md](stage4.md)。
