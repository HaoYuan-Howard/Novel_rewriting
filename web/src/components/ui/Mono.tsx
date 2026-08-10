import type { ReactNode } from 'react'
import { cx } from '@/lib/cx'

/** sid / sha1 / 字数 / rule id / 命令行。等宽 + 数字等宽 + 允许断行。 */
export function Mono({
  children,
  className,
  title,
  style,
}: {
  children: ReactNode
  className?: string
  title?: string
  /** 只用于从 vocabulary.ts 取来的状态色；不许写颜色字面量 */
  style?: React.CSSProperties
}) {
  return (
    <span className={cx('font-mono tabular-nums', className)} title={title} style={style}>
      {children}
    </span>
  )
}
