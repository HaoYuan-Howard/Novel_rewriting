/**
 * 动作三分类。
 *
 * 花不花钱**只看服务端下发的 `paid`**，前端一行判定都不写。
 * 但还有第三类：免费、却会改盘上的东西（刷新指标、提升产物、落定稿、归档旧轮、拼装成书）。
 * 它和「纯读」不是一回事，值得一个中间态的视觉。
 *
 * `/api/enums` 目前**没有** `writes_disk` 字段，只有 `free_flag`（CLI flag 串），
 * 所以这个分类只能在前端做一张表。表按结构化的 `extra` / `kind` 精确匹配，
 * **不对 label 或 detail 做正则**——文案改一个字正则就静默失效。
 * 已在 DELIVERY.md 里写明：这个字段该由后端下发。
 */

export type ActionClass = 'paid' | 'writes' | 'read' | 'unclassified'

/** 免费但会写盘的 flag */
const WRITES_FLAGS = new Set([
  '--recheck',
  '--revalidate',
  '--remeasure',
  '--migrate',
  '--assemble',
  '--approve',
  '--apply-edit',
])

/** 整个 kind 都会写盘（不带 flag 时的默认行为就是产出新产物） */
const WRITES_KINDS = new Set([
  'n1_partition', // stage1 摄入+分章：确定性切分，但写产物
  'stage5_review', // 全书验收：落一份报告
  'n0_extras', // 旧 mock 的 stage0 节点
  'stage0.edit',
  'stage0.approve',
  'stage1.partition',
])

export const ACTION_CLASS_LABEL: Record<ActionClass, string> = {
  paid: '调 LLM · 花真钱',
  writes: '写盘',
  read: '只读',
  unclassified: '未标注',
}

export interface ClassifiableAction {
  kind: string
  extra: string[] | null
  paid: boolean
  writes_disk?: boolean
}

function sameExtra(a: string[] | null, b: string[] | null): boolean {
  const x = a ?? []
  const y = b ?? []
  return x.length === y.length && x.every((v, i) => v === y[i])
}

/**
 * 把一个 (kind, extra) 引用还原成服务端下发的那条动作。
 * **只是查表**，不是判定：paid 永远是服务端算好的那个值。
 * 精确匹配不到就退回 kind 级的 paid；再查不到就返回 null，
 * 调用方会画成「未标注」，而不是默认成免费只读。
 */
export function lookupAction(
  enums: { actions: ClassifiableAction[]; job_kinds: Record<string, { paid: boolean }> } | undefined,
  ref: { kind: string; extra: string[] | null } | null | undefined,
): ClassifiableAction | null {
  if (!enums || !ref) return null
  const exact = enums.actions.find((a) => a.kind === ref.kind && sameExtra(a.extra, ref.extra))
  if (exact) return exact
  const byKind = enums.job_kinds[ref.kind]
  if (byKind) return { kind: ref.kind, extra: ref.extra, paid: byKind.paid }
  return null
}

/**
 * 分类。表里查不到的组合画成 `unclassified`，**不默认成只读**——
 * 「不知道它会不会写盘」和「它只读」是两回事（U-00 用在动作分类上）。
 */
export function classifyAction(a: ClassifiableAction | null | undefined): ActionClass {
  if (!a) return 'unclassified'
  if (a.paid) return 'paid'
  if (a.writes_disk !== undefined) return a.writes_disk ? 'writes' : 'read'
  if (WRITES_KINDS.has(a.kind)) return 'writes'
  if (a.extra && a.extra.some((x) => WRITES_FLAGS.has(x))) return 'writes'
  return 'read'
}
