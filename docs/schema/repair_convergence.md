# 当前故事链的返修与恢复

当前契约为story-v1，只在当前输出根上恢复相同请求身份的耐久响应。旧根只读归档，新根重新生成；无跨版本转换、旧候选采用或双路径维护。

## 节点内窄修

`novel/llm/story_requests.py`先耐久回答，再严格解析JSON、编译与验证。带明确path的字段/条目错误仅允许授权位置的替换，补丁应用后完整重过同一compiler。候选、已取材料、允许路径和错误冻结入请求身份。重复问题按根因停止，预算不重置；无法定位的缺陷明确停止，不猜修复位置。

合法材料请求为need_materials控制回答，受源章范围、段数、分页和同owner额度约束；它单独记为material，不冒充语义候选。空答保持同内容轮的有界attempt；仅完整reasoning可经同一JSON/schema/compiler救回，原网络响应和费用不改。unknown和截断不自动重买。

## 跨节点回派

源章事件漏错由book提交source_issue，回对应chNN；书级实体/认识/因果问题回book，姓名构词回names。保存策略发现源事实缺口必须回Stage2。章展开要求改变整书边界时回body_design；写手发现关键动机、决策或沟通缺失回章建模或Hook设计。

问题保留owner/node/item/path/证据、发现者、原产物摘要、问题指纹与累计轮数。原发现者在当前输入上成功复验才清账。相同问题无进展或额度耗尽即停；重启、换epoch和免费复验不重置额度。状态与成本账分别保留，文件存在不能证明通过。

book回派时保留初次已购全书草稿，章修后仅修显式引用/证据闭包；BookRepairWire有独立受限协议与耐久trace。详见 [Stage2书级冻结](stage2.md#全书综合的冻结与窄修)。原始综合只做一次，修订与材料控制分别计数。

## 正文与交付

正文按章顺序消费本章包和已接受前章的完整段落短尾。章包不变而短尾变化时，只能对前8个既有自然段keep/replace；超scope明确返回needs_scope，不能暗做整章改写。新补丁保留初始生成来源，并记录真实窄修响应、当前tail和结果字节。

最终组装和下载共用当前packet/body/receipt/Hook证明。封存中断后从合法耐久响应免费补提交；不直接接受未封存文件。源码修复、机械通过和免费复验均不证明文学质量，质量保持unver。

完整字段和节点见 [三层流水线](str06.md)，首答/材料/补丁/成本读数见 `tools/story_metrics.py`，真实反例见 `tests/test_story_runtime.py`、`tests/test_story_pipeline.py`、`tests/test_story_prose.py`。
