import { useChrome } from '@/hooks/useChrome'
import { api } from '@/api/endpoints'
import { ApiError } from '@/api/errors'
import { qk } from '@/api/queryKeys'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ConfirmDialog } from './ConfirmDialog'
import { renderRoute } from '@/test/render'
import { EXECUTOR_PROFILE } from '@/test/agent'
import type { JobPreview, JobRequest } from '@/types/jobs'

const request: JobRequest = {
  kind: 'stage5.write',
  label: '动态修复第 11 章',
  extra: ['--chapters'],
  args: ['11'],
  sid: 'book-1',
  ordinal: 11,
}

const preview: JobPreview = {
  argv: 'python -m novel.cli stage5.write --chapters 11',
  paid: true,
  label: request.label,
  kind: request.kind,
  executor_profile: EXECUTOR_PROFILE,
  egress_authorized: false,
  steps: [
    {
      label: '第 11 章闭卷写作',
      paid: true,
      writes: 'stage5/ch11.json',
      argv: 'python -m novel.cli stage5.write --chapters 11',
      executor_profile: EXECUTOR_PROFILE,
      egress_authorized: false,
    },
  ],
}

const revalidateRequest: JobRequest = {
  kind: 'stage5.write',
  label: 'stage5 全书闭卷写作 --revalidate',
  extra: ['--revalidate'],
  args: ['--chapters', '11'],
  required_inputs: [
    { flag: '--actor', description: '人工决定的自报身份（不是认证身份）' },
    { flag: '--reason', description: '人工决定的非空理由' },
  ],
  sid: 'book-1',
  ordinal: null,
}

const revalidatePreview: JobPreview = {
  argv:
    'python -m novel.cli stage5.write --revalidate --chapters 11 '
    + '--actor 待填·actor --reason 待填·reason',
  paid: false,
  label: revalidateRequest.label,
  kind: revalidateRequest.kind,
  steps: [
    {
      label: revalidateRequest.label,
      paid: false,
      writes: 'stage5/ch11.json',
      argv:
        'python -m novel.cli stage5.write --revalidate --chapters 11 '
        + '--actor 待填·actor --reason 待填·reason',
    },
  ],
}

function Harness() {
  const { askConfirm } = useChrome()
  return (
    <>
      <button type="button" onClick={() => askConfirm({ req: request })}>
        修第 11 章
      </button>
      <ConfirmDialog />
    </>
  )
}

function RevalidateHarness() {
  const { askConfirm } = useChrome()
  return (
    <>
      <button type="button" onClick={() => askConfirm({ req: revalidateRequest })}>
        同轮重判
      </button>
      <ConfirmDialog />
    </>
  )
}

describe('ConfirmDialog', () => {
  it('previews and starts the exact dynamic request including --chapters 11', async () => {
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    const previewSpy = vi.spyOn(api, 'previewJob').mockResolvedValue(preview)
    const startSpy = vi.spyOn(api, 'startJob').mockResolvedValue({
      id: 'j1',
      kind: request.kind,
      label: request.label,
      argv: preview.argv,
      state: 'running',
      paid: true,
      started: 1,
      finished: null,
      sid: request.sid,
      ordinal: 11,
    })
    renderRoute(<Harness />)

    fireEvent.click(screen.getByRole('button', { name: '修第 11 章' }))
    const command = await screen.findByText(/--chapters 11/)
    expect(command).toBeVisible()
    expect(command.textContent?.match(/--chapters/g)).toHaveLength(1)
    expect(screen.getByText(/executor-model-v7/)).toBeVisible()
    expect(screen.getByText('正文外发：未授权')).toBeVisible()
    expect(previewSpy).toHaveBeenCalledWith(request)
    fireEvent.click(screen.getByRole('button', { name: '确认发起（花钱）' }))
    await waitFor(() => expect(startSpy).toHaveBeenCalledWith(request))
  })

  it('hides stale paid state and command while a cached plan refetches', async () => {
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    let finishRefetch: ((value: JobPreview) => void) | undefined
    vi.spyOn(api, 'previewJob')
      .mockResolvedValueOnce(preview)
      .mockImplementationOnce(
        () => new Promise<JobPreview>((resolve) => {
          finishRefetch = resolve
        }),
      )
    const { queryClient } = renderRoute(<Harness />)

    fireEvent.click(screen.getByRole('button', { name: '修第 11 章' }))
    expect(await screen.findByText(/--chapters 11/)).toBeVisible()
    void queryClient.invalidateQueries({ queryKey: qk.jobPreview(request) })
    expect(await screen.findByText('贵贱待服务端确认')).toBeVisible()
    expect(screen.queryByText(/--chapters 11/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '等待执行计划' })).toBeDisabled()
    finishRefetch?.(preview)
  })

  it('作业没起来时把原因摆在框里，并且不关框', async () => {
    // ⭐ 真实场景：`POST /api/jobs` 409（后端拒绝了这次启动）。
    // 修复前这里什么都不画、框照旧开着、按钮照旧能点 ——
    // 用户只会反复点一个花钱的按钮，而钱一分没花、活一件没干。
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    vi.spyOn(api, 'previewJob').mockResolvedValue(preview)
    vi.spyOn(api, 'startJob').mockRejectedValue(
      new ApiError('server', '/jobs', {
        status: 409,
        message: 'save a current depth selection before writing',
      }),
    )
    renderRoute(<Harness />)

    fireEvent.click(screen.getByRole('button', { name: '修第 11 章' }))
    fireEvent.click(await screen.findByRole('button', { name: '确认发起（花钱）' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('没有发起')
    expect(alert).toHaveTextContent('save a current depth selection before writing')
    // 框还在 —— 关掉等于把「没发起」演成「发起了」
    expect(screen.getByRole('button', { name: '确认发起（花钱）' })).toBeVisible()
  })

  it('previews placeholders but starts revalidation only with freshly entered audit values', async () => {
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    const previewSpy = vi.spyOn(api, 'previewJob').mockResolvedValue(revalidatePreview)
    const startSpy = vi.spyOn(api, 'startJob').mockResolvedValue({
      id: 'j-revalidate',
      kind: revalidateRequest.kind,
      label: revalidateRequest.label,
      argv: revalidatePreview.argv,
      state: 'running',
      paid: false,
      started: 1,
      finished: null,
      sid: revalidateRequest.sid,
      ordinal: null,
    })
    renderRoute(<RevalidateHarness />)

    fireEvent.click(screen.getByRole('button', { name: '同轮重判' }))
    expect(await screen.findByText(/--actor 待填·actor --reason 待填·reason/)).toBeVisible()
    expect(previewSpy).toHaveBeenCalledWith(revalidateRequest)
    expect(screen.getByRole('button', { name: '请先填写必填项' })).toBeDisabled()

    fireEvent.change(screen.getByRole('textbox', { name: '--actor' }), {
      target: { value: ' reviewer-a ' },
    })
    fireEvent.change(screen.getByRole('textbox', { name: '--reason' }), {
      target: { value: '逐项核对当前闸门后同轮重判' },
    })
    fireEvent.click(screen.getByRole('button', { name: '发起' }))

    await waitFor(() => expect(startSpy).toHaveBeenCalledWith({
      ...revalidateRequest,
      args: [
        '--chapters', '11',
        '--actor', 'reviewer-a',
        '--reason', '逐项核对当前闸门后同轮重判',
      ],
    }))
    const submitted = startSpy.mock.calls[0]?.[0]
    expect(JSON.stringify(submitted)).not.toContain('待填·')
  })

  it('Esc 关闭确认框', async () => {
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    vi.spyOn(api, 'previewJob').mockResolvedValue(preview)
    renderRoute(<Harness />)

    fireEvent.click(screen.getByRole('button', { name: '修第 11 章' }))
    expect(await screen.findByRole('dialog')).toBeVisible()

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('已填必填项时 Esc 先挽留：取消不走，确定才关', async () => {
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    vi.spyOn(api, 'previewJob').mockResolvedValue(revalidatePreview)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderRoute(<RevalidateHarness />)

    fireEvent.click(screen.getByRole('button', { name: '同轮重判' }))
    expect(await screen.findByRole('dialog')).toBeVisible()
    fireEvent.change(screen.getByRole('textbox', { name: '--actor' }), {
      target: { value: 'reviewer-a' },
    })

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('dialog')).toBeVisible()

    confirmSpy.mockReturnValue(true)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('没填内容时 Esc 直接关，不挽留', async () => {
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    vi.spyOn(api, 'previewJob').mockResolvedValue(revalidatePreview)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderRoute(<RevalidateHarness />)

    fireEvent.click(screen.getByRole('button', { name: '同轮重判' }))
    expect(await screen.findByRole('dialog')).toBeVisible()

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
