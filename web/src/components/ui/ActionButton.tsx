import type { ReactNode } from 'react'
import { classifyAction, type ActionClass, type ClassifiableAction } from '@/constants/actions'
import { cx } from '@/lib/cx'

/**
 * 三类动作，视觉必须一眼分得开 —— 而且**权重是反过来的**：
 * 免费动作是日常操作，花钱动作是需要停一下的操作。
 * 36 个动作里只有 7 个真花钱。
 *
 * 第四类 `unclassified` 是给「表里查不到」的组合的：
 * 不知道它会不会写盘 ≠ 它只读。
 */

const BASE =
  'inline-flex items-center gap-8 rounded-btn text-body-lg font-semibold whitespace-nowrap transition-colors'

const SIZE = {
  md: 'px-18 py-9',
  sm: 'px-12 py-6 text-body font-normal',
} as const

const TONE: Record<ActionClass, string> = {
  paid: 'border-none bg-spend text-card',
  writes: 'border-none bg-ink text-card',
  read: 'border border-line-btn bg-card text-ink-2 hover:border-ink-4',
  unclassified: 'border border-dashed border-line-btn bg-card text-ink-3 hover:border-ink-4',
}

const CORNER: Partial<Record<ActionClass, { text: string; cls: string }>> = {
  writes: { text: '写盘', cls: 'bg-white/22 text-card' },
  unclassified: { text: '未标注', cls: 'bg-inset text-ink-4' },
}

export function ActionButton({
  action,
  children,
  onClick,
  disabled,
  disabledReason,
  size = 'md',
  className,
}: {
  /** 分类的唯一依据。paid 由服务端下发，写盘由 constants/actions.ts 的精确表判。 */
  action: ClassifiableAction | null
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  /** 禁用时说明原因 —— 「点不动」必须同时说清为什么 */
  disabledReason?: string
  size?: keyof typeof SIZE
  className?: string
}) {
  const kind = classifyAction(action)
  const corner = CORNER[kind]
  const btn = (
    <button
      type="button"
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      aria-disabled={disabled}
      className={cx(
        BASE,
        SIZE[size],
        TONE[kind],
        disabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer',
        className,
      )}
    >
      {children}
      {corner ? (
        <span className={cx('rounded-tag px-6 py-1 text-micro font-normal', corner.cls)}>
          {corner.text}
        </span>
      ) : null}
    </button>
  )
  // Chrome 对 disabled 按钮不触发鼠标事件 —— title 挂在外层 span 上，
  // 「点不动」的原因才 hover 得到（布局保持 inline-flex 不破行）
  return disabled ? (
    <span title={disabledReason} className="inline-flex">
      {btn}
    </span>
  ) : (
    btn
  )
}

/** 纯导航 / 纯读的次要按钮。不参与动作分类。 */
export function LinkButton({
  children,
  onClick,
  className,
  disabled,
  title,
}: {
  children: ReactNode
  onClick?: () => void
  className?: string
  disabled?: boolean
  title?: string
}) {
  const btn = (
    <button
      type="button"
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      title={disabled ? undefined : title}
      className={cx(
        'inline-flex items-center gap-6 rounded-btn border border-line-btn bg-card px-12 py-6 text-body text-ink-2 whitespace-nowrap',
        disabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer hover:border-ink-6',
        className,
      )}
    >
      {children}
    </button>
  )
  // 同 ActionButton：disabled 按钮在 Chrome 不触发鼠标事件，title 交给外层 span
  return disabled ? (
    <span title={title} className="inline-flex">
      {btn}
    </span>
  ) : (
    btn
  )
}
