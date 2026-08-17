import type { ReactNode } from 'react'
import { cx } from '@/lib/cx'

export interface ChapterGridItem {
  /** 阅读序章号，格子的唯一键 */
  ordinal: number
  active: boolean
}

/**
 * 章节格 / 章节索引。stage1 / stage4 / stage5 各造过一份，「当前章」画法互不一致；
 * 统一成 accent 系（bg-accent-bg + accent 描边），格子内容经 render prop 由调用方摆。
 *
 * 两种版式：`wrap` 是横排小格（stage4 / stage5 成稿网格），
 * `list` 是整行竖排索引（stage1）。表格状的章索引（stage5 验收章表）不套这个。
 */
export function ChapterGrid<T extends ChapterGridItem>({
  items,
  onPick,
  layout = 'wrap',
  className,
  children,
}: {
  items: T[]
  onPick: (ordinal: number) => void
  layout?: 'wrap' | 'list'
  className?: string
  /** 每格内容：章号 + 状态小字都由调用方摆 */
  children: (item: T) => ReactNode
}) {
  return (
    <div className={cx(layout === 'wrap' ? 'flex flex-wrap gap-8' : 'flex flex-col', className)}>
      {items.map((item) => (
        <button
          key={item.ordinal}
          type="button"
          onClick={() => onPick(item.ordinal)}
          aria-current={item.active ? 'true' : undefined}
          className={cx(
            'cursor-pointer text-left',
            layout === 'wrap'
              ? 'flex items-center gap-7 rounded-btn border px-9 py-5'
              : 'flex w-full flex-col gap-3 border-0 border-b border-line-row px-14 py-10',
            item.active
              ? layout === 'wrap'
                ? 'border-accent-line-2 bg-accent-bg font-semibold'
                : 'bg-accent-bg'
              : layout === 'wrap'
                ? 'border-line bg-card'
                : 'bg-card hover:bg-sunken-2',
          )}
        >
          {children(item)}
        </button>
      ))}
    </div>
  )
}
