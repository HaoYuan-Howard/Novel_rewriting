import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import type { TrashNovelResult } from '@/types/novels'
import { DeleteBookDialog } from './DeleteBookDialog'

describe('DeleteBookDialog', () => {
  it('必须输入完整 sid，成功后展示可恢复位置', async () => {
    const remove = vi.spyOn(api, 'trashNovel').mockResolvedValue({
      sid: 'book-1001',
      title: '测试书',
      trash_id: '1700000000-deadbeef',
      trash_rel: '_service/trash/1700000000-deadbeef/book',
    })
    const onDeleted = vi.fn()
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <DeleteBookDialog
          book={{ sid: 'book-1001', title: '测试书' }}
          onClose={vi.fn()}
          onDeleted={onDeleted}
        />
      </QueryClientProvider>,
    )

    const submit = screen.getByRole('button', { name: '移入回收站' })
    expect(submit).toBeDisabled()
    fireEvent.change(screen.getByRole('textbox', { name: '输入 sid 确认删除' }), {
      target: { value: 'book-1001' },
    })
    expect(submit).toBeEnabled()
    fireEvent.click(submit)

    await waitFor(() =>
      expect(remove).toHaveBeenCalledWith('book-1001', '测试书'),
    )
    expect(await screen.findByText('项目已移入回收站')).toBeVisible()
    expect(screen.getByText('_service/trash/1700000000-deadbeef/book')).toBeVisible()
    expect(onDeleted).toHaveBeenCalledWith(expect.objectContaining({ sid: 'book-1001' }))
  })

  it('请求在途时关不掉：取消不了的东西不能装成取消了', async () => {
    // ⭐ `api.trashNovel` 发出去就取消不掉。此时点「取消」或点遮罩，界面上像是
    //    撤销了，盘上那本书仍会被移入回收站 —— 而恢复位置（`trash_rel`，
    //    唯一一处告诉人「去哪儿找回来」的地方）再也不会显示。
    let settle: (value: TrashNovelResult) => void = () => {}
    vi.spyOn(api, 'trashNovel').mockImplementation(
      () => new Promise<TrashNovelResult>((resolve) => {
        settle = resolve
      }),
    )
    const onClose = vi.fn()
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <DeleteBookDialog
          book={{ sid: 'book-1001', title: '测试书' }}
          onClose={onClose}
          onDeleted={vi.fn()}
        />
      </QueryClientProvider>,
    )

    fireEvent.change(screen.getByRole('textbox', { name: '输入 sid 确认删除' }), {
      target: { value: 'book-1001' },
    })
    fireEvent.click(screen.getByRole('button', { name: '移入回收站' }))

    const cancel = await screen.findByRole('button', { name: '取消' })
    await waitFor(() => expect(cancel).toBeDisabled())
    fireEvent.click(cancel)
    fireEvent.click(screen.getByRole('dialog').parentElement as HTMLElement)
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByText('请求已经发出，取消不掉了——等它回来再关。')).toBeVisible()

    settle({
      sid: 'book-1001',
      title: '测试书',
      trash_id: '1700000000-deadbeef',
      trash_rel: '_service/trash/1700000000-deadbeef/book',
    })
    expect(await screen.findByText('项目已移入回收站')).toBeVisible()
    const close = screen.getByRole('button', { name: '关闭' })
    await waitFor(() => expect(close).toBeEnabled())
    fireEvent.click(close)
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })
})
