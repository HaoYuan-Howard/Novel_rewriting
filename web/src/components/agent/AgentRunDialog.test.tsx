import { api } from '@/api/endpoints'
import { ApiError } from '@/api/errors'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { AgentRunDialog } from './AgentRunDialog'

describe('AgentRunDialog', () => {
  it('默认收起长命令，并把滚动内容与固定操作栏分开', async () => {
    vi.spyOn(api, 'previewAgentRun').mockResolvedValue({
      argv: ['claude', '-p', '很长的 prompt '.repeat(500)],
      prompt: '很长的 prompt '.repeat(500),
      authorized: false,
      egress_authorized: true,
    })

    renderRoute(
      <AgentRunDialog sid="book-1" onClose={vi.fn()} onStarted={vi.fn()} />,
      '/n/book-1/stage0',
    )

    await waitFor(() => expect(api.previewAgentRun).toHaveBeenCalled())

    expect(screen.getByTestId('agent-run-dialog')).toHaveClass(
      'max-h-[calc(100vh-32px)]',
      'flex-col',
    )
    expect(screen.getByTestId('agent-run-dialog-body')).toHaveClass(
      'min-h-0',
      'overflow-y-auto',
    )
    expect(screen.getByTestId('agent-run-dialog-footer')).toHaveClass('shrink-0')
    expect(screen.getByTestId('agent-command-preview')).toHaveClass('max-h-[92px]')
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '展开完整命令' })).toBeEnabled(),
    )
    expect(screen.getByRole('button', { name: /展开 prompt 全文/ })).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: '展开完整命令' }))

    await waitFor(() =>
      expect(screen.getByTestId('agent-command-preview')).toHaveClass('max-h-[260px]'),
    )
    expect(screen.getByRole('button', { name: '收起完整命令' })).toBeVisible()
    expect(screen.getByRole('button', { name: '生成诊断与恢复方案' })).toBeVisible()
    expect(screen.getByText('仅诊断与恢复方案')).toBeVisible()

    fireEvent.click(screen.getByRole('checkbox', { name: /允许本会话产生外部模型调用费用/ }))

    expect(screen.getByRole('button', { name: '授权并开始跑书' })).toBeVisible()
  })

  it('同书冲突不再误报成全局只能跑一本', async () => {
    vi.spyOn(api, 'previewAgentRun').mockResolvedValue({
      argv: ['claude', '-p', 'prompt'],
      prompt: 'prompt',
      authorized: false,
      egress_authorized: true,
    })
    vi.spyOn(api, 'startAgentRun').mockRejectedValue(new ApiError(
      'server',
      '/agent/runs',
      { status: 409, backendKind: 'JobConflict', message: 'same book is running' },
    ))

    renderRoute(
      <AgentRunDialog sid="book-1" onClose={vi.fn()} onStarted={vi.fn()} />,
      '/n/book-1/stage0',
    )
    const start = await screen.findByRole('button', { name: '生成诊断与恢复方案' })
    await waitFor(() => expect(start).toBeEnabled())
    fireEvent.click(start)

    expect(await screen.findByText(/同一本书互斥；其他书仍可并行/)).toBeVisible()
    expect(screen.queryByText(/一次只跑一个/)).not.toBeInTheDocument()
  })
})
