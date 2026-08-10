import { cx } from '@/lib/cx'

/**
 * 中文正文块。
 *
 * - 段落之间用间距分隔，不用「首行缩进 + 零间距」的纸书排法
 *   （原文是网文，段落极短，常常一句一段）
 * - 不 justify（中文两端对齐会拉出难看的字距）
 * - 宽度有上限，且**并排视图里两栏的上限必须一致**，否则对照会错位
 *   —— 所以 measure 是从外面传进来的，不是每个块自己定的
 */

export type Measure = 'compare' | 'read' | 'solo' | 'full'

const MEASURE: Record<Measure, string> = {
  compare: 'max-w-[var(--measure-compare)]',
  read: 'max-w-[var(--measure-read)]',
  solo: 'max-w-[var(--measure-solo)]',
  full: '',
}

export type ProseSize = 'sm' | 'md' | 'lg'

const SIZE: Record<ProseSize, string> = {
  sm: 'text-section', // 13.5 / 1.9
  md: 'text-read', // 14 / 2.05
  lg: 'text-read-xl', // 15 / 2.15
}

/** 把服务端给的整块文本按空行切成段。**不按下标切**（U-13）。 */
export function paragraphsOf(text: string): string[] {
  return text
    .split(/\n+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

export function Prose({
  text,
  measure = 'compare',
  size = 'md',
  tone = 'ink',
  className,
}: {
  text: string
  measure?: Measure
  size?: ProseSize
  tone?: 'ink' | 'ink-2' | 'muted'
  className?: string
}) {
  const paras = paragraphsOf(text)
  return (
    <div
      className={cx(
        'prose-cn flex flex-col gap-8',
        SIZE[size],
        MEASURE[measure],
        tone === 'ink' && 'text-ink',
        tone === 'ink-2' && 'text-ink-2',
        tone === 'muted' && 'text-ink-4',
        className,
      )}
    >
      {paras.map((p, i) => (
        <p key={i} className="m-0">
          {p}
        </p>
      ))}
    </div>
  )
}
