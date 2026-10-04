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
import { act, fireEvent, screen, waitFor } from '@testing-library/react'
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
      schema_version: 4,
      executor_default: {
        kind: 'legacy',
        profile: {
          provider: 'deepseek',
          model: 'deepseek-v4-pro',
          reasoning_effort: 'max',
          thinking_mode: 'on',
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
    expect(screen.getByRole('combobox', { name: '默认模型执行配置 Effort' })).toHaveValue('max')
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
    expect(screen.getByRole('combobox', { name: '默认模型执行配置 Model' })).toHaveValue(
      'deepseek-v4-pro',
    )
    expect(screen.getByRole('button', {
      name: '需先授权模型调用费用',
    })).toBeDisabled()
  })

  it('default_profiles=null 时不猜选项，Provider 保持空选', async () => {
    const capabilityCall = vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities(null))
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
    expect(screen.getByRole('combobox', { name: '默认模型执行配置 Provider' })).toHaveValue('')
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
      screen.getByRole('combobox', { name: '默认模型执行配置 Provider' }),
      'deepseek',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认模型执行配置 Model' }),
      'deepseek-v4-pro',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认模型执行配置 Thinking' }),
      'on',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认模型执行配置 Effort' }),
      'high',
    )

    await waitFor(() => expect(preview).toHaveBeenCalledTimes(1))
    expect(preview.mock.calls[0]?.[0].profiles).toEqual(READY_PROFILES)

    await user.click(screen.getByRole('checkbox', { name: '单独配置 node.dynamic-a' }))
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(2))
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'node.dynamic-a 模型执行配置 Effort' }),
      'max',
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
            kind: 'legacy',
            profile: {
              model: 'deepseek-v4-pro',
              reasoning_effort: 'max',
              thinking_mode: 'on',
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
      screen.getByRole('combobox', { name: '默认模型执行配置 Effort' }),
      'max',
    )
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(2))
    expect(preview.mock.calls.at(-1)?.[0].profiles).toMatchObject({
      executor_default: { kind: 'legacy', profile: { reasoning_effort: 'max' } },
    })

    current = agentCapabilities()
    current.legacy_providers[0].models = current.legacy_providers[0].models.filter(
      (model) => model.id !== 'deepseek-v4-pro',
    )
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: qk.agentCapabilities() })
    })
    await waitFor(() => expect(screen.getByText(/deepseek-v4-pro 不属于/)).toBeVisible())
    expect(screen.getByRole('combobox', { name: '默认模型执行配置 Model' })).toHaveValue('')
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

  it('preview 按后端现实返回 null argv/prompt 时不崩溃，命令框给占位且不渲染 prompt 面', async () => {
    // 后端唯一构造点恒传 None（Commander 退役）；历史 bug 正是夹具从不走 null 路径。
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities())
    vi.spyOn(api, 'previewAgentRun').mockImplementation(async (req) => ({
      ...previewOf(req.profiles, req.authorized, req.egress_authorized),
      argv: null,
      prompt: null,
    }))
    const start = vi.spyOn(api, 'startAgentRun').mockImplementation(async (req) => runningRecord(req))

    renderRoute(
      <AgentRunDialog sid="book-1" onClose={vi.fn()} onStarted={vi.fn()} />,
      '/n/book-1/stage0',
    )
    expect(await screen.findByText(/不再摊开顶层命令行/)).toBeInTheDocument()
    expect(screen.getByTestId('agent-command-preview')).toHaveTextContent(
      /不再摊开顶层命令行/,
    )
    expect(screen.queryByRole('button', { name: /展开 prompt/ })).not.toBeInTheDocument()
    fireEvent.click(
      await screen.findByRole('checkbox', { name: /允许本会话产生外部模型调用费用/ }),
    )
    // 非续跑模式正文外发默认已授权，不再多点把它关掉。
    const launch = await screen.findByRole('button', { name: '授权并开始跑书' })
    await waitFor(() => expect(launch).toBeEnabled())
    fireEvent.click(launch)
    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
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
