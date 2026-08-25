import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_SID } from '@/api/mock'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import { ChromeProvider } from '@/hooks/useChrome'
import type { ApprovalDoc } from '@/types/approvals'
import type { JobPreview } from '@/types/jobs'
import { Stage5Screen } from './Stage5Screen'

vi.mock('@/components/app/ScreenFrame', () => ({
  ScreenFrame: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

vi.mock('@/hooks/useSample', () => ({
  useSampleCoverage: () => ({ hasSample: () => true }),
}))

function renderScreen() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return {
    queryClient,
    ...render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={['/n/' + SAMPLE_SID + '/stage5/3']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <ChromeProvider>
          <Routes>
            <Route
              path="/n/:sid/stage5/:o"
              element={
                <>
                  <Stage5Screen />
                  <ConfirmDialog />
                </>
              }
            />
          </Routes>
        </ChromeProvider>
      </MemoryRouter>
    </QueryClientProvider>,
    ),
  }
}

describe('Stage5 部分写作授权链', () => {
  it('已有部分 canonical、writing=false 时，确认付费补写后先授权再启动', async () => {
    const [legacyStage5, review, enums] = await Promise.all([
      mockApi.getStage5(SAMPLE_SID, 3),
      mockApi.getReview(SAMPLE_SID, 3),
      mockApi.getEnums(),
    ])
    const subject = 'a'.repeat(64)
    const structureDecision = {
      subject_sha256: subject,
      actor: 'reviewer',
      reason: '已核对全书结构',
      chapters: [{ output_ordinal: 3, decision: 'accept', reason: '第 3 章可执行' }],
      decided_at: '2026-08-25T00:00:00+00:00',
    }
    const approvals: ApprovalDoc = {
      v: 3,
      sid: SAMPLE_SID,
      gate: { s0: true, s1: true, s2: true, s3: true, s4: true, s5: false, s6: false, s7: false },
      seen: { read: true, global: true },
      writing: false,
      structure: {
        state: 'accepted', current_subject_sha256: subject,
        current_decision: structureDecision, history: [structureDecision],
      },
      spawns: {},
    }
    const order: string[] = []

    vi.spyOn(api, 'getStage5').mockResolvedValue({
      ...legacyStage5,
      written: 1,
      missing: [3],
      legacy_incompatible: false,
      legacy_paths: [],
    })
    vi.spyOn(api, 'getReview').mockResolvedValue(review)
    vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
    vi.spyOn(api, 'getApprovals').mockResolvedValue(approvals)
    vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
    const preview: JobPreview = {
      argv: 'python -m novel.cli stage5.write --chapters 3',
      paid: true,
      label: 'stage5 全书闭卷写作 --chapters',
      kind: 'stage5.write',
      steps: [{
        label: 'stage5 全书闭卷写作 --chapters',
        paid: true,
        writes: 'stage5/ch03.json',
        argv: 'python -m novel.cli stage5.write --chapters 3',
      }],
    }
    vi.spyOn(api, 'previewJob').mockResolvedValue(preview)
    const writingSpy = vi.spyOn(api, 'startWriting').mockImplementation(async () => {
      order.push('writing')
      return { ...approvals, writing: true }
    })
    const startSpy = vi.spyOn(api, 'startJob').mockImplementation(async (request) => {
      order.push('job')
      return {
        id: 'stage5-paid',
        kind: request.kind,
        label: request.label,
        argv: preview.argv,
        state: 'running',
        paid: true,
        started: 1,
        finished: null,
        sid: request.sid,
        ordinal: null,
      }
    })

    renderScreen()

    fireEvent.click(await screen.findByRole('button', { name: '全书闭卷写作 --chapters' }))
    fireEvent.click(await screen.findByRole('button', { name: '确认发起（花钱）' }))

    await waitFor(() => expect(startSpy).toHaveBeenCalledTimes(1))
    expect(writingSpy).toHaveBeenCalledWith(SAMPLE_SID)
    expect(startSpy).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'stage5.write',
      extra: ['--chapters'],
      args: ['3'],
      sid: SAMPLE_SID,
    }))
    expect(order).toEqual(['writing', 'job'])
  })
})
