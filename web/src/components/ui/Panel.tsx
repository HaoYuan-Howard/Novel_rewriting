import type { ReactNode } from 'react'
import { cx } from '@/lib/cx'

/** 卡片。11px 圆角、1px 描边、几乎不用阴影 —— 全站的基本面。 */
export function Panel({
  children,
  className,
  tone = 'card',
}: {
  children: ReactNode
  className?: string
  tone?: 'card' | 'sunken' | 'dashed'
}) {
  return (
    <div
      className={cx(
        'overflow-hidden rounded-card',
        tone === 'dashed' ? 'border border-dashed border-line-dash bg-sunken' : 'border border-line',
        tone === 'card' && 'bg-card',
        tone === 'sunken' && 'bg-sunken',
        className,
      )}
    >
      {children}
    </div>
  )
}

/**
 * 面板头。sticky 时 top 走 --head-h，不让每一屏各自硬编码 top:118px。
 */
export function PaneHeader({
  title,
  meta,
  right,
  sticky,
  className,
}: {
  title: ReactNode
  meta?: ReactNode
  right?: ReactNode
  sticky?: boolean
  className?: string
}) {
  return (
    <div
      className={cx(
        'flex items-center gap-10 border-b border-line-soft bg-sunken-2 px-18 py-11',
        sticky && 'sticky top-0 z-2',
        className,
      )}
    >
      <span className="text-section font-semibold">{title}</span>
      {meta ? <span className="text-tag text-ink-4">{meta}</span> : null}
      {right ? <span className="ml-auto flex items-center gap-8">{right}</span> : null}
    </div>
  )
}

/** 区块小标题 */
export function SectionTitle({ children, note }: { children: ReactNode; note?: ReactNode }) {
  return (
    <div className="flex items-baseline gap-10">
      <span className="text-section font-semibold">{children}</span>
      {note ? <span className="text-tag text-ink-4">{note}</span> : null}
    </div>
  )
}
