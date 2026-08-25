/**
 * 人工验收记录。
 *
 * 在真产品里这是服务端的东西（人点了哪些确认、是否已经批准开写）。
 * mock 版本里它由 mock.ts 存在 localStorage —— 但**取数路径和真实响应完全一样**：
 * 走 api.getApprovals() / api.approveGate() / api.approveStructure()，
 * 屏与组件从不直接碰传输层。
 *
 * 这些字段合成一份文档而不是各存一张 map：若 gate.s2 为真而 seen 丢了，
 * 闸门会画出「✓ 已确认」旁边挂两个灰 chip 的自相矛盾状态。
 * 一份文档、一次写、一个生命周期。
 */

export type GateKey = 's0' | 's1' | 's2' | 's3' | 's4' | 's5' | 's6' | 's7'

export const GATE_KEYS: GateKey[] = ['s0', 's1', 's2', 's3', 's4', 's5', 's6', 's7']

/** stage2 有两屏，两屏都要看过才能确认 */
export type SeenKey = 'read' | 'global'

/** 一条会被发起的动作。paid 不在这里 —— 它要去 /api/enums 里查，那才是权威。 */
export interface ActionRef {
  kind: string
  label: string
  extra: string[] | null
}

/**
 * Stage4 结构审批的取值面来自 `/api/enums.structure_approval_decisions`。
 * 这里刻意用 string，不在 TypeScript 再抄一份服务端枚举。
 */
export interface StructureChapterApproval {
  output_ordinal: number
  decision: string
  reason: string
}

export interface StructureApprovalRequest {
  subject_sha256: string
  actor: string
  reason: string
  chapters: StructureChapterApproval[]
}

export interface StructureApprovalDecision extends StructureApprovalRequest {
  decided_at: string
}

export interface StructureApprovalView {
  /** 取值面来自 `/api/enums.structure_approval_states`。 */
  state: string
  current_subject_sha256: string | null
  current_decision: StructureApprovalDecision | null
  history: StructureApprovalDecision[]
}

export interface ApprovalDoc {
  v: 3
  sid: string
  gate: Record<GateKey, boolean>
  seen: Record<SeenKey, boolean>
  /** 是否已经批准全书闭卷开写。 */
  writing: boolean
  /** 独立于 writing 的、绑定当前完整 Stage4 计划字节的逐章结构裁决。 */
  structure: StructureApprovalView
  /**
   * 确认某一道闸门之后服务端会发起什么。
   * 由服务端下发，界面据此决定按钮是不是橙的 —— 免得「谁会花钱」在前后端各存一份。
   */
  spawns: Partial<Record<GateKey, ActionRef[]>>
}

export interface ApprovalBackfillResult {
  approvals: ApprovalDoc
  /** 这些章来自盘面补录，不伪装成用户逐屏点击。 */
  backfilled: GateKey[]
}
