import { cx } from '@/lib/cx'

export interface ChapterStripItem {
  n: number
  active: boolean
  /** 服务端知不知道这一章 —— 灰掉的不是「不能点」，是「点了会看到没样本」 */
  dim: boolean
  go: () => void
}

/**
 * 章号条。哪些章有数据由数据层给（mock 里就是「样本只抓了第 3 章」），
 * 渲染层不认识 `n === 3` 这种事。
 */
export function ChapterStrip({ items }: { items: ChapterStripItem[] }) {
  return (
    <div className="flex items-center gap-6">
      {items.map((c) => (
        <button
          key={c.n}
          type="button"
          onClick={c.go}
          title={c.dim ? '这一章的响应没有在样本里' : undefined}
          className={cx(
            'h-28 w-30 cursor-pointer rounded-btn border text-body',
            c.active
              ? 'border-ink bg-ink text-card'
              : cx('border-line-btn bg-card', c.dim ? 'text-ink-6' : 'text-ink'),
          )}
        >
          {c.n}
        </button>
      ))}
    </div>
  )
}
