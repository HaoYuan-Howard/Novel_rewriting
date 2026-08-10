import type { ReactNode } from 'react'
import { GATE, SEV } from '@/constants/vocabulary'
import { Prose, type Measure } from '@/components/ui/Prose'
import { cx } from '@/lib/cx'

export interface CompareRow {
  key: string
  left: string
  right: string
  /** 这一块有没有被锚定到确定的拍上 */
  anchored: boolean
  /** 「这一行盖了 3 拍」这类说明 */
  note?: ReactNode
}

/**
 * 并排壳 B：行配对栅格。
 *
 * 两侧存在已知对应关系时，不要做两栏 —— 做一个栅格，
 * 每个对齐单位是横跨两列的一行。每行自己 grid-cols-2，两格自动等高，
 * 对齐是精确的、和文本长度无关，零 JS、零滚动同步。
 *
 * 对不齐的块必须有视觉标记（虚线左边框 + 说明）：
 * 没有这个标记，「这一行盖了 3 拍」和「这一行就是第 3 拍」长得一模一样。
 */
export function CompareRows({
  rows,
  captions,
  measure = 'compare',
  emptyNote,
}: {
  rows: CompareRow[]
  captions: [string, string]
  measure?: Measure
  emptyNote?: ReactNode
}) {
  if (!rows.length) return <>{emptyNote}</>

  return (
    <div className="flex flex-col">
      <div className="sticky top-0 z-2 grid grid-cols-2 gap-16 border-b border-line-soft bg-sunken-2 px-18 py-9">
        {captions.map((c) => (
          <span key={c} className="text-tag font-semibold text-ink-3">
            {c}
          </span>
        ))}
      </div>
      {rows.map((r) => (
        <div
          key={r.key}
          className={cx(
            'border-b border-line-row px-18 py-14',
            !r.anchored && 'border-l-4 border-l-transparent',
          )}
          style={
            r.anchored
              ? undefined
              : { borderLeftStyle: 'dashed', borderLeftColor: SEV.unver.border }
          }
        >
          {r.note ? (
            <div
              className="mb-8 inline-flex items-center gap-6 rounded-tag px-7 py-1 text-micro"
              style={
                r.anchored
                  ? { color: GATE.wait.chipColor, background: GATE.wait.chipBg }
                  : { color: SEV.unver.color, background: SEV.unver.bg }
              }
            >
              {r.note}
            </div>
          ) : null}
          <div className="grid grid-cols-2 gap-16">
            <Prose text={r.left} measure={measure} size="md" tone="ink-2" />
            <Prose text={r.right} measure={measure} size="md" />
          </div>
        </div>
      ))}
    </div>
  )
}
