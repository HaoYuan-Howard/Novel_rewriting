import { resolveGate } from '@/constants/gates'
import { lookupAction } from '@/constants/actions'
import { GATE, SPEND_INK, type ScreenName } from '@/constants/vocabulary'
import { useApprovals } from '@/hooks/useApprovals'
import { useAskJob } from '@/hooks/useChrome'
import { useEnums } from '@/hooks/useEnums'
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
  const ask = useAskJob()

  if (!doc) return null
  const view = resolveGate(screen, doc)
  if (!view) return null

  const spawns = doc.spawns[view.key] ?? []
  const resolved = spawns.map((s) => lookupAction(enums.data, s))
  const willSpend = resolved.some((a) => a?.paid)
  // 有未知动作时不假装它免费
  const unknownSpawn = spawns.length > 0 && resolved.some((a) => a === null)

  /**
   * 确认这道闸门会不会动产物盘。
   *
   * ⚠ **`paid` 和 `writes_disk` 必须一起给**：`classifyAction` 在 `paid=false`
   * 时接着看 `writes_disk`，缺格即画成「未标注」——而「未标注」的意思是
   * **不知道它会不会写盘**（U-08）。s3 / s4 这种没有 spawn 的闸门我们其实完全知道：
   * 确认只写 `_service/` 下的人工审批记录，产物盘由 CLI 独占（`R-11.3`）。
   * 只给 `paid` 会把一个已知免费、不动产物的动作说成「不知道」，
   * 而那正好稀释了这个信号本身要保护的东西。
   * ⚠ 有 spawn 时折叠 spawn 的那一格；任一 spawn 缺格就**不给**，
   * 让它照旧落回「未标注」——不拿 `undefined` 当 `false` 用。
   */
  const spawnWrites = spawns.length === 0
    ? false
    : resolved.every((a) => a?.writes_disk !== undefined)
      ? resolved.some((a) => a?.writes_disk)
      : undefined

  const off = view.done || view.blocked || approve.isPending || spawns.length > 1

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
                : {
                    kind: 'gate.' + view.key,
                    extra: null,
                    paid: willSpend,
                    writes_disk: spawnWrites,
                  }
            }
            disabled={off}
            disabledReason={
              view.blocked ? view.blockedReason
                : view.done ? '已经确认过了'
                  : spawns.length > 1 ? '服务端返回了多个作业，需合并成一个可预览动作' : ''
            }
            onClick={() => {
              const spawn = spawns[0]
              if (!spawn) {
                approve.mutate(view.key)
                return
              }
              ask(
                {
                  kind: spawn.kind,
                  label: spawn.label,
                  extra: spawn.extra,
                  mode: spawn.mode ?? null,
                  required_inputs: resolved[0]?.required_inputs,
                  sid: doc.sid,
                  ordinal: null,
                },
                undefined,
                async () => { await approve.mutateAsync(view.key) },
              )
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
