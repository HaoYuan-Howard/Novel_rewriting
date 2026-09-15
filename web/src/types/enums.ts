import type { RequiredActionInput } from '@/types/jobs'

/**
 * /api/enums —— 除 vocabulary.ts 里那几个核心枚举外，其余枚举一律运行时从这里取，
 * 不硬编码进前端。后端改了枚举，前端不该跟着改代码。
 */

export interface EnumAction {
  kind: string
  label: string
  /** CLI 附加参数。命令行按这个拼，不按 free_flag。 */
  extra: string[] | null
  /** 花不花钱由服务端算好下发。36 个动作里只有 7 个是 true。 */
  paid: boolean
  /** 免费动作的 flag 串（可能是空串）；paid 的一律是 null。 */
  free_flag: string | null
  writes?: string
  writes_disk?: boolean
  needs_value?: string | null
  /** Backend-derived missing NodeSpec values to collect before execution. */
  required_inputs?: RequiredActionInput[]
}

export interface EnumJobKind {
  label: string
  paid: boolean
  stage?: string
  section?: string
  flags?: string[]
  produces?: string[]
  lifecycle?: {
    unit_kind: string
    input_workpack: string
    generation_mode: string
    /** 审阅节点一律 'human'（Commander 退役后审批闸由人工在 Stage 页面过）。 */
    review_mode: string
    review_scope: string
    approval_projection: string[]
    repair_actions: string[]
    repair_owners: string[]
    session_scope: string
    recovery_entries: string[]
    executor_content_limit: number
    /** [blocker, scope][]：审批闸归属映射，形状与退役前的 commander_bindings 相同。 */
    approval_bindings: [string, string][]
  }
}

export interface EnumsResponse {
  nonbody_kinds: string[]
  time_layers: string[]
  style_words: string[]
  invent: { allow: string[]; forbid: string[]; principle: string }
  job_kinds: Record<string, EnumJobKind>
  actions: EnumAction[]
  /** Stage4 计划的实例值仍随计划下发；这里仅提供表单/展示需要的运行时取值面。 */
  structure_modes: string[]
  structure_change_kinds: string[]
  structure_approval_decisions: string[]
  structure_approval_states: string[]
  /** Stage4A 正式审阅的五张闭合集；表单不得在前端另抄取值。 */
  design_strategy_decisions: string[]
  design_chapter_decisions: string[]
  design_reviewer_kinds: string[]
  design_repair_codes: string[]
  design_constraint_operation_kinds: string[]
  /** code -> [minScenes, minOpaqueAliases]；用于表单完整性检查，最终合法性仍由后端判定。 */
  design_repair_constraint_arity: Record<string, [number, number]>
  design_review_states: string[]
  design_preflight_statuses: string[]
  /** Stage5 Commander 闭合表单的三张运行时取值面；页面不得复制枚举字面。 */
  stage5_must_appear_rulings: string[]
  stage5_closure_groups: string[]
  stage5_closure_states: string[]
  /** Stage4B 语义担忧与代码铸造不可行证明的运行时取值面。 */
  binding_concern_codes: string[]
  binding_infeasibility_codes: string[]
  /** Stage4 A/B 异常优先工作包 v1 的服务端闭集；组件只把本地映射当展示标签。 */
  approval_workpack_groups: string[]
  approval_workpack_kinds: string[]
  approval_workpack_dispositions: string[]
  final_approval_workpack_kinds: string[]
  approval_workpack_actions: string[]
  approval_workpack_effects: string[]
  approval_workpack_group_effects: Record<string, string[]>
  /**
   * This is an honest display boundary, not an access-control boundary: the server still
   * sends the complete stage2 document and the adapter projects it before a Screen sees it.
   */
  spoiler_disclosure: {
    mode: 'display_only'
    security_boundary: false
    server_sends_full_data: true
    hidden_paths: string[]
    known_leakage_surfaces: string[]
    consumers: string[]
    notice: string
  }
}
