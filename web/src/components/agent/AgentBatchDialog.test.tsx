import { api } from '@/api/endpoints'
import {
  agentCapabilities,
  OVERRIDE_PROFILE,
  READY_PROFILES,
} from '@/test/agent'
import type {
  AgentBatchRequest,
  AgentQueueView,
  AgentRunRecord,
} from '@/types/agent'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { AgentBatchDialog } from './AgentBatchDialog'

function running(req: AgentBatchRequest, sid: string): AgentRunRecord {
  return {
    run_id: 'run-' + sid,
    start: {
      sid,
      argv: ['agent-dynamic'],
      prompt: 'dynamic',
      authorized: req.authorized,
      egress_authorized: req.egress_authorized,
      code_version: 'code-dynamic',
      profiles: req.profiles,
    },
    contract: null,
    epochs: [],
    active_epoch_id: null,
    started: 1,
    ended: null,
    exit_code: null,
    state: 'running',
    note: null,
    outcome: null,
    allowed_controls: ['stop'],
  }
}

describe('AgentBatchDialog', () => {
  it('default_profiles=null 时不受理，也不拿第一项填充整批配置', async () => {
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities(null))
    vi.spyOn(api, 'getAgentQueue').mockResolvedValue({
      running: [], pending: [], rejected: [], max_concurrent: 3,
    })
    const start = vi.spyOn(api, 'startAgentBatch')

    renderRoute(
      <AgentBatchDialog books={[{ sid: 'book-1', title: '第一本' }]} onClose={vi.fn()} />,
      '/n/book-1/stage0',
    )

    expect(await screen.findByText(/服务端尚未发布默认 Agent 配置/)).toBeVisible()
    expect(screen.getByRole('combobox', { name: '指挥大脑 driver' })).toHaveValue('')
    const button = screen.getByRole('button', {
      name: '需先授权模型调用费用',
    })
    expect(button).toBeDisabled()
    expect(screen.getByText(/仅可预览配置，不会启动任何一本/)).toBeVisible()
    expect(start).not.toHaveBeenCalled()
  })

  it('整批按受理瞬间冻结同一份 profiles 与两项授权，包含逐节点覆盖', async () => {
    const user = userEvent.setup()
    const defaults = {
      commander: { ...READY_PROFILES.commander },
      executor_default: { ...READY_PROFILES.executor_default },
      executor_overrides: {},
    }
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities(defaults))
    vi.spyOn(api, 'getAgentQueue').mockResolvedValue({
      running: [], pending: [], rejected: [], max_concurrent: 3,
    })
    const start = vi.spyOn(api, 'startAgentBatch').mockImplementation(async (req) => {
      const result: AgentQueueView = {
        running: [running(req, req.sids[0])],
        pending: req.sids.slice(1).map((sid, index) => ({ sid, position: index + 1 })),
        rejected: [],
        max_concurrent: 1,
      }
      return result
    })

    renderRoute(
      <AgentBatchDialog
        books={[
          { sid: 'book-1', title: '第一本' },
          { sid: 'book-2', title: '第二本' },
        ]}
        onClose={vi.fn()}
      />,
      '/n/book-1/stage0',
    )

    await screen.findByRole('combobox', { name: '默认执行者 模型' })
    await user.click(screen.getByRole('checkbox', { name: '单独配置 node.dynamic-b' }))
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'node.dynamic-b 执行者 模型' }),
      OVERRIDE_PROFILE.model,
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'node.dynamic-b 执行者 思考强度' }),
      OVERRIDE_PROFILE.reasoning_effort,
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'node.dynamic-b 执行者 执行模式' }),
      OVERRIDE_PROFILE.execution_mode ?? '',
    )
    await user.click(screen.getByRole('checkbox', { name: /允许这一批产生外部模型调用费用/ }))
    await user.click(screen.getByRole('checkbox', { name: /允许把这几本的正文/ }))

    expect(screen.getByRole('button', { name: '需先授权正文外发' })).toBeDisabled()
    expect(screen.getByText(/未授予正文外发.*不会启动任何一本/)).toBeVisible()
    await user.click(screen.getByRole('checkbox', { name: /允许把这几本的正文/ }))
    const button = screen.getByRole('button', { name: '授权并开始跑这 2 本' })
    await waitFor(() => expect(button).toBeEnabled())
    await user.click(button)
    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))

    const request = start.mock.calls[0]?.[0]
    expect(request).toMatchObject({
      sids: ['book-1', 'book-2'],
      authorized: true,
      egress_authorized: true,
      profiles: {
        executor_overrides: { 'node.dynamic-b': OVERRIDE_PROFILE },
      },
    })
    expect(request?.profiles).not.toBe(defaults)

    defaults.executor_default.model = 'later-default-drift'
    expect(request?.profiles.executor_default.model).toBe('executor-model-v7')
    expect(await screen.findByText(/本批受理时的冻结配置/)).toBeVisible()
    expect(screen.getByTestId('agent-profiles-snapshot')).toHaveTextContent('node.dynamic-b')
    expect(screen.getByText('排队中（1）')).toBeVisible()
  })
})
