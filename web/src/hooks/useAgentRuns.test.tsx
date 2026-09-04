import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { useAgentQueue, useStartAgentRun } from '@/hooks/useAgentRuns'
import { READY_PROFILES } from '@/test/agent'

function QueueProbe() {
  const queue = useAgentQueue()
  const start = useStartAgentRun('next-book')
  if (!queue.data) return <span>加载中</span>
  return (
    <>
      <span>{`${queue.data.running.length}/${queue.data.pending.length}`}</span>
      <button
        type="button"
        onClick={() => start.mutate({
          sid: 'next-book',
          authorized: true,
          egress_authorized: true,
          profiles: READY_PROFILES,
        })}
      >
        启动
      </button>
    </>
  )
}

describe('useAgentQueue', () => {
  it('单本启动响应失败也重新读取原子队列快照', async () => {
    const getQueue = vi
      .spyOn(api, 'getAgentQueue')
      .mockResolvedValueOnce({
        running: [],
        pending: [],
        rejected: [],
        max_concurrent: 3,
      })
      .mockResolvedValueOnce({
        running: [],
        pending: [{ sid: 'next-book', position: 1, profiles: READY_PROFILES }],
        rejected: [],
        max_concurrent: 3,
      })
    const start = vi.spyOn(api, 'startAgentRun').mockRejectedValue(
      new Error('202 响应丢失'),
    )

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <QueueProbe />
      </QueryClientProvider>,
    )

    expect(await screen.findByText('0/0')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '启动' }))
    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(getQueue).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('0/1')).toBeInTheDocument()
  })
})
