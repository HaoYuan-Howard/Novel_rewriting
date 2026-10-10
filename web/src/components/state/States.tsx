import type { ReactNode } from 'react'
import { errorBody, errorHint, errorTitle, isApiError } from '@/api/errors'
import { SEV } from '@/constants/vocabulary'
import { cx } from '@/lib/cx'
import { LinkButton } from '@/components/ui/ActionButton'

/**
 * 这一层就是 U-00 的落点：
 * 「查过、没问题」和「查不了 / 没查 / 不知道」在界面上必须长得不一样。
 */

export function LoadingPane({ label = '取数中', rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div className="flex flex-col gap-10 p-20" role="status" aria-busy>
      <div className="text-tag text-ink-4">{label} …</div>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-13 animate-pulse rounded-tag bg-inset"
          style={{ width: 100 - i * 12 + '%' }}
        />
      ))}
    </div>
  )
}

/**
 * 「下一条命令」单独一行。**没有就什么都不画**——不占位、不写「暂无」。
 *
 * ⚠ 它与错误正文分开渲染，因为后端本来就分成两格给（`ErrorBody.message` / `.hint`）。
 * 糊回正文里，用户就得从一段中文里自己找那条命令，而「这条失败到底有没有出路」
 * 也就变回不可判定的了。
 * ⚠ 每一处渲染 `errorBody()` 的地方都要带上它，否则后端给了出路而界面把它吞掉——
 * 那比不给更坏：人会以为只能干等。
 */
export function ErrorHint({ error }: { error: unknown }) {
  const hint = errorHint(error)
  if (!hint) return null
  // ⚠ 外层用 `span.block` 而不是 `div`：几处调用点的容器本身是 `<span>`
  //（DepthScreen 那条就是），`div` 塞进 `span` 是非法嵌套。
  return (
    <span className="mt-6 block text-tag text-ink-3">
      下一条命令：<span className="font-mono break-all select-all">{hint}</span>
    </span>
  )
}

/** 取不到。不显示 0、不显示空、不显示灰色占位。 */
export function LoadFailed({
  error,
  onRetry,
  compact,
}: {
  error: unknown
  onRetry?: () => void
  compact?: boolean
}) {
  const status = isApiError(error) && error.status ? '（HTTP ' + error.status + '）' : ''
  return (
    <div
      className={cx(
        'flex flex-col items-start gap-8 rounded-block border border-dashed',
        compact ? 'p-14' : 'p-24',
      )}
      style={{ borderColor: SEV.unver.border, background: SEV.unver.bg }}
    >
      <div className="flex items-center gap-8">
        <span
          aria-hidden
          className="inline-block h-9 w-9 rounded-full border-[1.5px] border-dashed"
          style={{ borderColor: SEV.unver.color }}
        />
        <span className="text-section font-semibold" style={{ color: SEV.unver.color }}>
          {errorTitle(error)}
          {status}
        </span>
      </div>
      <div className="prose-cn max-w-[46em] text-body text-ink-2">
        {errorBody(error)}
        <ErrorHint error={error} />
      </div>
      {onRetry ? <LinkButton onClick={onRetry}>重试</LinkButton> : null}
    </div>
  )
}

/**
 * 还没跑。不是「暂无数据」——
 * 用户需要知道的是「这一步还没做 / 做了但被拦下了 / 上游还没好」，以及现在该点哪个按钮。
 */
export function NotRunYet({
  title,
  body,
  action,
  compact,
}: {
  title: string
  body: string
  action?: ReactNode
  compact?: boolean
}) {
  return (
    <div
      className={cx(
        'flex flex-col items-start gap-10 rounded-block border border-dashed border-line-dash bg-sunken',
        compact ? 'p-14' : 'p-24',
      )}
    >
      <div className="text-section font-semibold">{title}</div>
      <div className="prose-cn max-w-[46em] text-body text-ink-3">{body}</div>
      {action}
    </div>
  )
}
