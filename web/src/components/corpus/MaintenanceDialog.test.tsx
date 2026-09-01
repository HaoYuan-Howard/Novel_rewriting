import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import type { RetentionPlan, TrashRestorePlan } from '@/types/maintenance'
import { MaintenanceDialog } from './MaintenanceDialog'

function renderDialog() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  })
  const onRestored = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <MaintenanceDialog onClose={vi.fn()} onRestored={onRestored} />
    </QueryClientProvider>,
  )
  return { onRestored }
}

describe('MaintenanceDialog', () => {
  it('retention 表单变化会作废旧 preview，apply 原样传计划并显式确认', async () => {
    const plan: RetentionPlan = {
      wire_version: 1,
      generated_at: 1,
      policy: {
        log_ttl_seconds: null,
        telemetry_ttl_seconds: null,
        trash_ttl_seconds: null,
        checkpoint_ttl_seconds: null,
        max_entries: 512,
      },
      examined: 3,
      candidates: [],
      plan_token: '1'.repeat(64),
    }
    vi.spyOn(api, 'previewRetention').mockResolvedValue(plan)
    const apply = vi.spyOn(api, 'applyRetention').mockResolvedValue({
      plan_token: plan.plan_token,
      removed: [],
      receipts: [],
    })
    renderDialog()

    fireEvent.click(screen.getByRole('button', { name: '预览 retention 计划' }))
    expect(await screen.findByText(/扫描 3 项/)).toBeVisible()
    fireEvent.change(screen.getByLabelText('日志 TTL（秒）'), {
      target: { value: '3600' },
    })
    expect(screen.queryByText(/扫描 3 项/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '预览 retention 计划' }))
    await screen.findByText(/扫描 3 项/)
    const applyButton = screen.getByRole('button', { name: '应用这份 retention 计划' })
    expect(applyButton).toBeDisabled()
    fireEvent.click(screen.getByLabelText('我已核对这份计划，并确认 apply'))
    fireEvent.click(applyButton)
    await waitFor(() => expect(apply).toHaveBeenCalledWith(plan, true))
  })

  it('trash restore 只有同一份 preview 经过确认才可 apply', async () => {
    const plan: TrashRestorePlan = {
      wire_version: 1,
      generated_at: 1,
      trash_id: '1700000000-deadbeef',
      sid: '1001',
      title: '测试书',
      original_dirname: '测试书_1001',
      metadata_sha256: '2'.repeat(64),
      manifest_file_sha256: '3'.repeat(64),
      manifest_sha256: '4'.repeat(64),
      plan_token: '5'.repeat(64),
    }
    vi.spyOn(api, 'previewTrashRestore').mockResolvedValue(plan)
    const apply = vi.spyOn(api, 'applyTrashRestore').mockResolvedValue({
      sid: '1001', title: '测试书', trash_id: plan.trash_id,
      book_rel: '测试书_1001',
    })
    const { onRestored } = renderDialog()
    fireEvent.change(screen.getByLabelText('trash id'), {
      target: { value: plan.trash_id },
    })
    fireEvent.change(screen.getByLabelText('完整书名确认'), {
      target: { value: plan.title },
    })
    fireEvent.click(screen.getByRole('button', { name: '预览恢复' }))
    expect(await screen.findByText(/《测试书》/)).toBeVisible()
    fireEvent.click(screen.getByLabelText('我已核对 trash id、书名与 sid，并确认恢复'))
    fireEvent.click(screen.getByRole('button', { name: '应用这份恢复计划' }))
    await waitFor(() => expect(apply).toHaveBeenCalledWith(plan, true))
    expect(onRestored).toHaveBeenCalled()
  })
})
