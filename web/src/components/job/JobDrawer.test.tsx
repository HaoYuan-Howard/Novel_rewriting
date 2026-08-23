import { api } from '@/api/endpoints'
import { fireEvent, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { JobDrawer } from './JobDrawer'

describe('JobDrawer 作业概览', () => {
  it('逐条展示最新状态，不连接或展示详细日志', async () => {
    vi.spyOn(api, 'getJobs').mockResolvedValue({
      running: null,
      jobs: [
        {
          id: 'job-1',
          kind: 'stage0.remove',
          label: 'Stage0 扫描',
          argv: 'python -m novel.cli run stage0.remove book-1',
          state: 'failed',
          paid: false,
          started: 1,
          finished: 2,
          sid: 'book-1',
          ordinal: null,
        },
        {
          id: 'job-2',
          kind: 'stage1.partition',
          label: 'Stage1 分章',
          argv: 'python -m novel.cli run stage1.partition book-1',
          state: 'ok',
          paid: false,
          started: 3,
          finished: 4,
          sid: 'book-1',
          ordinal: null,
        },
      ],
    })
    const stream = vi.spyOn(api, 'streamJobLog')

    renderRoute(<JobDrawer />, '/n/book-1/stage1')
    fireEvent.click(await screen.findByRole('button', { name: /作业 2 条记录/ }))

    expect(await screen.findByText('Stage1 分章')).toBeVisible()
    expect(screen.getByText('Stage0 扫描')).toBeVisible()
    expect(screen.getByText('这里只显示全部作业的最新状态')).toBeVisible()
    expect(screen.getByRole('link', { name: '查看运行进度 →' })).toHaveAttribute(
      'href',
      '/n/book-1/run',
    )
    expect(screen.queryByText(/python -m novel\.cli/)).not.toBeInTheDocument()
    expect(stream).not.toHaveBeenCalled()
  })
})
