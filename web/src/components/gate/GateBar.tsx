import { resolveGate } from '@/constants/gates'
import { lookupAction } from '@/constants/actions'
import { GATE, SPEND_INK, type ScreenName } from '@/constants/vocabulary'
import { useApprovals } from '@/hooks/useApprovals'
import { useEnums } from '@/hooks/useEnums'
import { useChrome } from '@/hooks/useChrome'
import { ActionButton } from '@/components/ui/ActionButton'
import { GateChip, GateMark } from '@/components/vocab/Badges'

/**
 * 每屏底部那条闸门。上一层没确认，下一层不许开跑。
 *
 * 按钮是不是橙的，取决于这次确认会不会发起花钱的下游 ——
 * 而「会发起什么」由服务端随验收记录一起下发，前端只是查 /api/enums 拿它的 paid。
 */
export function GateBar({ screen }: { screen: ScreenName }) {
  const { doc, approve } = useApprovals()
  const enums = useEnums()
  const { openDrawer } = useChrome()

  if (!doc) return null
  const view = resolveGate(screen, doc)
  if (!view) return null

  const spawns = doc.spawns[view.key] ?? []
  const resolved = spawns.map((s) => lookupAction(enums.data, s))
  const willSpend = resolved.some((a) => a?.paid)
  // 有未知动作时不假装它免费
  const unknownSpawn = spawns.length > 0 && resolved.some((a) => a === null)

  const off = view.done || view.blocked || approve.isPending

  return (
    <div className="mx-30 mt-20 mb-4 overflow-hidden rounded-card border border-line-btn bg-card">
      <div className="flex items-center gap-16 px-20 py-15">
        <GateMark ok={view.done} />
        <div className="flex min-w-0 flex-col gap-3">
          <span className="text-read font-semibold">{view.title}</span>
          <span className="text-sm2 text-ink-3">{view.note}</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-8">
          {view.chips.map((c) => (
            <GateChip key={c.label} ok={c.ok} label={c.label} />
          ))}
          <ActionButton
            action={
              unknownSpawn
                ? null
                : { kind: 'gate.' + view.key, extra: null, paid: willSpend }
            }
            disabled={off}
            disabledReason={view.blocked ? view.blockedReason : view.done ? '已经确认过了' : ''}
            onClick={() => {
              approve.mutate(view.key)
              if (spawns.length) openDrawer()
            }}
          >
            {view.btn}
          </ActionButton>
        </div>
      </div>

      {spawns.length ? (
        <div className="flex flex-wrap items-center gap-8 border-t border-line-soft bg-sunken-2 px-20 py-9 text-tag text-ink-4">
          <span>确认后会发起：</span>
          {spawns.map((s) => {
            const a = lookupAction(enums.data, s)
            return (
              <span
                key={s.kind + (s.extra ?? []).join(',')}
                className="rounded-tag bg-inset px-7 py-2"
                style={a?.paid ? { color: SPEND_INK } : undefined}
              >
                {s.label}
                {a === null ? ' · 未标注' : a.paid ? ' · 花钱' : ''}
              </span>
            )
          })}
        </div>
      ) : null}

      {view.doneNote ? (
        <div
          className="border-t border-line-soft px-20 py-10 text-sm2"
          style={{ background: GATE.doneStrip.bg, color: GATE.doneStrip.color }}
        >
          {view.doneNote}
        </div>
      ) : null}
    </div>
  )
}
