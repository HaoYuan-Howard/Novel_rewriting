/**
 * 人工验收记录。
 *
 * 在真产品里这是服务端的东西（人点了哪些确认、挑了哪几章深度重构）。
 * mock 版本里它由 mock.ts 存在 localStorage —— 但**取数路径和真实响应完全一样**：
 * 走 api.getApprovals() / api.approveGate()，屏与组件从不直接碰它。
 *
 * 四个字段合成一份文档而不是四个 map：若 gate.s2 为真而 seen 丢了，
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
  mode?: string | null
}

export interface ApprovalDoc {
  v: 1
  sid: string
  gate: Record<GateKey, boolean>
  seen: Record<SeenKey, boolean>
  /** 深度重构的章号。默认值由数据层给，组件里永远不写 ?? [1,2,3] */
  deep: number[]
  /** 是否已经开写（false 时 /stage5 要落到挑深度章节那一屏） */
  writing: boolean
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
