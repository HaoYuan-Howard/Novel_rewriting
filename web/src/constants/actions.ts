import type { RequiredActionInput } from '@/types/jobs'

/**
 * 动作三分类。
 *
 * 花不花钱**只看服务端下发的 `paid`**，会不会写盘**只看服务端下发的 `writes_disk`**
 * ——前端一行判定都不写（FE-03）。`/api/enums` 的 30 条动作每条都带这两格
 * （writes_disk 由 WRITE_EFFECTS 折出来，落点在 novel/api/enums.py）。
 *
 * 缺格的组合画成 `unclassified`：「不知道它会不会写盘」和「它只读」是两回事。
 */

export type ActionClass = 'paid' | 'writes' | 'read' | 'unclassified'

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
  required_inputs?: RequiredActionInput[]
}

function sameExtra(a: string[] | null, b: string[] | null): boolean {
  const x = a ?? []
  const y = b ?? []
  return x.length === y.length && x.every((v, i) => v === y[i])
}

/**
 * 把一个 (kind, extra) 引用还原成服务端下发的那条动作。
 * **只是查表**，不是判定：paid / writes_disk 永远是服务端算好的那个值。
 * 精确匹配不到就退回 kind 级的 paid（writes_disk 不退——kind 级说不清带 flag
 * 之后写不写盘）；再查不到就返回 null，调用方画成「未标注」。
 */
export function lookupAction(
  enums: { actions: ClassifiableAction[]; job_kinds: Record<string, { paid: boolean }> } | undefined,
  ref: { kind: string; extra: string[] | null } | null | undefined,
): ClassifiableAction | null {
  if (!enums || !ref) return null
  const exact = enums.actions.find(
    (a) => a.kind === ref.kind
      && sameExtra(a.extra, ref.extra),
  )
  if (exact) return exact
  const byKind = enums.job_kinds[ref.kind]
  if (byKind) return { kind: ref.kind, extra: ref.extra, paid: byKind.paid }
  return null
}

/**
 * 分类。**只认服务端字段**：paid → writes_disk → 缺格即 `unclassified`，
 * 不默认成免费、也不默认成只读（U-00 用在动作分类上）。
 */
export function classifyAction(a: ClassifiableAction | null | undefined): ActionClass {
  if (!a) return 'unclassified'
  if (a.paid) return 'paid'
  if (a.writes_disk !== undefined) return a.writes_disk ? 'writes' : 'read'
  return 'unclassified'
}
