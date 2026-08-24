import { api } from '@/api/endpoints'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { AgentBatchDialog } from './AgentBatchDialog'

describe('AgentBatchDialog', () => {
  it('按付费授权准确区分恢复方案与真正跑书', async () => {
    vi.spyOn(api, 'getAgentQueue').mockResolvedValue({
      running: [],
      pending: [],
      rejected: [],
      max_concurrent: 3,
    })

    renderRoute(
      <AgentBatchDialog
        books={[
          { sid: 'book-1', title: '第一本' },
          { sid: 'book-2', title: '第二本' },
        ]}
        onClose={vi.fn()}
      />,
      '/n/book-1/stage0',
    )

    await waitFor(() => expect(api.getAgentQueue).toHaveBeenCalled())
    expect(screen.getByText('未授权流水线付费节点')).toBeVisible()
    expect(
      screen.getByRole('button', { name: '批量启动诊断（仍产生 Claude 费用）' }),
    ).toBeVisible()

    fireEvent.click(screen.getByRole('checkbox', { name: /允许这一批产生外部模型调用费用/ }))

    expect(screen.getByText('已授权流水线付费节点')).toBeVisible()
    expect(screen.getByRole('button', { name: '授权并开始跑这 2 本' })).toBeVisible()
  })
})
