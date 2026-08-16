import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { Stage7Screen } from './Stage7Screen'

describe('Stage7Screen download', () => {
  it('uses the API adapter so mock and real modes share one path', async () => {
    vi.spyOn(api, 'getStage7').mockResolvedValue(await mockApi.getStage7(SAMPLE_SID))
    const download = vi
      .spyOn(api, 'downloadStage7Book')
      .mockResolvedValue(new Blob(['book'], { type: 'text/plain' }))
    const createUrl = vi.fn(() => 'blob:test')
    const revokeUrl = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createUrl })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeUrl })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)

    renderRoute(<Stage7Screen />, `/n/${SAMPLE_SID}/stage7`)
    fireEvent.click(await screen.findByRole('button', { name: '下载 book.txt' }))

    await waitFor(() => expect(download).toHaveBeenCalledWith(SAMPLE_SID))
    expect(createUrl).toHaveBeenCalled()
    expect(revokeUrl).toHaveBeenCalledWith('blob:test')
  })
})
