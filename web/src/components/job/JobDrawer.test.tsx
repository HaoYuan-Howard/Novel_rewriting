import { api } from '@/api/endpoints'
import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { EXECUTOR_BACKEND } from '@/test/agent'
import { JobDrawer } from './JobDrawer'

describe('JobDrawer 作业概览', () => {
  it('逐条展示状态，并能打开任一失败作业的 stdout/stderr', async () => {
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
          executor_backend: EXECUTOR_BACKEND,
          egress_authorized: false,
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
    const stream = vi.spyOn(api, 'streamJobLog').mockImplementation((id, _offset, sink) => {
      sink.onEvent({
        text: id === 'job-1' ? 'stderr: 分章器失败\n恢复命令：python -m novel.cli state\n' : 'done\n',
        next_byte: 64,
        done: true,
        state: id === 'job-1' ? 'failed' : 'ok',
      })
      return vi.fn()
    })

    renderRoute(<JobDrawer />, '/n/book-1/stage1')
    fireEvent.click(await screen.findByRole('button', { name: /作业 2 条记录/ }))

    expect((await screen.findAllByText('Stage1 分章')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Stage0 扫描').length).toBeGreaterThan(0)
    expect(screen.getByText('选择任一作业可查看完整 stdout / stderr')).toBeVisible()
    expect(screen.getByRole('link', { name: '查看运行进度 →' })).toHaveAttribute(
      'href',
      '/n/book-1/run',
    )
    fireEvent.click(screen.getByRole('button', { name: '查看“Stage0 扫描”日志' }))

    expect(screen.getByText(/deepseek-v4-pro/)).toBeVisible()
    expect(screen.getByText('正文外发：未授权')).toBeVisible()
    expect(await screen.findByText('stderr: 分章器失败')).toBeVisible()
    expect(screen.getByText('恢复命令：python -m novel.cli state')).toBeVisible()
    expect(stream).toHaveBeenLastCalledWith('job-1', 0, expect.any(Object))
  })

  it('人往上翻之后不再被新日志拽回底部', async () => {
    // ⭐ 原来每追加一行都无条件 `scrollTop = scrollHeight`。运行期间日志每秒都在长，
    //    往上翻一眼就被拽回底部 —— 而 failed 作业的恢复依据恰恰在**前面**那些行里，
    //    这个抽屉是它唯一的出口。
    vi.spyOn(api, 'getJobs').mockResolvedValue({
      running: null,
      jobs: [{
        id: 'job-1',
        kind: 'stage0.remove',
        label: 'Stage0 扫描',
        argv: 'python -m novel.cli run stage0.remove book-1',
        state: 'running',
        paid: false,
        started: 1,
        finished: null,
        sid: 'book-1',
        ordinal: null,
      }],
    })
    let push: ((text: string) => void) | null = null
    let offset = 0
    vi.spyOn(api, 'streamJobLog').mockImplementation((_id, _from, sink) => {
      push = (text: string) => {
        offset += text.length
        sink.onEvent({ text, next_byte: offset, done: false, state: 'running' })
      }
      push('第 1 行\n')
      return vi.fn()
    })

    renderRoute(<JobDrawer />, '/n/book-1/stage1')
    fireEvent.click(await screen.findByRole('button', { name: /作业 1 条记录/ }))
    fireEvent.click(await screen.findByRole('button', { name: '查看“Stage0 扫描”日志' }))
    const pane = await screen.findByLabelText('Stage0 扫描 作业日志')

    // jsdom 不做布局：显式摆出「内容比视口高、人停在顶部」这一状态。
    Object.defineProperty(pane, 'scrollHeight', { value: 1000, configurable: true })
    Object.defineProperty(pane, 'clientHeight', { value: 100, configurable: true })
    pane.scrollTop = 0
    fireEvent.scroll(pane)

    await act(async () => {
      push?.('第 2 行\n')
    })
    await waitFor(() => expect(screen.getByText('第 2 行')).toBeVisible())
    expect(pane.scrollTop).toBe(0)

    // 人自己滚回底部 ⇒ 恢复跟随。
    pane.scrollTop = 900
    fireEvent.scroll(pane)
    await act(async () => {
      push?.('第 3 行\n')
    })
    await waitFor(() => expect(screen.getByText('第 3 行')).toBeVisible())
    expect(pane.scrollTop).toBe(1000)
  })

  it('收起时移除内部焦点入口，并把已有焦点归还给展开按钮', async () => {
    vi.spyOn(api, 'getJobs').mockResolvedValue({
      running: null,
      jobs: [{
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
      }],
    })
    vi.spyOn(api, 'streamJobLog').mockReturnValue(vi.fn())

    renderRoute(<JobDrawer />, '/n/book-1/stage1')
    const toggle = await screen.findByRole('button', { name: /作业 1 条记录/ })
    expect(screen.queryByRole('button', { name: '查看“Stage0 扫描”日志' })).not.toBeInTheDocument()

    fireEvent.click(toggle)
    const jobButton = screen.getByRole('button', { name: '查看“Stage0 扫描”日志' })
    jobButton.focus()
    expect(jobButton).toHaveFocus()

    fireEvent.click(toggle)
    await waitFor(() => expect(toggle).toHaveFocus())
    expect(screen.queryByRole('button', { name: '查看“Stage0 扫描”日志' })).not.toBeInTheDocument()
  })
})
