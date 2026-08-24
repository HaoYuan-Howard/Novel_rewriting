import { api } from '@/api/endpoints'
import { mockApi, SAMPLE_ORDINAL, SAMPLE_SID } from '@/api/mock'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'
import type { ApprovalDoc } from '@/types/approvals'
import type { EnumsResponse } from '@/types/enums'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { DepthScreen } from './DepthScreen'

/**
 * ⭐ 最顺的那条路：用户认可默认的前 3 章、**一章都没点**，直接开写。
 *
 * `deep` 的初值来自服务端，而 `deep_proof` 只在显式保存时才落盘 ⇒ 不先保存一次，
 * 后端 `begin_writing` 会以「save a current depth selection before writing」拒发（409）。
 * 这条钉的就是「没点过任何一章也必须能开写」。
 */

const doc: ApprovalDoc = {
  v: 1,
  sid: SAMPLE_SID,
  gate: { s0: true, s1: true, s2: true, s3: true, s4: true, s5: false, s6: false, s7: false },
  seen: { read: true, global: true },
  deep: [1, 2, 3],
  writing: false,
  spawns: {},
}

const enums = {
  hinge_kinds: [],
  nonbody_kinds: [],
  time_layers: [],
  hinge_groups: {},
  style_words: [],
  invent: { allow: [], forbid: [], principle: '' },
  job_kinds: {},
  actions: [{
    kind: 'stage5.write',
    label: 'stage5 逐章写作',
    extra: ['--deep', '--shallow-rest'],
    mode: null,
    paid: true,
    free_flag: null,
    writes_disk: true,
  }],
  uncovered_min_chars: 0,
  spoiler_disclosure: {
    mode: 'display_only',
    security_boundary: false,
    server_sends_full_data: true,
    hidden_paths: [],
    known_leakage_surfaces: [],
    consumers: [],
    notice: 'test',
  },
} as unknown as EnumsResponse

async function harness() {
  const stage4 = await mockApi.getStage4(SAMPLE_SID, SAMPLE_ORDINAL)
  vi.spyOn(api, 'getSampleCoverage').mockResolvedValue(null)
  vi.spyOn(api, 'getStage4').mockResolvedValue(stage4)
  vi.spyOn(api, 'getApprovals').mockResolvedValue(doc)
  vi.spyOn(api, 'getEnums').mockResolvedValue(enums)
  vi.spyOn(api, 'getJobs').mockResolvedValue({ jobs: [], running: null })
  vi.spyOn(api, 'previewJob').mockResolvedValue({
    argv: 'python -m novel.cli run stage5.write --deep 1,2,3 --shallow-rest',
    paid: true,
    label: 'stage5 写作',
    kind: 'stage5.write',
    steps: [{
      label: 'stage5 逐章写作',
      paid: true,
      writes: 'stage5/deep 与 stage5/shallow',
      argv: 'python -m novel.cli run stage5.write --deep 1,2,3 --shallow-rest --yes',
    }],
  })
  vi.spyOn(api, 'startWriting').mockResolvedValue({ ...doc, writing: true })
  renderRoute(
    <>
      <DepthScreen />
      <ConfirmDialog />
    </>,
    `/n/${SAMPLE_SID}/depth`,
  )
}

describe('DepthScreen 开写前置', () => {
  it('一章都没点也先落一次深度 proof，再启动作业', async () => {
    const order: string[] = []
    const setDeep = vi.spyOn(api, 'setDeep').mockImplementation(async () => {
      order.push('setDeep')
      return doc
    })
    const startJob = vi.spyOn(api, 'startJob').mockImplementation(async () => {
      order.push('startJob')
      return {
        id: 'job-1', kind: 'stage5.write', label: 'stage5 写作',
        argv: 'python -m novel.cli run stage5.write', state: 'running' as const,
        paid: true, started: 1, finished: null, sid: SAMPLE_SID, ordinal: null,
      }
    })
    await harness()

    fireEvent.click(await screen.findByRole('button', { name: /按这个分配开写/ }))
    fireEvent.click(await screen.findByRole('button', { name: /确认发起（花钱）/ }))

    await waitFor(() => expect(startJob).toHaveBeenCalled())
    // 保存的正是屏上显示的那份分配，且必须发生在启动之前
    expect(setDeep).toHaveBeenCalledWith(SAMPLE_SID, [1, 2, 3])
    expect(order).toEqual(['setDeep', 'startJob'])
  })

  it('前置被服务端拒绝时不掉进 POST /jobs，并把原因摆在框里', async () => {
    vi.spyOn(api, 'setDeep').mockRejectedValue(
      new Error('save a current depth selection before writing'),
    )
    const startJob = vi.spyOn(api, 'startJob')
    await harness()

    fireEvent.click(await screen.findByRole('button', { name: /按这个分配开写/ }))
    fireEvent.click(await screen.findByRole('button', { name: /确认发起（花钱）/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('没有发起')
    expect(startJob).not.toHaveBeenCalled()
    // 框不许关——关掉等于把「没发起」演成「发起了」
    expect(screen.getByRole('button', { name: /确认发起（花钱）/ })).toBeVisible()
  })
})
