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
  /**
   * 后端 `ErrorBody.kind`：**异常类名，不是措辞**（Q-6）。
   * 层级路由的 404 + `UpstreamMissing` = 「这一步还没跑，下一步点这里」（U-17）——
   * 书级 404 已由总览查询拦在上层，所以到了层级屏还 404 的，几乎总是「层没跑」。
   */
  readonly backendKind: string | null
  /** 缺的是哪一个（`'novel'` / `'stage7/name_map.json'`）；null = 与某一份具体的东西无关 */
  readonly missing: string | null
  /** 下一步该做什么；null = 后端说不出来（说不出来就不编一句） */
  readonly hint: string | null

  constructor(
    kind: ApiErrorKind,
    path: string,
    opts: {
      status?: number | null
      scope?: 'book' | 'chapter' | null
      message?: string
      backendKind?: string | null
      missing?: string | null
      hint?: string | null
    } = {},
  ) {
    super(opts.message ?? kind + ' ' + path)
    this.name = 'ApiError'
    this.kind = kind
    this.path = path
    this.status = opts.status ?? null
    this.scope = opts.scope ?? null
    this.backendKind = opts.backendKind ?? null
    this.missing = opts.missing ?? null
    this.hint = opts.hint ?? null
  }
}

/** 「这一步还没跑」——与「书不存在 / 打错 id」分开渲染的依据。 */
export function isNotRunYet(e: unknown): boolean {
  return isApiError(e) && e.kind === 'notfound' && e.backendKind === 'UpstreamMissing'
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

/**
 * 后端给的「下一条命令」。`null` = 后端说不出来 —— **说不出来就不编一句**。
 *
 * 它与 `errorBody()` 是**两件事**：那条说发生了什么，这条说照着敲什么。
 * 后端把两者分成 `ErrorBody.message` / `ErrorBody.hint` 两格正是为了这个区分
 * ——拼在一起，「这条失败有没有出路」在界面上就不可判定，用户只能从一段中文里
 * 自己去找那条命令。⇒ 渲染也要分开，别把它塞回正文。
 */
export function errorHint(e: unknown): string | null {
  return isApiError(e) ? e.hint : null
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
