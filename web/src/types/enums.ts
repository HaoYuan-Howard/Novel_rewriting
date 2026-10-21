import type { RequiredActionInput } from '@/types/jobs'

/**
 * /api/enums —— 除 vocabulary.ts 里那几个核心枚举外，其余枚举一律运行时从这里取，
 * 不硬编码进前端。后端改了枚举，前端不该跟着改代码。
 *
 * ⚠ STR-06 切换（F8c）后前端只消费动作表与剧透标注：旧链 Stage4 结构审批、
 * Stage5 闭合与人工闸工作包的取值面随旧链整层退役，本类型不再为它们保留
 * 第二份声明（响应里仍带旧键，由数据缝整体转型，不逐字段消费）。
 */

export interface EnumAction {
  kind: string
  label: string
  /** CLI 附加参数。命令行按这个拼，不按 free_flag。 */
  extra: string[] | null
  /** 花不花钱由服务端算好下发。 */
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
    review_mode: string
    review_scope: string
    approval_projection: string[]
    repair_actions: string[]
    repair_owners: string[]
    session_scope: string
    recovery_entries: string[]
    executor_content_limit: number
    /** [blocker, scope][]：审批闸归属映射。 */
    approval_bindings: [string, string][]
  }
}

export interface EnumsResponse {
  pipeline_steps: string[]
  job_kinds: Record<string, EnumJobKind>
  actions: EnumAction[]
  /**
   * This is an honest display boundary, not an access-control boundary: the server still
   * sends the complete response and the adapter projects it before a Screen sees it.
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
