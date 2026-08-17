import { useEffect, useRef, useState } from 'react'
import { cx } from '@/lib/cx'

export interface ChapterStripItem {
  n: number
  active: boolean
  /** 服务端知不知道这一章 —— 灰掉的不是「不能点」，是「点了会看到没样本」 */
  dim: boolean
  go: () => void
}

const NAV_BTN =
  'h-28 w-24 flex-none cursor-pointer rounded-btn border border-line-btn bg-card text-body text-ink-3 disabled:cursor-not-allowed disabled:opacity-40'

/**
 * 章号条。哪些章有数据由数据层给（mock 里就是「样本只抓了第 3 章」），
 * 渲染层不认识 `n === 3` 这种事。
 *
 * 章多了不撑破屏头：条体横向滚动，当前章变化时自动滚进可视区；
 * 两端 ‹ › 是上一章 / 下一章，末尾的「第 N 章」输入回车直跳（非法值不跳）。
 */
export function ChapterStrip({ items }: { items: ChapterStripItem[] }) {
  const activeRef = useRef<HTMLButtonElement>(null)
  const [jump, setJump] = useState('')

  const idx = items.findIndex((c) => c.active)
  const prev = idx > 0 ? items[idx - 1] : null
  const next = idx >= 0 && idx < items.length - 1 ? items[idx + 1] : null
  const activeN = idx >= 0 ? items[idx].n : null

  // 换章后把当前章滚进可视区（nearest：已经在视野里就不动）
  useEffect(() => {
    activeRef.current?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
  }, [activeN])

  const jumpTo = () => {
    const n = Number(jump)
    const hit = items.find((c) => c.n === n)
    if (!hit) return
    setJump('')
    hit.go()
  }

  return (
    <div className="flex min-w-0 items-center gap-6">
      <button
        type="button"
        aria-label="上一章"
        disabled={!prev}
        onClick={() => prev?.go()}
        className={NAV_BTN}
      >
        ‹
      </button>
      <div className="flex min-w-0 items-center gap-6 overflow-x-auto">
        {items.map((c) => (
          <button
            key={c.n}
            ref={c.active ? activeRef : undefined}
            type="button"
            onClick={c.go}
            aria-current={c.active ? 'true' : undefined}
            title={c.dim ? '这一章的响应没有在样本里' : undefined}
            className={cx(
              'h-28 w-30 flex-none cursor-pointer rounded-btn border text-body',
              c.active
                ? 'border-ink bg-ink text-card'
                : cx('border-line-btn bg-card', c.dim ? 'text-ink-6' : 'text-ink'),
            )}
          >
            {c.n}
          </button>
        ))}
      </div>
      <button
        type="button"
        aria-label="下一章"
        disabled={!next}
        onClick={() => next?.go()}
        className={NAV_BTN}
      >
        ›
      </button>
      <label className="flex flex-none items-center gap-4 text-tag text-ink-4">
        第
        <input
          value={jump}
          onChange={(e) => setJump(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') jumpTo()
          }}
          aria-label="跳到第几章"
          className="w-[52px] rounded-btn border border-line-btn bg-card px-6 py-3 text-center text-body text-ink"
        />
        章
      </label>
    </div>
  )
}
