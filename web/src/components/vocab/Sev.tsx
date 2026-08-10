import { METRIC_RULE_COLOR, SEV, type Severity } from '@/constants/vocabulary'

/**
 * 四级严重度的形与色。
 *
 * metric 不是问题，是度量 —— 它画成一条底线，不是点，也绝不用警告色。
 * unver 是独立的第四种：虚线圆点 + 虚线边框，**绝不能并进「通过」**。
 */

export function SevMark({ sev, size = 9 }: { sev: Severity; size?: number }) {
  const m = SEV[sev]
  const base: React.CSSProperties = {
    display: 'inline-block',
    flex: 'none',
    width: size,
    height: size,
    boxSizing: 'border-box',
  }
  if (sev === 'hard') return <span aria-hidden style={{ ...base, background: m.color, borderRadius: 1.5 }} />
  if (sev === 'info')
    return <span aria-hidden style={{ ...base, background: m.color, borderRadius: '50%' }} />
  if (sev === 'unver')
    return (
      <span
        aria-hidden
        style={{
          ...base,
          border: '1.5px dashed ' + m.color,
          borderRadius: '50%',
          background: 'transparent',
        }}
      />
    )
  // metric：一条底线
  return (
    <span
      aria-hidden
      style={{
        display: 'inline-block',
        flex: 'none',
        width: size + 2,
        height: 5,
        background: 'transparent',
        borderBottom: '2px solid ' + METRIC_RULE_COLOR,
        boxSizing: 'border-box',
      }}
    />
  )
}

export function SevChip({ sev, label }: { sev: Severity; label?: string }) {
  const m = SEV[sev]
  return (
    <span
      className="whitespace-nowrap rounded-tag border px-7 py-1 text-mini font-medium"
      style={{
        color: m.color,
        background: m.bg,
        borderColor: m.border,
        borderStyle: sev === 'unver' ? 'dashed' : 'solid',
      }}
    >
      {label ?? m.label}
    </span>
  )
}

/** 点 + 文字一体 */
export function SevBadge({ sev, label }: { sev: Severity; label?: string }) {
  const m = SEV[sev]
  return (
    <span className="inline-flex items-center gap-6 whitespace-nowrap text-tag">
      <SevMark sev={sev} />
      <span style={{ color: m.color }}>{label ?? m.label}</span>
    </span>
  )
}

/** 计数条：hard N · 提示 N · 度量 N · 查不了 N。四级一个都不许少、不许合并。 */
export function SevCounts({ counts }: { counts: Partial<Record<Severity, number>> }) {
  const order: Severity[] = ['hard', 'info', 'metric', 'unver']
  return (
    <span className="inline-flex flex-wrap items-center gap-10">
      {order.map((s) => (
        <SevBadge key={s} sev={s} label={SEV[s].label + ' ' + (counts[s] ?? 0)} />
      ))}
    </span>
  )
}
