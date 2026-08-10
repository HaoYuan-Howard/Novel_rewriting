/**
 * 真实 HTTP 传输层。只做三件事：拼 base URL、带 CSRF 头、把错误归一成 ApiError。
 * 不知道任何业务，也不认识任何 endpoint。
 */
import { ApiError } from './errors'

const BASE = '/api'

/** 后端的 CSRF 闸门：写请求没有这个头一律 403 */
const UI_HEADER = { 'X-Novel-UI': '1' }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(BASE + path, init)
  } catch {
    // fetch 只有在连不上时才 reject —— 4xx/5xx 是 resolve
    throw new ApiError('offline', path)
  }

  if (res.status === 404) throw new ApiError('notfound', path, { status: 404 })
  if (!res.ok) throw new ApiError('server', path, { status: res.status })

  try {
    return (await res.json()) as T
  } catch {
    throw new ApiError('malformed', path, { status: res.status })
  }
}

export function get<T>(path: string): Promise<T> {
  return request<T>(path)
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { ...UI_HEADER, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}
