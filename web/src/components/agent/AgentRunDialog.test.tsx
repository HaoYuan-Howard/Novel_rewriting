import { api } from '@/api/endpoints'
import { ApiError } from '@/api/errors'
import { qk } from '@/api/queryKeys'
import {
  agentCapabilities,
  previewOf,
  READY_PROFILES,
} from '@/test/agent'
import type {
  AgentCapabilitiesResponse,
  AgentProfileSettingsView,
  AgentRunRecord,
  AgentRunRequest,
} from '@/types/agent'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { AgentRunDialog } from './AgentRunDialog'

function runningRecord(req: AgentRunRequest): AgentRunRecord {
  return {
    run_id: 'run-dynamic',
    start: {
      sid: req.sid,
      argv: ['agent-dynamic'],
      prompt: '动态 prompt',
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

function fourStateCapabilities(): AgentCapabilitiesResponse {
  const base = agentCapabilities(null)
  return {
    ...base,
    drivers: [
      ...base.drivers,
      ...(['missing', 'unknown', 'unauthenticated'] as const).map((availability) => ({
        id: 'driver-' + availability,
        provider: 'provider-' + availability,
        label: '驱动-' + availability,
        roles: ['commander' as const],
        availability,
        availability_reason: '探针原因-' + availability,
        version: null,
        models: [{
          id: 'model-' + availability,
          label: '模型-' + availability,
          reasoning_efforts: ['effort-' + availability],
          execution_modes: ['mode-' + availability],
        }],
      })),
    ],
  }
}

const UNSET_SETTINGS: AgentProfileSettingsView = {
  state: 'unset',
  profiles: null,
  revision: '0'.repeat(64),
  updated_at: null,
  issue: null,
}

describe('AgentRunDialog', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getAgentProfileSettings').mockResolvedValue(UNSET_SETTINGS)
  })

  it('用户常用配置优先于产品默认，并作为本次 preview 的完整快照', async () => {
    const savedProfiles = {
      commander: {
        driver: 'commander-dynamic' as const,
        model: 'commander-model-v10',
        reasoning_effort: 'new-effort-dynamic',
        execution_mode: 'new-mode-dynamic',
      },
      executor_default: { ...READY_PROFILES.executor_default },
      executor_overrides: {},
    }
    vi.mocked(api.getAgentProfileSettings).mockResolvedValue({
      state: 'configured',
      profiles: savedProfiles,
      revision: 'a'.repeat(64),
      updated_at: 1,
      issue: null,
    })
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities())
    const preview = vi.spyOn(api, 'previewAgentRun').mockImplementation(async (req) =>
      previewOf(req.profiles, req.authorized, req.egress_authorized),
    )

    renderRoute(
      <AgentRunDialog sid="book-1" onClose={vi.fn()} onStarted={vi.fn()} />,
      '/n/book-1/stage0',
    )

    expect(await screen.findByText(/已载入设置页保存的常用配置/)).toBeVisible()
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('combobox', { name: '指挥大脑 模型' })).toHaveValue(
      'commander-model-v10',
    )
    expect(preview.mock.calls[0]?.[0].profiles).toEqual(savedProfiles)
  })

  it('capability 加载期间关闭预览；ready 后才用服务端默认初始化一次', async () => {
    let resolveCapabilities!: (value: AgentCapabilitiesResponse) => void
    vi.spyOn(api, 'getAgentCapabilities').mockReturnValue(new Promise((resolve) => {
      resolveCapabilities = resolve
    }))
    const preview = vi.spyOn(api, 'previewAgentRun').mockImplementation(async (req) =>
      previewOf(req.profiles, req.authorized, req.egress_authorized),
    )

    renderRoute(
      <AgentRunDialog sid="book-1" onClose={vi.fn()} onStarted={vi.fn()} />,
      '/n/book-1/stage0',
    )
    expect(screen.getByText(/正在读取常用配置并探测本机 Agent 能力/)).toBeVisible()
    expect(preview).not.toHaveBeenCalled()
    expect(screen.getByRole('button', {
      name: '需先授权模型调用费用',
    })).toBeDisabled()

    act(() => resolveCapabilities(agentCapabilities()))
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('combobox', { name: '指挥大脑 模型' })).toHaveValue(
      'commander-model-v9',
    )
    expect(screen.getByRole('button', {
      name: '需先授权模型调用费用',
    })).toBeDisabled()
  })

  it('default_profiles=null 时不猜选项，四态均可见且非 ready driver 不可选', async () => {
    const capabilities = fourStateCapabilities()
    const capabilityCall = vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(capabilities)
    const preview = vi.spyOn(api, 'previewAgentRun')

    renderRoute(
      <AgentRunDialog sid="book-1" onClose={vi.fn()} onStarted={vi.fn()} />,
      '/n/book-1/stage0',
    )

    expect(await screen.findByText(/尚未保存常用 Agent 配置/)).toBeVisible()
    expect(capabilityCall).toHaveBeenCalledTimes(1)
    expect(preview).not.toHaveBeenCalled()
    expect(screen.getByRole('button', {
      name: '需先授权模型调用费用',
    })).toBeDisabled()

    const commander = screen.getByRole('combobox', { name: '指挥大脑 driver' })
    expect(within(commander).getByRole('option', { name: '驱动-missing · 未安装' })).toBeDisabled()
    expect(within(commander).getByRole('option', { name: '驱动-unknown · 状态未知' })).toBeDisabled()
    expect(within(commander).getByRole('option', { name: '驱动-unauthenticated · 未登录' })).toBeDisabled()
    const states = screen.getByTestId('agent-commander-availability')
    expect(states).toHaveTextContent(/驱动-missing · 未安装.*探针原因-missing/)
    expect(states).toHaveTextContent(/驱动-unknown · 状态未知.*探针原因-unknown/)
    expect(states).toHaveTextContent(/驱动-unauthenticated · 未登录.*探针原因-unauthenticated/)
    expect(states).toHaveTextContent(/动态指挥 CLI · 可用.*动态指挥探针通过/)
  })

  it('动态选项、逐节点覆盖与授权变化都重新 preview，start 复用最后成功 preview 的同一深快照', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities(null))
    const preview = vi.spyOn(api, 'previewAgentRun').mockImplementation(async (req) =>
      previewOf(req.profiles, req.authorized, req.egress_authorized),
    )
    const start = vi.spyOn(api, 'startAgentRun').mockImplementation(async (req) =>
      runningRecord(req),
    )

    renderRoute(
      <AgentRunDialog sid="book-1" onClose={vi.fn()} onStarted={vi.fn()} />,
      '/n/book-1/stage0',
    )
    await screen.findByText(/界面不会猜第一项/)

    await user.selectOptions(
      screen.getByRole('combobox', { name: '指挥大脑 driver' }),
      'commander-dynamic',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '指挥大脑 模型' }),
      'commander-model-v9',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '指挥大脑 思考强度' }),
      'careful-dynamic',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '指挥大脑 执行模式' }),
      'interactive-dynamic',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认执行者 driver' }),
      'executor-dynamic',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认执行者 模型' }),
      'executor-model-v7',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认执行者 思考强度' }),
      'deep-dynamic',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认执行者 执行模式' }),
      'worker-dynamic',
    )

    await waitFor(() => expect(preview).toHaveBeenCalledTimes(1))
    expect(preview.mock.calls[0]?.[0].profiles).toEqual(READY_PROFILES)
    expect(screen.getByText('动态指挥模型九')).toBeVisible()
    expect(screen.getByText('动态执行模型七')).toBeVisible()

    await user.click(screen.getByRole('checkbox', { name: '单独配置 node.dynamic-a' }))
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(2))
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'node.dynamic-a 执行者 模型' }),
      'executor-model-v8',
    )
    // 模型切换后 effort/mode 已清空；这份半成品不准触发下一次 preview。
    expect(screen.getByRole('combobox', {
      name: 'node.dynamic-a 执行者 思考强度',
    })).toHaveValue('')
    expect(screen.getByRole('combobox', {
      name: 'node.dynamic-a 执行者 执行模式',
    })).toHaveValue('')
    expect(screen.getByRole('button', {
      name: '需先授权模型调用费用',
    })).toBeDisabled()

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'node.dynamic-a 执行者 思考强度' }),
      'fast-dynamic',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'node.dynamic-a 执行者 执行模式' }),
      'worker-dynamic',
    )
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(3))

    const paid = screen.getByRole('checkbox', { name: /允许本会话产生外部模型调用费用/ })
    const egress = screen.getByRole('checkbox', { name: /允许将小说正文及派生上下文/ })
    await user.click(paid)
    await user.click(egress)
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(5))
    const egressRefusedPreview = preview.mock.calls.at(-1)?.[0]
    expect(egressRefusedPreview).toMatchObject({
      sid: 'book-1',
      authorized: true,
      egress_authorized: false,
      profiles: {
        executor_overrides: {
          'node.dynamic-a': {
            model: 'executor-model-v8',
            reasoning_effort: 'fast-dynamic',
          },
        },
      },
    })
    expect(screen.getByRole('button', { name: '需先授权正文外发' })).toBeDisabled()
    await user.click(egress)
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(6))
    const previewRequest = preview.mock.calls.at(-1)?.[0]
    expect(previewRequest).toMatchObject({ authorized: true, egress_authorized: true })

    const button = screen.getByRole('button', { name: '授权并开始跑书' })
    await waitFor(() => expect(button).toBeEnabled())
    await user.click(button)
    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    expect(start.mock.calls[0]?.[0]).toBe(previewRequest)
    expect(start.mock.calls[0]?.[0]).toEqual(previewRequest)
  })

  it('服务端默认只初始化草稿；用户可改，能力刷新失效时不回退或静默重解', async () => {
    const user = userEvent.setup()
    let current = agentCapabilities()
    vi.spyOn(api, 'getAgentCapabilities').mockImplementation(async () => current)
    const preview = vi.spyOn(api, 'previewAgentRun').mockImplementation(async (req) =>
      previewOf(req.profiles, req.authorized, req.egress_authorized),
    )

    const { queryClient } = renderRoute(
      <AgentRunDialog sid="book-1" onClose={vi.fn()} onStarted={vi.fn()} />,
      '/n/book-1/stage0',
    )
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(1))

    await user.selectOptions(
      screen.getByRole('combobox', { name: '指挥大脑 模型' }),
      'commander-model-v10',
    )
    expect(screen.getByRole('combobox', { name: '指挥大脑 思考强度' })).toHaveValue('')
    expect(screen.getByRole('button', {
      name: '需先授权模型调用费用',
    })).toBeDisabled()
    await user.selectOptions(
      screen.getByRole('combobox', { name: '指挥大脑 思考强度' }),
      'new-effort-dynamic',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '指挥大脑 执行模式' }),
      'new-mode-dynamic',
    )
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(2))
    expect(preview.mock.calls.at(-1)?.[0].profiles.commander.model).toBe(
      'commander-model-v10',
    )

    current = agentCapabilities()
    current.drivers[0].models = current.drivers[0].models.filter(
      (model) => model.id !== 'commander-model-v10',
    )
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: qk.agentCapabilities() })
    })
    await waitFor(() => expect(screen.getByText(/commander-model-v10 不属于当前 driver/)).toBeVisible())
    expect(screen.getByRole('combobox', { name: '指挥大脑 模型' })).toHaveValue('')
    expect(screen.getByRole('button', {
      name: '需先授权模型调用费用',
    })).toBeDisabled()
    expect(preview).toHaveBeenCalledTimes(2)
  })

  it('preview 回显的 profiles 漂移时关闭启动', async () => {
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities())
    vi.spyOn(api, 'previewAgentRun').mockResolvedValue({
      ...previewOf(),
      profiles: null,
    })
    const start = vi.spyOn(api, 'startAgentRun')

    renderRoute(
      <AgentRunDialog sid="book-1" onClose={vi.fn()} onStarted={vi.fn()} />,
      '/n/book-1/stage0',
    )

    expect(await screen.findByRole('alert')).toHaveTextContent(/回显.*不一致/)
    const button = screen.getByRole('button', {
      name: '需先授权模型调用费用',
    })
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(start).not.toHaveBeenCalled()
  })

  it('默认收起长命令，并把滚动内容与固定操作栏分开', async () => {
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities())
    vi.spyOn(api, 'previewAgentRun').mockImplementation(async (req) => ({
      ...previewOf(req.profiles, req.authorized, req.egress_authorized),
      argv: ['agent-dynamic', '-p', '很长的 prompt '.repeat(500)],
      prompt: '很长的 prompt '.repeat(500),
    }))

    renderRoute(
      <AgentRunDialog sid="book-1" onClose={vi.fn()} onStarted={vi.fn()} />,
      '/n/book-1/stage0',
    )
    await waitFor(() => expect(api.previewAgentRun).toHaveBeenCalled())

    expect(screen.getByTestId('agent-run-dialog')).toHaveClass(
      'max-h-[calc(100vh-32px)]',
      'flex-col',
    )
    expect(screen.getByTestId('agent-run-dialog-body')).toHaveClass('min-h-0', 'overflow-y-auto')
    expect(screen.getByTestId('agent-run-dialog-footer')).toHaveClass('shrink-0')
    expect(screen.getByTestId('agent-command-preview')).toHaveClass('max-h-[92px]')
    await waitFor(() => expect(screen.getByRole('button', {
      name: '展开完整命令',
    })).toBeEnabled())
    const expand = screen.getByRole('button', { name: '展开完整命令' })
    fireEvent.click(expand)
    await waitFor(() =>
      expect(screen.getByTestId('agent-command-preview')).toHaveClass('max-h-[260px]'),
    )
    expect(screen.getByTestId('agent-run-cost-note')).toHaveTextContent(/只可预览，不会启动/)
  })

  it('同书冲突不再误报成全局只能跑一本', async () => {
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities())
    vi.spyOn(api, 'previewAgentRun').mockImplementation(async (req) =>
      previewOf(req.profiles, req.authorized, req.egress_authorized),
    )
    vi.spyOn(api, 'startAgentRun').mockRejectedValue(new ApiError(
      'server',
      '/agent/runs',
      { status: 409, backendKind: 'JobConflict', message: 'same book is running' },
    ))

    renderRoute(
      <AgentRunDialog sid="book-1" onClose={vi.fn()} onStarted={vi.fn()} />,
      '/n/book-1/stage0',
    )
    const authorization = await screen.findByRole('checkbox', {
      name: /允许本会话产生外部模型调用费用/,
    })
    fireEvent.click(authorization)
    const start = screen.getByRole('button', { name: '授权并开始跑书' })
    await waitFor(() => expect(start).toBeEnabled())
    fireEvent.click(start)

    expect(await screen.findByText(/同一本书互斥；其他书仍可并行/)).toBeVisible()
    expect(screen.queryByText(/一次只跑一个/)).not.toBeInTheDocument()
  })
})
