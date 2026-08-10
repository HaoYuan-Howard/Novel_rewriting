import type { ReactNode } from 'react'
import { cx } from '@/lib/cx'

/**
 * 并排壳 A：一栏正常流 + 一栏粘性侧栏。
 *
 * 并排是主视图，不是 tab。把左右做成两个 tab、或做成「点开看详情」的弹层，
 * 等于把这个工具的核心功能删掉。
 *
 * 两栏天然不等高（左边 1461 字原文 vs 右边 9 个结构化 beat）——解法是：
 *   · 页面唯一的滚动条归长文那一栏（Ctrl-F / PageDown / 滚动条即进度，都不能坏）
 *   · 结构化那一栏有界（9 拍 / 15 人 / 8 章），钉在旁边、只在自己更高时内滚
 * **不做滚动同步**：两侧长度无关，线性比例是错的；按偏移量算又会撞上
 * 「Python 码点下标 ≠ JS UTF-16 码元」那条坑。
 */
export function ComparePanes({
  left,
  right,
  /** 网格比例。用设计稿的原值：read 1:1 / stage4 1.15:0.85 / stage5 0.62:1.6 */
  ratio = '1fr 1fr',
  /** 哪一栏钉住。默认右栏（结构化那一栏）。 */
  sticky = 'right',
  gap = 18,
  className,
}: {
  left: ReactNode
  right: ReactNode
  ratio?: string
  sticky?: 'left' | 'right' | 'none'
  gap?: number
  className?: string
}) {
  const cols = ratio
    .split(' ')
    .map((c) => (c.includes('minmax') ? c : 'minmax(0,' + c + ')'))
    .join(' ')

  const stickyCls =
    'sticky top-[var(--head-h)] max-h-[var(--pane-max)] flex flex-col overflow-hidden'

  return (
    <div
      className={cx('grid min-w-0 items-start', className)}
      style={{ gridTemplateColumns: cols, gap }}
    >
      <div className={cx('min-w-0', sticky === 'left' && stickyCls)}>{left}</div>
      <div className={cx('min-w-0', sticky === 'right' && stickyCls)}>{right}</div>
    </div>
  )
}

/** 粘性栏里可滚的那一段。头固定，身体内滚。 */
export function PaneScroll({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('min-h-0 flex-1 overflow-y-auto', className)}>{children}</div>
}
