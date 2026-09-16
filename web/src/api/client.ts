/**
 * 真实 HTTP 传输层。只做三件事：拼 base URL、带 CSRF 头、把错误归一成 ApiError。
 * 不知道任何业务，也不认识任何 endpoint。
 */
import { ApiError } from './errors'

const BASE = '/api'

/** 后端的 CSRF 闸门：写请求没有这个头一律 403 */
const UI_HEADER = { 'X-Novel-UI': '1' }

async function response(path: string, init?: RequestInit): Promise<Response> {
  let res: Response
  try {
    res = await fetch(BASE + path, init)
  } catch {
    // fetch 只有在连不上时才 reject —— 4xx/5xx 是 resolve
    throw new ApiError('offline', path)
  }

  if (!res.ok) {
    // 后端错误体是 ErrorBody{kind,message,missing,hint}：kind 是异常类名（id 不是措辞），
    // 前端按它分「层没跑 / 产物坏了 / 书不存在」，不靠句子前缀去认。
    let message: string | undefined
    let backendKind: string | null = null
    let missing: string | null = null
    let hint: string | null = null
    try {
      const body = (await res.json()) as {
        message?: unknown
        kind?: unknown
        missing?: unknown
        hint?: unknown
      }
      if (typeof body.message === 'string' && body.message) message = body.message
      if (typeof body.kind === 'string' && body.kind) backendKind = body.kind
      if (typeof body.missing === 'string') missing = body.missing
      if (typeof body.hint === 'string') hint = body.hint
    } catch {
      // 非 JSON 错误仍按状态码归一，不能让解析错误盖掉真正的 HTTP 状态。
    }
    throw new ApiError(res.status === 404 ? 'notfound' : 'server', path, {
      status: res.status,
      message,
      backendKind,
      missing,
      hint,
    })
  }
  return res
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await response(path, init)
  try {
    return (await res.json()) as T
  } catch {
    throw new ApiError('malformed', path, { status: res.status })
  }
}

async function requestText(path: string): Promise<string> {
  const res = await response(path)
  return res.text()
}

async function requestBlob(path: string): Promise<Blob> {
  const res = await response(path)
  return res.blob()
}

export function get<T>(path: string): Promise<T> {
  return request<T>(path)
}

export function getText(path: string): Promise<string> {
  return requestText(path)
}

export function getBlob(path: string): Promise<Blob> {
  return requestBlob(path)
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { ...UI_HEADER, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

/** 上传正文时保留原始字节；JSON 化 File 只会得到一个空对象。 */
export function postRaw<T>(
  path: string,
  body: BodyInit,
  extraHeaders?: Record<string, string>,
): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: {
      ...UI_HEADER,
      'Content-Type': 'application/octet-stream',
      ...extraHeaders,
    },
    body,
  })
}
