import { api } from '@/api/endpoints'
import { ApiError } from '@/api/errors'
import { agentCapabilities, READY_PROFILES } from '@/test/agent'
import { renderRoute } from '@/test/render'
import type {
  AgentProfileSettingsUpdate,
  AgentProfileSettingsView,
} from '@/types/agent'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SettingsScreen } from './SettingsScreen'

const CONFIGURED: AgentProfileSettingsView = {
  state: 'configured',
  profiles: READY_PROFILES,
  revision: 'a'.repeat(64),
  updated_at: 1,
  issue: null,
}

function saved(req: AgentProfileSettingsUpdate): AgentProfileSettingsView {
  if (!req.profiles) throw new Error('本用例只保存非空 profiles')
  return {
    state: 'configured',
    profiles: req.profiles,
    revision: 'b'.repeat(64),
    updated_at: 2,
    issue: null,
  }
}

describe('SettingsScreen', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities(null))
    vi.spyOn(api, 'getAgentProfileSettings').mockResolvedValue(CONFIGURED)
  })

  it('按读取 revision 保存完整常用配置，不把产品 default_profiles 改成用户偏好', async () => {
    const user = userEvent.setup()
    const save = vi.spyOn(api, 'saveAgentProfileSettings').mockImplementation(async (req) =>
      saved(req),
    )

    renderRoute(<SettingsScreen />, '/n/book-1/settings')

    expect(await screen.findByText('已配置')).toBeVisible()
    expect(screen.getByText(/产品默认仍为 null/)).toBeVisible()
    await user.selectOptions(
      screen.getByRole('combobox', { name: '指挥大脑 模型' }),
      'commander-model-v10',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '指挥大脑 思考强度' }),
      'new-effort-dynamic',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '指挥大脑 执行模式' }),
      'new-mode-dynamic',
    )
    await user.click(screen.getByRole('button', { name: '保存常用配置' }))

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save.mock.calls[0]?.[0]).toMatchObject({
      expected_revision: 'a'.repeat(64),
      profiles: {
        commander: {
          model: 'commander-model-v10',
          reasoning_effort: 'new-effort-dynamic',
          execution_mode: 'new-mode-dynamic',
        },
      },
    })
    expect(await screen.findByText(/当前表单已与读取快照一致/)).toBeVisible()
    expect(screen.getByText('bbbbbbbbbbbb')).toBeVisible()
  })

  it('并发冲突保持显式，旧 revision 不会静默覆盖', async () => {
    const user = userEvent.setup()
    const save = vi.spyOn(api, 'saveAgentProfileSettings').mockRejectedValue(new ApiError(
      'server',
      '/settings/agent-profiles',
      {
        status: 409,
        backendKind: 'AgentProfileSettingsConflict',
        message: 'stale revision',
      },
    ))

    renderRoute(<SettingsScreen />, '/n/book-1/settings')
    await screen.findByText('已配置')
    await user.selectOptions(
      screen.getByRole('combobox', { name: '指挥大脑 模型' }),
      'commander-model-v10',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '指挥大脑 思考强度' }),
      'new-effort-dynamic',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '指挥大脑 执行模式' }),
      'new-mode-dynamic',
    )
    await user.click(screen.getByRole('button', { name: '保存常用配置' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/另一个页面已经更新/)
    expect(save.mock.calls[0]?.[0].expected_revision).toBe('a'.repeat(64))
  })

  it('可把默认 Executor 显式切到 Legacy DeepSeek 并保存独立 profile', async () => {
    const user = userEvent.setup()
    const save = vi.spyOn(api, 'saveAgentProfileSettings').mockImplementation(async (req) =>
      saved(req),
    )

    renderRoute(<SettingsScreen />, '/n/book-1/settings')
    await screen.findByText('已配置')
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认执行者 后端' }),
      'legacy',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认执行者 · Legacy API provider' }),
      'deepseek',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认执行者 · Legacy API 模型' }),
      'deepseek-v4-pro',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认执行者 · Legacy API 思考模式' }),
      'on',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认执行者 · Legacy API 思考强度' }),
      'high',
    )
    // 切到 legacy 后表单自动补覆盖：钉死节点预选 Pro；agent-only 节点预置空
    // Agent 草稿，需要显式选 driver/模型/强度后保存才解锁。
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'node.agent-only 执行者 · Agent CLI driver' }),
      'executor-dynamic',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'node.agent-only 执行者 · Agent CLI 模型' }),
      'executor-model-v7',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'node.agent-only 执行者 · Agent CLI 思考强度' }),
      'deep-dynamic',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'node.agent-only 执行者 · Agent CLI 执行模式' }),
      'worker-dynamic',
    )
    await user.click(screen.getByRole('button', { name: '保存常用配置' }))

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save.mock.calls[0]?.[0]).toMatchObject({
      profiles: {
        schema_version: 2,
        executor_default: {
          kind: 'legacy',
          profile: {
            provider: 'deepseek',
            model: 'deepseek-v4-pro',
            reasoning_effort: 'high',
            thinking_mode: 'on',
          },
        },
        executor_overrides: {
          'node.agent-only': {
            kind: 'agent',
            profile: { driver: 'executor-dynamic', model: 'executor-model-v7' },
          },
        },
      },
    })
  })

  it('不可读设置可凭原始 revision 清空修复', async () => {
    const user = userEvent.setup()
    vi.mocked(api.getAgentProfileSettings).mockResolvedValue({
      state: 'unreadable',
      profiles: null,
      revision: 'c'.repeat(64),
      updated_at: null,
      issue: '常用 Agent 配置无法按当前契约读取',
    })
    const save = vi.spyOn(api, 'saveAgentProfileSettings').mockResolvedValue({
      state: 'unset',
      profiles: null,
      revision: 'd'.repeat(64),
      updated_at: 3,
      issue: null,
    })

    renderRoute(<SettingsScreen />, '/n/book-1/settings')
    expect(await screen.findByText('配置不可读')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '清空常用配置' }))

    await waitFor(() => expect(save).toHaveBeenCalledWith({
      profiles: null,
      expected_revision: 'c'.repeat(64),
    }))
    expect(await screen.findByText('尚未配置')).toBeVisible()
  })
})
