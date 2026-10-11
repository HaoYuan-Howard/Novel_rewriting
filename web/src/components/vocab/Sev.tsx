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
