import { api } from '@/api/endpoints'
import { ApiError } from '@/api/errors'
import { agentCapabilities, READY_PROFILES } from '@/test/agent'
import { renderRoute } from '@/test/render'
import type {
  AgentProfileSettingsUpdate,
  AgentProfileSettingsView,
  NetworkSettingsUpdate,
  NetworkSettingsView,
} from '@/types/agent'
import { screen, waitFor, within } from '@testing-library/react'
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
    vi.spyOn(api, 'getNetworkSettings').mockResolvedValue({
      state: 'unset',
      network: null,
      revision: 'e'.repeat(64),
      updated_at: null,
      issue: null,
    })
  })

  it('按读取 revision 保存完整常用配置，不把产品 default_profiles 改成用户偏好', async () => {
    const user = userEvent.setup()
    const save = vi.spyOn(api, 'saveAgentProfileSettings').mockImplementation(async (req) =>
      saved(req),
    )

    renderRoute(<SettingsScreen />, '/n/book-1/settings')

    expect(await screen.findByText('已配置')).toBeVisible()
    expect(screen.getByText(/产品默认仍为 null/)).toBeVisible()
    expect(screen.getByText('模型执行配置')).toBeVisible()
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认模型执行配置 Effort' }),
      'max',
    )
    await user.click(screen.getByRole('button', { name: '保存常用配置' }))

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save.mock.calls[0]?.[0]).toMatchObject({
      expected_revision: 'a'.repeat(64),
      profiles: {
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
      screen.getByRole('combobox', { name: '默认模型执行配置 Effort' }),
      'max',
    )
    await user.click(screen.getByRole('button', { name: '保存常用配置' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/另一个页面已经更新/)
    expect(save.mock.calls[0]?.[0].expected_revision).toBe('a'.repeat(64))
  })

  it('可把默认模型切到 Flash，并为钉死节点保留 Pro 覆盖后保存', async () => {
    const user = userEvent.setup()
    const save = vi.spyOn(api, 'saveAgentProfileSettings').mockImplementation(async (req) =>
      saved(req),
    )

    renderRoute(<SettingsScreen />, '/n/book-1/settings')
    await screen.findByText('已配置')
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认模型执行配置 Model' }),
      'deepseek-flash',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认模型执行配置 Thinking' }),
      'on',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: '默认模型执行配置 Effort' }),
      'high',
    )
    await waitFor(() => expect(
      screen.getByRole('combobox', { name: 'node.dynamic-b 模型执行配置 Model' }),
    ).toHaveValue('deepseek-v4-pro'))
    await user.click(screen.getByRole('button', { name: '保存常用配置' }))

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save.mock.calls[0]?.[0]).toMatchObject({
      profiles: {
        schema_version: 4,
        executor_default: {
          kind: 'legacy',
          profile: {
            provider: 'deepseek',
            model: 'deepseek-flash',
            reasoning_effort: 'high',
            thinking_mode: 'on',
          },
        },
        executor_overrides: {
          'node.dynamic-b': {
            kind: 'legacy',
            profile: {
              provider: 'deepseek',
              model: 'deepseek-v4-pro',
              thinking_mode: 'on',
              reasoning_effort: 'low',
            },
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

  it('网络偏好默认直连，可在设置页切到走代理并带地址保存', async () => {
    const user = userEvent.setup()
    const save = vi.spyOn(api, 'saveNetworkSettings').mockImplementation(
      async (req: NetworkSettingsUpdate): Promise<NetworkSettingsView> => ({
        state: req.network === null ? 'unset' : 'configured',
        network: req.network,
        revision: 'f'.repeat(64),
        updated_at: 4,
        issue: null,
      }),
    )

    renderRoute(<SettingsScreen />, '/n/book-1/settings')
    const card = await screen.findByTestId('network-settings')
    expect(within(card).getByText('未配置（按直连）')).toBeVisible()
    expect(within(card).getByLabelText('直连（默认）')).toBeChecked()
    // 没有改动时保存不可点：不把「读到的默认」当成一次用户选择。
    expect(within(card).getByRole('button', { name: '保存网络偏好' })).toBeDisabled()

    await user.click(within(card).getByLabelText('走代理'))
    await user.type(within(card).getByPlaceholderText('http://127.0.0.1:7890'), 'http://127.0.0.1:7890')
    const saveButton = within(card).getByRole('button', { name: '保存网络偏好' })
    await waitFor(() => expect(saveButton).toBeEnabled())
    await user.click(saveButton)

    await waitFor(() => expect(save).toHaveBeenCalledWith({
      network: { mode: 'proxy', proxy_url: 'http://127.0.0.1:7890' },
      expected_revision: 'e'.repeat(64),
    }))
    expect(within(card).getByText('已配置')).toBeVisible()
    expect(within(card).getByDisplayValue('http://127.0.0.1:7890')).toBeVisible()
  })

  it('走代理但地址为空时拒绝保存', async () => {
    const user = userEvent.setup()
    const save = vi.spyOn(api, 'saveNetworkSettings')

    renderRoute(<SettingsScreen />, '/n/book-1/settings')
    const card = await screen.findByTestId('network-settings')
    await user.click(within(card).getByLabelText('走代理'))

    expect(within(card).getByText('走代理必须填代理地址。')).toBeVisible()
    expect(within(card).getByRole('button', { name: '保存网络偏好' })).toBeDisabled()
    expect(save).not.toHaveBeenCalled()
  })

  it('网络偏好 409 冲突时如实提示且不覆盖', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'saveNetworkSettings').mockRejectedValue(
      new ApiError('server', '/settings/network', {
        status: 409,
        backendKind: 'NetworkSettingsConflict',
        message: '网络偏好已被另一个页面更新',
      }),
    )

    renderRoute(<SettingsScreen />, '/n/book-1/settings')
    const card = await screen.findByTestId('network-settings')
    await user.click(within(card).getByLabelText('走代理'))
    await user.type(within(card).getByPlaceholderText('http://127.0.0.1:7890'), 'http://127.0.0.1:9999')
    const saveButton = within(card).getByRole('button', { name: '保存网络偏好' })
    await waitFor(() => expect(saveButton).toBeEnabled())
    await user.click(saveButton)

    expect(await within(card).findByText(/另一个页面已经更新了网络偏好/)).toBeVisible()
  })
})
