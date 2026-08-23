import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
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
})
