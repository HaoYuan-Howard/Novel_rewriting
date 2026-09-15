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
  PipelineAgentProfiles,
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

  it('按原配置继续使用服务端历史完整快照，两项授权重新确认，不读取常用配置', async () => {
    const source = { run_id: 'old-run', source_sha256: 'f'.repeat(64) }
    const originalProfiles = { ...READY_PROFILES, concurrency: {
      node_overrides: { 'node.dynamic-a': 6 }, per_book: 6, per_provider: 8,
    } }
    vi.spyOn(api, 'getAgentRunContinuation').mockResolvedValue({
      run_id: source.run_id, sid: 'book-1', available: true, reason: '原 owner 已停止',
      profiles: originalProfiles, source, pause_before_node: 'stage2.blueprint',
    })
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities(null))
    const preview = vi.spyOn(api, 'previewAgentRun').mockImplementation(async (req) => ({
      ...previewOf(req.profiles, req.authorized, req.egress_authorized), continuation: req.continuation,
      pause_before_node: 'stage2.blueprint',
    }))
    const start = vi.spyOn(api, 'startAgentRun').mockImplementation(async (req) => runningRecord(req))
    renderRoute(<AgentRunDialog sid="book-1" sourceRunId="old-run" onClose={vi.fn()} onStarted={vi.fn()} />)
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(1))
    expect(api.getAgentProfileSettings).not.toHaveBeenCalled()
    expect(preview.mock.calls[0]?.[0]).toMatchObject({ profiles: originalProfiles, continuation: source,
      authorized: false, egress_authorized: false })
    expect(preview.mock.calls[0]?.[0]).not.toHaveProperty('pause_before_node')
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    const boxes = screen.getAllByRole('checkbox')
    fireEvent.click(boxes[0]!)
    fireEvent.click(boxes[1]!)
    const launch = await screen.findByRole('button', { name: '授权并开始跑书' })
    await waitFor(() => expect(launch).toBeEnabled())
    fireEvent.click(launch)
    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    expect(start.mock.calls[0]?.[0]).toEqual(preview.mock.calls.at(-1)?.[0])
    expect(start.mock.calls[0]?.[0]).not.toHaveProperty('pause_before_node')
  })

  it('续跑预览意外解除原范围时拒绝启动，不把默认 null 当成用户扩权', async () => {
    const source = { run_id: 'bounded-run', source_sha256: 'b'.repeat(64) }
    vi.spyOn(api, 'getAgentRunContinuation').mockResolvedValue({
      run_id: source.run_id, sid: 'book-1', available: true, reason: '原范围已安全排空',
      profiles: READY_PROFILES, source, pause_before_node: 'stage2.blueprint',
    })
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities(null))
    vi.spyOn(api, 'previewAgentRun').mockImplementation(async (req) => ({
      ...previewOf(req.profiles, req.authorized, req.egress_authorized),
      continuation: source, pause_before_node: null,
    }))
    const start = vi.spyOn(api, 'startAgentRun')
    renderRoute(<AgentRunDialog sid="book-1" sourceRunId="bounded-run" onClose={vi.fn()} onStarted={vi.fn()} />)
    await waitFor(() => expect(api.previewAgentRun).toHaveBeenCalled())
    for (const box of screen.getAllByRole('checkbox')) fireEvent.click(box)
    expect(await screen.findByRole('button', { name: '授权并开始跑书' })).toBeDisabled()
    expect(start).not.toHaveBeenCalled()
  })

  it('并发选项来自 capability，目标与两个总上限随同一预览启动', async () => {
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities())
    const preview = vi.spyOn(api, 'previewAgentRun').mockImplementation(async (req) =>
      previewOf(req.profiles, req.authorized, req.egress_authorized))
    const start = vi.spyOn(api, 'startAgentRun').mockImplementation(async (req) => runningRecord(req))
    renderRoute(<AgentRunDialog sid="book-1" onClose={vi.fn()} onStarted={vi.fn()} />)
    fireEvent.click(await screen.findByRole('checkbox', { name: '自定义并发' }))
    fireEvent.change(screen.getByRole('combobox', { name: 'node.dynamic-a 并发' }), { target: { value: '6' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: '整书总并发' }), { target: { value: '6' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /允许本会话产生外部模型调用费用/ }))
    const launch = screen.getByRole('button', { name: '授权并开始跑书' })
    await waitFor(() => expect(launch).toBeEnabled())
    expect(preview.mock.calls.at(-1)?.[0].profiles.concurrency).toEqual({
      node_overrides: { 'node.dynamic-a': 6 }, per_book: 6, per_provider: 8,
    })
    fireEvent.click(launch)
    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    expect(start.mock.calls[0]?.[0]).toBe(preview.mock.calls.at(-1)?.[0])
  })

  it('原配置或停止证明不可用时保留原因，禁止回落默认或启动', async () => {
    vi.spyOn(api, 'getAgentRunContinuation').mockResolvedValue({
      run_id: 'bad-source', sid: 'book-1', available: false, reason: '原 profiles 缺失', profiles: null, source: null,
    })
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities())
    const preview = vi.spyOn(api, 'previewAgentRun')
    renderRoute(<AgentRunDialog sid="book-1" sourceRunId="bad-source" onClose={vi.fn()} onStarted={vi.fn()} />)
    expect(await screen.findByText('原 profiles 缺失')).toBeVisible()
    expect(preview).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '需先授权模型调用费用' })).toBeDisabled()
  })

  it('用户常用配置优先于产品默认，并作为本次 preview 的完整快照', async () => {
    const savedProfiles: PipelineAgentProfiles = {
      schema_version: 3,
      executor_default: {
        kind: 'agent',
        profile: {
          driver: 'executor-dynamic',
          model: 'executor-model-v8',
          reasoning_effort: 'fast-dynamic',
          execution_mode: 'worker-dynamic',
        },
      },
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
    expect(screen.getByRole('combobox', { name: '默认执行者 · Agent CLI 模型' })).toHaveValue(
      'executor-model-v8',
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
    expect(screen.getByRole('combobox', { name: '默认执行者 · Agent CLI 模型' })).toHaveValue(
      'executor-model-v7',
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

    // 表单不再代选任何后端；显式选择 Agent CLI 后才进入 driver 选择面。
    fireEvent.change(screen.getByRole('combobox', { name: '默认执行者 后端' }), {
      target: { value: 'agent' },
    })
    const executor = screen.getByRole('combobox', { name: '默认执行者 · Agent CLI driver' })
    expect(executor).toHaveValue('')
    expect(within(executor).getByRole('option', { name: '驱动-missing · 未安装' })).toBeDisabled()
    expect(within(executor).getByRole('option', { name: '驱动-unknown · 状态未知' })).toBeDisabled()
    expect(within(executor).getByRole('option', { name: '驱动-unauthenticated · 未登录' })).toBeDisabled()
    const states = screen.getByTestId('agent-executor-default-agent-availability')
    expect(states).toHaveTextContent(/驱动-missing · 未安装.*探针原因-missing/)
    expect(states).toHaveTextContent(/驱动-unknown · 状态未知.*探针原因-unknown/)
    expect(states).toHaveTextContent(/驱动-unauthenticated · 未登录.*探针原因-unauthenticated/)
    expect(states).toHaveTextContent(/动态执行 CLI · 可用.*动态执行探针通过/)
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
      screen.getByRole('combobox', { name: '默认执行者 后端' }),
      'agent',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认执行者 · Agent CLI driver' }),
      'executor-dynamic',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认执行者 · Agent CLI 模型' }),
      'executor-model-v7',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认执行者 · Agent CLI 思考强度' }),
      'deep-dynamic',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认执行者 · Agent CLI 执行模式' }),
      'worker-dynamic',
    )

    await waitFor(() => expect(preview).toHaveBeenCalledTimes(1))
    expect(preview.mock.calls[0]?.[0].profiles).toEqual(READY_PROFILES)
    expect(screen.getByText('动态执行模型七')).toBeVisible()

    await user.click(screen.getByRole('checkbox', { name: '单独配置 node.dynamic-a' }))
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(2))
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'node.dynamic-a 执行者 · Agent CLI 模型' }),
      'executor-model-v8',
    )
    // 模型切换后 effort/mode 已清空；这份半成品不准触发下一次 preview。
    expect(screen.getByRole('combobox', {
      name: 'node.dynamic-a 执行者 · Agent CLI 思考强度',
    })).toHaveValue('')
    expect(screen.getByRole('combobox', {
      name: 'node.dynamic-a 执行者 · Agent CLI 执行模式',
    })).toHaveValue('')
    expect(screen.getByRole('button', {
      name: '需先授权模型调用费用',
    })).toBeDisabled()

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'node.dynamic-a 执行者 · Agent CLI 思考强度' }),
      'fast-dynamic',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'node.dynamic-a 执行者 · Agent CLI 执行模式' }),
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
            kind: 'agent',
            profile: {
              model: 'executor-model-v8',
              reasoning_effort: 'fast-dynamic',
            },
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
      screen.getByRole('combobox', { name: '默认执行者 · Agent CLI 模型' }),
      'executor-model-v8',
    )
    expect(screen.getByRole('combobox', { name: '默认执行者 · Agent CLI 思考强度' })).toHaveValue('')
    expect(screen.getByRole('button', {
      name: '需先授权模型调用费用',
    })).toBeDisabled()
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认执行者 · Agent CLI 思考强度' }),
      'fast-dynamic',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认执行者 · Agent CLI 执行模式' }),
      'worker-dynamic',
    )
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(2))
    expect(preview.mock.calls.at(-1)?.[0].profiles).toMatchObject({
      executor_default: { kind: 'agent', profile: { model: 'executor-model-v8' } },
    })

    current = agentCapabilities()
    current.drivers[0].models = current.drivers[0].models.filter(
      (model) => model.id !== 'executor-model-v8',
    )
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: qk.agentCapabilities() })
    })
    await waitFor(() => expect(screen.getByText(/executor-model-v8 不属于当前 driver/)).toBeVisible())
    expect(screen.getByRole('combobox', { name: '默认执行者 · Agent CLI 模型' })).toHaveValue('')
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
