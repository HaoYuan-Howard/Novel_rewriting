import { afterEach, describe, expect, it, vi } from 'vitest'
import { get, getBlob, getText, post } from './client'

afterEach(() => vi.unstubAllGlobals())

describe('HTTP 错误适配', () => {
  it.each([['JSON', get], ['正文', getText], ['下载', getBlob]] as const)(
    '%s 消费保留后端拒绝类别、消息与免费恢复提示', async (_label, read) => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
        kind: 'UpstreamStale', message: '当前成书与来源不一致',
        missing: 'stage7/book.txt', hint: '重新执行免费 assemble 后再下载',
      }), { status: 409 })))
      await expect(read('/book')).rejects.toMatchObject({
        kind: 'server', status: 409, backendKind: 'UpstreamStale',
        message: '当前成书与来源不一致', missing: 'stage7/book.txt',
        hint: '重新执行免费 assemble 后再下载',
      })
    },
  )

  it('非 JSON 错误不掩盖 HTTP 状态，断线与解析失败分别呈现', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response('missing', { status: 404 }))
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce(new Response('invalid json', { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    await expect(getBlob('/book')).rejects.toMatchObject({ kind: 'notfound', status: 404 })
    await expect(getText('/book')).rejects.toMatchObject({ kind: 'offline', status: null })
    await expect(get('/book')).rejects.toMatchObject({ kind: 'malformed', status: 200 })
  })

  it('成功下载保留原始字节，写请求仍带 CSRF 头', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response('第一章\n合成小说正文'))
      .mockResolvedValueOnce(new Response('{"ok":true}'))
    vi.stubGlobal('fetch', fetch)
    expect(await (await getBlob('/book')).text()).toBe('第一章\n合成小说正文')
    expect(await post('/approval', { accept: true })).toEqual({ ok: true })
    expect(fetch).toHaveBeenLastCalledWith('/api/approval', expect.objectContaining({
      method: 'POST', headers: { 'X-Novel-UI': '1', 'Content-Type': 'application/json' },
    }))
  })
})
