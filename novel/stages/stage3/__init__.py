#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novel.stages.stage3 —— 文风画像。**第一条端到端跑通的纵切。**

选它当第一条纵切的决定性理由：**它吃的三份上游都在盘上**
（`stage1/main_text.json` · `stage1/hook_copy.json` · `stage2/global_grounding.json`）
⇒ **不实现 stage0/1/2 也能端到端跑通**，让「不要再往摄入/分章投入」
与「要一个真的纵切」同时成立。

而它一层就覆盖：1 次付费调用 · 1 份产物 · 9 条判据 · 两条不同的 `.INVALID` 路径 +
`gates_skipped` · 先校验后落盘 · `--check-only`/`--remeasure`/`--revalidate` ·
**退役而不删**的两条 · `name_leak` 的**三态** · 代码算的 `measurable{}`。

⚠ 基线臂只在校验期用第三份上游；默认逐章臂还用其中的专名表确定性脱敏当前章，
但不向风格模型下发 Stage 2 的秘密事实或全书情节。
"""
