import type { UseQueryResult } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { isNoSample } from '@/api/errors'
import type { ApiError } from '@/api/errors'
import { LoadFailed, LoadingPane } from './States'
import { MissingSample } from './MissingSample'

/**
 * 每屏四态，一个都不能少：加载中 / 出错 / 还没跑 / 正常。
 * 「还没跑」由各屏自己判断（服务端给的 exists / ctx_exists / plan_source 这类显式标志），
 * 因为它不是取数失败 —— 空数组也**不**算「还没跑」。
 */
export function QueryStates<T>({
  q,
  children,
  loadingLabel,
  loadingRows,
  missing,
  compactError,
}: {
  q: UseQueryResult<T>
  children: (data: T) => ReactNode
  loadingLabel?: string
  loadingRows?: number
  /** 章号屏 / 书屏：nosample 时用整屏的「没有样本」而不是一条错误框 */
  missing?: { title: string; chapters?: number[]; ordinal?: number; path?: string }
  compactError?: boolean
}) {
  if (q.isPending) return <LoadingPane label={loadingLabel} rows={loadingRows} />
  if (q.isError) {
    if (missing && isNoSample(q.error)) {
      return (
        <MissingSample
          error={q.error as ApiError}
          title={missing.title}
          chapters={missing.chapters}
          ordinal={missing.ordinal}
          path={missing.path}
        />
      )
    }
    return <LoadFailed error={q.error} onRetry={() => void q.refetch()} compact={compactError} />
  }
  return <>{children(q.data)}</>
}
