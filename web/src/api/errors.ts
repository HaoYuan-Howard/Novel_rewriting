/**
 * 错误归一。
 *
 * 界面必须能区分「后端没起来」和「这本书没有」——
 * 两种都画成一句「加载失败」，用户就没法判断该去启动服务还是该换一本书。
 */

export type ApiErrorKind =
  /** 连不上：后端没起来 / 网络断了 */
  | 'offline'
  /** 404：这本书 / 这一章不存在 */
  | 'notfound'
  /** 5xx：后端起来了但这个请求炸了 */
  | 'server'
  /** 响应不是 JSON、或形状对不上 */
  | 'malformed'
  /** 仅 mock：这一份响应没有在样本里。**不拿另一本的数据顶上。** */
  | 'nosample'

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | null
  readonly path: string
  /** nosample 专用：缺的是整本书还是某一章 */
  readonly scope: 'book' | 'chapter' | null

  constructor(
    kind: ApiErrorKind,
    path: string,
    opts: { status?: number | null; scope?: 'book' | 'chapter' | null; message?: string } = {},
  ) {
    super(opts.message ?? kind + ' ' + path)
    this.name = 'ApiError'
    this.kind = kind
    this.path = path
    this.status = opts.status ?? null
    this.scope = opts.scope ?? null
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError
}

export function isNoSample(e: unknown): e is ApiError {
  return isApiError(e) && e.kind === 'nosample'
}

export function errorTitle(e: unknown): string {
  if (!isApiError(e)) return '取不到'
  switch (e.kind) {
    case 'offline':
      return '后端没起来'
    case 'notfound':
      return '这本书 / 这一章不在后端上'
    case 'server':
      return '后端报错了'
    case 'malformed':
      return '响应读不动'
    case 'nosample':
      return '这一份响应没有在样本里'
  }
}

export function errorBody(e: unknown): string {
  if (!isApiError(e)) {
    return e instanceof Error && e.message
      ? e.message
      : '这一处的数据取不到。界面不拿空白顶上——空白会被当成「查过、没问题」。'
  }
  switch (e.kind) {
    case 'offline':
      return '连不上 127.0.0.1:8765。服务没起来的时候这里是取不到，不是「没有数据」——两者不一样。'
    case 'notfound':
      return e.message || '后端起来了，但它说没有这一份（404）。这不是网络问题，是这本书 / 这一章确实不在盘上。'
    case 'server':
      return e.message || '后端返回 ' + (e.status ?? '5xx') + '。请求发出去了，但没有完成。'
    case 'malformed':
      return '拿到响应了但读不动。字段形状和前端约定的不一样——这是要报给后端的问题。'
    case 'nosample':
      return '样本包只抓了一本书、一章的响应。界面不拿另一本 / 另一章的数据顶上，那会让你以为你在看这一份。'
  }
}
