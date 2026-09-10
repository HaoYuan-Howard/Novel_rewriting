import { api } from '@/api/endpoints'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import type { BudgetGrant, WorkUnitGrantPreview, WorkUnitGrantRequest } from '@/types/run'
import { WorkUnitBudgetGrant } from './WorkUnitBudgetGrant'

const PREVIEW: WorkUnitGrantPreview = {
  sid: 'book-1', unit_id: 'a'.repeat(64), available: true, reason: '可明确追加',
  additional: 1, allowed_additions: [1, 2, 3, 4], preview_token: 'b'.repeat(64),
  unit: { unit_id: 'a'.repeat(64), node: 'stage5.write', role: 'executor', scope: 'chapter:17',
    content_used: 4, content_limit: 4, content_remaining: 0, content_base_limit: 4, content_granted: 0,
    grant_count: 0, state: 'failed', latest_request_sha256: 'c'.repeat(64), latest_response_sha256: null,
    recovery_action: 'manual_edit' },
  costs: { calls: 4, failed_calls: 2, cached_calls: 0, prompt_tokens: 40, completion_tokens: 12,
    total_tokens: 52, missing_usage_calls: 0 }, history: [],
}

function granted(req: WorkUnitGrantRequest): BudgetGrant {
  return { ...req, actor: 'human:frontend', created_at: 10, before_ledger_sha256: 'd'.repeat(64),
    surface_sha256: 'e'.repeat(64), authorized_after_ordinal: 4, owner_run_ids: ['ended-owner'] }
}

describe('工作单元额度追加', () => {
  it('读取不自动授权，次数仅来自后端，网络重试复用同一意图', async () => {
    vi.spyOn(api, 'previewWorkUnitGrant').mockResolvedValue(PREVIEW)
    const apply = vi.spyOn(api, 'grantWorkUnit').mockRejectedValueOnce(new Error('响应不确定'))
      .mockImplementationOnce(async (_sid, _unit, req) => ({ grant: granted(req), current: PREVIEW }))
    renderRoute(<WorkUnitBudgetGrant sid="book-1" unitId={PREVIEW.unit_id} />)
    expect(apply).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '查看 / 追加额度' }))
    expect(await screen.findByText(/累计 token 成本：52/)).toBeVisible()
    expect(screen.getAllByRole('option')).toHaveLength(4)
    expect(screen.getByRole('combobox')).toHaveValue('1')
    expect(screen.getByRole('button', { name: '明确追加 1 次' })).toBeDisabled()
    fireEvent.change(screen.getByRole('textbox', { name: '追加理由' }), { target: { value: '完成本章修订' } })
    fireEvent.click(screen.getByRole('button', { name: '明确追加 1 次' }))
    const retry = await screen.findByRole('button', { name: '核验并重试同一授权' })
    await waitFor(() => expect(retry).toBeEnabled())
    fireEvent.click(retry)
    await waitFor(() => expect(apply).toHaveBeenCalledTimes(2))
    expect(apply.mock.calls[1]?.[2]).toEqual(apply.mock.calls[0]?.[2])
    expect(await screen.findByText(/已追加 1 次，原轮次与成本账均保留/)).toBeVisible()
  })

  it('响应丢失后从历史确认生效，不再追加第二笔', async () => {
    let current = PREVIEW
    vi.spyOn(api, 'previewWorkUnitGrant').mockImplementation(async () => current)
    const apply = vi.spyOn(api, 'grantWorkUnit').mockImplementation(async (_sid, _unit, req) => {
      current = { ...PREVIEW, available: false, preview_token: null, reason: '仍有额度', history: [granted(req)] }
      throw new Error('已写入但响应丢失')
    })
    renderRoute(<WorkUnitBudgetGrant sid="book-1" unitId={PREVIEW.unit_id} />)
    fireEvent.click(screen.getByRole('button', { name: '查看 / 追加额度' }))
    await screen.findByText('可明确追加')
    fireEvent.change(screen.getByRole('textbox', { name: '追加理由' }), { target: { value: '一次修订' } })
    fireEvent.click(screen.getByRole('button', { name: '明确追加 1 次' }))
    expect(await screen.findByRole('button', { name: '本次授权已生效' })).toBeDisabled()
    expect(apply).toHaveBeenCalledTimes(1)
  })

  it('未知保持未知，后端拒绝时不能以输入理由启用授权', async () => {
    vi.spyOn(api, 'previewWorkUnitGrant').mockResolvedValue({ ...PREVIEW, available: false,
      reason: '来源账未闭合', preview_token: null, costs: null, unit: null, history: null })
    const apply = vi.spyOn(api, 'grantWorkUnit')
    renderRoute(<WorkUnitBudgetGrant sid="book-1" unitId={PREVIEW.unit_id} />)
    fireEvent.click(screen.getByRole('button', { name: '查看 / 追加额度' }))
    expect(await screen.findByText('累计成本未知。')).toBeVisible()
    fireEvent.change(screen.getByRole('textbox', { name: '追加理由' }), { target: { value: '不能覆盖拒因' } })
    expect(screen.getByRole('button', { name: '明确追加 1 次' })).toBeDisabled()
    expect(apply).not.toHaveBeenCalled()
  })
})
