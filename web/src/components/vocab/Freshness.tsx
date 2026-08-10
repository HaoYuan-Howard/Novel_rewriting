import { freshToken, type DotShape, type FreshnessDisplay } from '@/constants/vocabulary'

/**
 * 状态语言 = 色 + 形 + 文字，三者齐备。
 *
 * 这一目录（components/vocab/）是全前端**唯一**允许写 inline style 的地方：
 * `bg-${freshness}` 这种 class 名 Tailwind 扫不到，而把颜色复制成一张 class 表
 * 就等于把设计词汇存了第二份，两份一定会漂移。
 *
 * 规矩：dot 不能单独出现。同一行里必须有它的文字标签（FreshnessTag 或等价文案）。
 */

function shapeStyle(shape: DotShape, color: string, size: number): React.CSSProperties {
  const base: React.CSSProperties = {
    display: 'inline-block',
    flex: 'none',
    width: size,
    height: size,
    boxSizing: 'border-box',
  }
  if (shape === 'circle') return { ...base, borderRadius: '50%', background: color }
  if (shape === 'square')
    return { ...base, borderRadius: 1.5, background: color, transform: 'rotate(45deg)' }
  return {
    ...base,
    borderRadius: '50%',
    border: '1.5px dashed ' + color,
    background: 'transparent',
  }
}

export function FreshnessDot({ f, size = 9 }: { f: FreshnessDisplay; size?: number }) {
  const m = freshToken(f)
  return <span aria-hidden style={shapeStyle(m.shape, m.color, size)} />
}

export function FreshnessTag({ f }: { f: FreshnessDisplay }) {
  const m = freshToken(f)
  return (
    <span
      className="whitespace-nowrap rounded-[3px] px-5 py-1 text-micro"
      style={{ color: m.color, background: m.bg }}
      title={m.desc}
    >
      {m.label}
    </span>
  )
}

/** 点 + 文字一体。能用这个就别单独用 dot。 */
export function FreshnessBadge({ f, size = 9 }: { f: FreshnessDisplay; size?: number }) {
  const m = freshToken(f)
  return (
    <span className="inline-flex items-center gap-6 whitespace-nowrap text-tag" title={m.desc}>
      <FreshnessDot f={f} size={size} />
      <span style={{ color: m.color }}>{m.label}</span>
    </span>
  )
}

/** 形状图例。四态放一起，让人知道形状本身在说话。 */
export function FreshnessLegend() {
  const all: FreshnessDisplay[] = ['fresh', 'stale', 'unknown', 'missing']
  return (
    <span className="inline-flex flex-wrap items-center gap-12">
      {all.map((f) => (
        <FreshnessBadge key={f} f={f} />
      ))}
    </span>
  )
}
