import {
  AGENT_STOPPED,
  BADGE,
  GATE,
  JOB,
  NOT_RUN,
  SEV,
  type Badge,
  type JobState,
  type JobStateToken,
} from '@/constants/vocabulary'

/**
 * badge 为 null 就是「没跑过」，**不会掉回 pass**。
 * amber 的文案自己写明了它不是绿的。
 */
export function CheckBadge({ badge }: { badge: Badge | null }) {
  const m = badge ? BADGE[badge] : NOT_RUN
  return (
    <span
      className="whitespace-nowrap rounded-chip px-10 py-3 text-sm2 font-semibold"
      style={{ color: m.color, background: m.bg }}
    >
      {m.label}
    </span>
  )
}

/** 徽章的形。fail 用 hard 的方块，amber 用 unver 的虚线圆，没跑过是空心虚线圆。 */
export function CheckMark({ badge }: { badge: Badge | null }) {
  const base: React.CSSProperties = {
    display: 'inline-block',
    flex: 'none',
    width: 10,
    height: 10,
    boxSizing: 'border-box',
  }
  if (badge === 'pass')
    return <span aria-hidden style={{ ...base, borderRadius: '50%', background: BADGE.pass.color }} />
  if (badge === 'fail')
    return <span aria-hidden style={{ ...base, borderRadius: 1.5, background: SEV.hard.color }} />
  if (badge === 'amber')
    return (
      <span
        aria-hidden
        style={{ ...base, borderRadius: '50%', border: '1.5px dashed ' + BADGE.amber.color }}
      />
    )
  return (
    <span aria-hidden style={{ ...base, borderRadius: '50%', border: '1.5px dashed ' + NOT_RUN.mark }} />
  )
}

function StateTokenChip({ token }: { token: JobStateToken }) {
  const m = token
  const dot: React.CSSProperties = {
    display: 'inline-block',
    flex: 'none',
    width: 8,
    height: 8,
    boxSizing: 'border-box',
    ...(m.shape === 'circle'
      ? { borderRadius: '50%', background: m.color }
      : m.shape === 'square'
        ? { borderRadius: 1.5, background: m.color }
        : { borderRadius: '50%', border: '1.5px dashed ' + m.color }),
  }
  return (
    <span
      className="inline-flex items-center gap-7 whitespace-nowrap rounded-tag px-8 py-2 text-tag font-semibold"
      style={{ color: m.color, background: m.bg }}
    >
      <span aria-hidden style={dot} />
      {m.label}
    </span>
  )
}

export function JobStateChip({ state }: { state: JobState }) {
  return <StateTokenChip token={JOB[state]} />
}

/** 整书 AgentRun 的显式终态；单节点 JobState 不扩域。 */
export function AgentStoppedChip() {
  return <StateTokenChip token={AGENT_STOPPED} />
}

/** 闸门上的「✓ 已看过 / · 还没看」 */
export function GateChip({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className="whitespace-nowrap rounded-chip px-10 py-4 text-tag"
      style={
        ok
          ? { color: GATE.ok.chipColor, background: GATE.ok.chipBg }
          : { color: GATE.wait.chipColor, background: GATE.wait.chipBg }
      }
    >
      {(ok ? '✓ ' : '· ') + label}
    </span>
  )
}
