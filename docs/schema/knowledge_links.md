# 必要知识关联（KnowledgeLinks v3）

本载体由 Blueprint owner 免费物化，保存传播许可而不复制事实正文。
Global v7 只提供长期关系、规则版本、背景与 arc_secrets；人物知情和局部读者揭示
仅由 Commander 在已有 Beat/State 上稀疏登记。不存在 fact×人物矩阵。

## 义务与引用

关系保留 relation 与 relation_visibility，规则版本保留 world_rule 与 world_rule_reveal。
事实义务使用 effective_from；披露义务使用 revealed_from，两者不互相裁剪。
effective_until 是包含该章的成立末章；历史事实后揭示、提前透露未来事实均合法。
规则 knowledge_ref 与 subject_ref.ref 均为 `rule_id/version_id`，不以时间代替持久身份。
secret_reveal / secret_setup 分别使用 revealed_from / setup_chapters；无铺垫的冷揭示不补造 setup。
setting 未建立时没有义务。退休的 knowledge_event / character_state 不进入当前 schema。

PlotSubjectRef 带实际人物或关系端点呈现；语义消费 key 包含 presentation，原 Global 查询
只用 kind/ref。Global 关系义务的默认 ActorRef 是内部命题端点，不能作为 Writer 呈现许可。
Writer 展开关系或秘密 fact_ref 时，仍须从实际 BP/PlanGoal 找到唯一显式关系 use；缺失或
歧义由 owner 免费订正，不能从人物 ID 猜映射。

## 时点与身份

at_beats 必须显式 trigger_beat_ref，且该 Beat 属于支持主体和目标章；at_entry 仅接受
本义务明确提供的章首候选。subject_refs 选择已有完整命题；引用证据不等于语义接受。
character_knowledge audience 为 `{kind: character, actor: ActorRef}`，group 分支保留明确标签。
受众必须属于目标章已提供的人物呈现闭集。

at_beats/at_entry 的 `identity_actors` 默认为空。非空只可用于 secret_reveal 或 reader_reveal，
明确同一实体的至少两个不同、已提供的 presentation。candidate_actors 由当前 BP 的实际位置
及携入 State 支持闭包生成，不是人物名册或别名知识图。一次事件只开放所声明的组合。

同章局部同人揭示完整写入 Beat/State，按必要性登记 reader_reveal；不会自动晋升 arc_secret。
例如第 11 章先见“新邻居”、后段开门认出裴书远，许可在后段 Beat。跨章 10→11 的长期隐藏
例子才由已有 arc_secret 生成长期披露义务。标量身份时点不是同章场景许可。

## 提交与恢复

put/retire 采用 event_id 和 expected_event_sha256，原决定与物化 application 同时留审计。
主体删除不删除未解决事件；必须有明确退役或修订。较新的否定/未决遮住旧接受。
接受记录绑定每项实际候选、完整主体、证据及原文范围的 SHA，不以整书 SHA 扩大无关失效。
正式 links 必须完整覆盖当前义务且不含 unresolved；缺来源、候选或映射不能放宽 schema。
免费闭合/已购恢复沿用现有 owner 提交、来源复验和审批审计，不新增固定模型调用。
