import { api } from '@/api/endpoints'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useLocation } from 'react-router-dom'
import { renderRoute } from '@/test/render'
import { agentCapabilities, previewOf, READY_PROFILES } from '@/test/agent'
import { Stage0Screen } from './Stage0Screen'
import type { Stage0Segments } from '@/types/stages'

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>
}

describe('Stage0Screen 新书起跑入口', () => {
  it('只读展示 Stage0 并可直接交给 Agent；不再有任何人工编辑/批准表单', async () => {
    vi.spyOn(api, 'getStage0Report').mockResolvedValue({
      original_filename: 'book.txt',
      total_code_points: 300,
      warnings: [],
      executable: true,
      revision_id: 'a'.repeat(64),
      revision_origin: 'machine' as const,
    })
    vi.spyOn(api, 'getStage0Segments').mockResolvedValue({
      available: true,
      reason: null,
      segments: [],
    })
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities())
    vi.spyOn(api, 'getAgentProfileSettings').mockResolvedValue({
      state: 'unset',
      profiles: null,
      revision: '0'.repeat(64),
      updated_at: null,
      issue: null,
    })
    const preview = vi.spyOn(api, 'previewAgentRun').mockImplementation(async (req) => ({
      ...previewOf(req.profiles, req.authorized, req.egress_authorized),
      prompt: '从当前盘面续跑',
    }))

    renderRoute(<Stage0Screen />, '/n/book-1/stage0')

    expect(await screen.findByRole('button', { name: 'Agent 代跑' })).toBeVisible()
    expect(screen.getByText(/机器剔除结果只读展示/)).toBeVisible()
    expect(screen.getByText('机械校验通过 · 已自动接受')).toBeVisible()
    // 人工闸退役：没有 actor/reason 表单，也没有人工编辑入口。
    expect(screen.queryByLabelText('自报身份 actor')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('本次批准 / 编辑理由 reason')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '采纳机器新版' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '保留当前版' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Agent 代跑' }))

    expect(await screen.findByText('让 agent 跑这本书')).toBeVisible()
    await waitFor(() =>
      expect(preview).toHaveBeenCalledWith({
        sid: 'book-1',
        authorized: false,
        egress_authorized: true,
        profiles: READY_PROFILES,
      }),
    )
  })

  it('stage0 只读：无切分按钮，partitionStage1 已从 Api 退役', async () => {
    vi.spyOn(api, 'getStage0Report').mockResolvedValue({
      original_filename: 'book.txt',
      total_code_points: 300,
      warnings: [],
      executable: true,
      revision_id: 'a'.repeat(64),
      revision_origin: 'machine' as const,
    })
    vi.spyOn(api, 'getStage0Segments').mockResolvedValue({
      available: true,
      reason: null,
      segments: [],
    })
    expect('partitionStage1' in api).toBe(false)

    renderRoute(
      <>
        <Stage0Screen />
        <LocationProbe />
      </>,
      '/n/book-1/stage0',
    )

    expect(screen.queryByLabelText('自报身份 actor')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /切分并进入 stage1/ })).not.toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/n/book-1/stage0')
  })
})

describe('Stage0Screen 原文只读审阅', () => {
  it('原文分区只读展示（长书 DOM 有界），机器剔除账与告警照常可见', async () => {
    const rows: Stage0Segments['segments'] = Array.from({ length: 622 }, (_, index) => ({
      id: 'seg-' + index,
      kind: index === 621 ? ('end_marker' as const) : ('keep' as const),
      start: index * 10,
      end: index * 10 + 10,
      chars: 10,
      flagged: false,
      text: '正文分区 ' + index,
      origin: null,
      removed_kind: null,
      marker_line: null,
    }))
    vi.spyOn(api, 'getStage0Report').mockResolvedValue({
      original_filename: 'book.txt',
      total_code_points: 6220,
      warnings: [],
      executable: true,
      revision_id: 'a'.repeat(64),
      revision_origin: 'machine' as const,
    })
    vi.spyOn(api, 'getStage0Segments').mockResolvedValue({
      available: true,
      reason: null,
      segments: rows,
    })
    renderRoute(<Stage0Screen />)

    const viewport = await screen.findByTestId('stage0-segment-viewport')
    await waitFor(() => expect(within(viewport).getAllByText(/正文分区 /).length).toBeGreaterThan(0))
    expect(within(viewport).getAllByText(/正文分区 /).length).toBeLessThan(80)

    // 只读：分区不再是可点击改选的按钮。
    expect(within(viewport).queryByRole('button', { name: '正文分区 0' })).not.toBeInTheDocument()
    expect(screen.getByText(/机器剔除 0 段/)).toBeVisible()
    expect(screen.getByRole('img', { name: 'warning 说明' })).toBeInTheDocument()
    expect(screen.queryByText(/个候选分区/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '收起原文' }))
    expect(screen.queryByTestId('stage0-segment-viewport')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '展开原文' }))
    expect(await screen.findByTestId('stage0-segment-viewport')).toBeInTheDocument()
  })

  it('机器剔除的分区红底展示，并进入机器剔除账', async () => {
    vi.spyOn(api, 'getStage0Report').mockResolvedValue({
      original_filename: 'book.txt',
      total_code_points: 30,
      warnings: [],
      executable: true,
      revision_id: 'a'.repeat(64),
      revision_origin: 'machine' as const,
    })
    vi.spyOn(api, 'getStage0Segments').mockResolvedValue({
      available: true,
      reason: null,
      segments: [
        {
          id: 'keep-1',
          kind: 'keep',
          start: 0,
          end: 12,
          chars: 12,
          flagged: false,
          text: '第一段正文内容',
          origin: null,
          removed_kind: null,
          marker_line: null,
        },
        {
          id: 'remove-1',
          kind: 'remove',
          start: 12,
          end: 30,
          chars: 18,
          flagged: true,
          text: '番外\n\n番外体的内容。',
          origin: 'machine',
          removed_kind: '番外',
          marker_line: '番外',
        },
      ],
    })
    renderRoute(<Stage0Screen />)

    expect(await screen.findByText(/机器剔除 1 段 · 18 码点/)).toBeVisible()
    const removedNode = document.querySelector('[data-segment-id="remove-1"]')
    expect(removedNode).not.toBeNull()
    expect(removedNode).toHaveClass('bg-remove-bg', 'text-remove')
    expect(screen.getByText('番外 · 18 字')).toBeVisible()
  })
})
