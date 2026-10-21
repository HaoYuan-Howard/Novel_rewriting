import { afterEach, describe, expect, it, vi } from 'vitest'
import { get, getBlob, getText, getTextFile, post } from './client'

afterEach(() => vi.unstubAllGlobals())

describe('HTTP 错误适配', () => {
  it.each([['JSON', get], ['正文', getText], ['下载', getBlob], ['具名下载', getTextFile]] as const)(
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
    expect(await post('/echo', { accept: true })).toEqual({ ok: true })
    expect(fetch).toHaveBeenLastCalledWith('/api/echo', expect.objectContaining({
      method: 'POST', headers: { 'X-Novel-UI': '1', 'Content-Type': 'application/json' },
    }))
  })

  it('下载使用后端UTF8文件名与原始换行，文件名缺失或编码损坏不能静默重拼', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response('正文\r\n\r\n尾段', { headers: {
        'Content-Disposition': "attachment; filename=\"book.txt\"; filename*=UTF-8''%E9%9B%A8%E4%B8%AD%E9%80%81%E4%BF%A1_rewriting.txt",
      } }))
      .mockResolvedValueOnce(new Response('正文'))
      .mockResolvedValueOnce(new Response('正文', { headers: {
        'Content-Disposition': "attachment; filename*=UTF-8''%ZZ",
      } })))
    expect(await getTextFile('/book?download=true')).toEqual({ text: '正文\r\n\r\n尾段', filename: '雨中送信_rewriting.txt' })
    await expect(getTextFile('/book')).rejects.toMatchObject({ kind: 'malformed' })
    await expect(getTextFile('/book')).rejects.toMatchObject({ kind: 'malformed' })
  })
})
