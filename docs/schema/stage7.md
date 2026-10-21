# Stage7：最终装配

唯一节点assemble不调用模型。它检查当前完整章集合、逐章packet/tail/body/receipt链、Hook和真实文本字节，按原阅读顺序输出 `stage7/final/{title}_rewriting.txt` 与FinalManifest。不得用文件存在或退出码代替交付证明。

下载与交付读面免费重验同一证明；正文或章包变化后，旧manifest不能授权下载。装配不规范化换行、不删字、不改名、不重写正文。质量仍为unver。

纯入口在 `novel/stages/stage7/story.py`，正式载体在 `contracts/schema/story_prose.py`。详细交接见 [三层规格](str06.md)。

## 当前版本与判据

本层正式故事产物**都是 schema story-v1**，旧根只读归档，须在新根由当前producer重建。

当前注册表共 **3 条**：**2 hard、1 unver**。

| 判据 | 级别 | 职责 |
|---|---|---|
| `story_delivery_chapters` | hard | 交付按源阅读序核对完整章包、正文与回执集合 |
| `story_delivery_bytes` | hard | TXT/manifest 可从当前接受产物免费逐字重建并核对摘要 |
| `naming_quality_unver` | unver | 目标名称的阅读质量与正文使用效果未经语义验收 |
