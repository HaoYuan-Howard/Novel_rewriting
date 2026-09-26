import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import type { UploadResponse } from '@/types/jobs'
import type { Artifact, NovelRow } from '@/types/novels'
import { agentCapabilities, READY_PROFILES } from '@/test/agent'
import { CorpusScreen } from './CorpusScreen'

function uploaded(sid: string): UploadResponse {
  return {
    sid,
    job: {
      id: 'upload-' + sid,
      kind: 'stage0.remove',
      label: '扫描非正文',
      argv: 'novel stage0.remove',
      state: 'ok',
      paid: false,
      started: 1,
      finished: 2,
      sid,
      ordinal: null,
    },
  }
}

function renderCorpus(novels: NovelRow[] = []) {
  vi.spyOn(api, 'getNovels').mockResolvedValue({ base: 'E:/outputs', novels })
  vi.spyOn(api, 'getAgentProfileSettings').mockResolvedValue({
    state: 'unset',
    profiles: null,
    revision: '0'.repeat(64),
    updated_at: null,
    issue: null,
  })
  vi.spyOn(api, 'getAgentQueue').mockResolvedValue({
    running: [],
    pending: [],
    rejected: [],
    max_concurrent: 3,
  })

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={['/']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/" element={<CorpusScreen />} />
          <Route path="/n/:sid/stage0" element={<div>Stage0 人工审校落点</div>} />
          <Route path="/n/:sid/stage1" element={<div>Stage1 分章报告落点</div>} />
          <Route path="/n/:sid/run" element={<div>Run 进度落点</div>} />
          <Route path="/n/:sid/stage5/:ordinal" element={<div>Stage5 未知状态落点</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { queryClient, ...view }
}

function artifact(stage: string, name: string): Artifact {
  return {
    stage,
    name,
    exists: true,
    bytes: 1024,
    mtime: 1,
    provenance: {},
    freshness: 'fresh',
    display_freshness: 'fresh',
    condition: 'ready',
    stale: [],
  }
}

function compactBook(): NovelRow {
  return {
    sid: 'book-1',
    title: '测试书',
    dir: '测试书_book-1',
    dirname: '测试书_book-1',
    chapters: 12,
    stages: {
      stage0: [artifact('stage0', 'removal_report')],
      stage7: [artifact('stage7', 'name_map')],
    },
    stage_done: {
      stage0: true,
      stage1: false,
      stage2: false,
      stage4: false,
      stage5: null,
      stage7: true,
    },
    stage_reviewable: {},
    chapters_done: {},
    summary: {
      verdict: 'pending',
      text: '待继续',
      detail: '下一步：stage1 · stage1 分章',
      artifact_count: 2,
      latest_mtime: 1,
    },
  }
}


async function confirmUploadAuth() {
  expect(await screen.findByTestId('upload-auth-dialog')).toBeVisible()
  expect(screen.getByText('上传并自动开跑')).toBeVisible()
  const boxes = screen.getAllByRole('checkbox')
  fireEvent.click(boxes[0]!)
  fireEvent.click(boxes[1]!)
  const confirm = await screen.findByRole('button', { name: '确认上传并开跑' })
  await waitFor(() => expect(confirm).toBeEnabled())
  fireEvent.click(confirm)
}

function choose(files: File[]) {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]')
  expect(input).not.toBeNull()
  fireEvent.change(input!, { target: { files } })
  return input!
}

describe('CorpusScreen 批量开书', () => {
  it('语料库只保留操作与符号状态，不重复解释流程和下一步', async () => {
    renderCorpus([compactBook()])

    expect(await screen.findByRole('button', { name: '测试书' })).toBeVisible()
    expect(within(screen.getByTestId('corpus-header')).getByRole('textbox', {
      name: '按 sid 或书名过滤',
    })).toBeVisible()
    expect(screen.queryByText('上传后每本书会走的路')).not.toBeInTheDocument()
    expect(screen.queryByText('下一步：stage1 · stage1 分章')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /开一本或一批新书/ }))
    expect(screen.getByText('选择或拖入一本或多本 .txt / .md 原作')).toBeVisible()
    expect(document.querySelector('input[type="file"]')).toHaveAttribute('accept', '.txt,.md')
    expect(
      screen.queryByText('文件名、大小与内容由后端逐本校验；原始字节会完整保留'),
    ).not.toBeInTheDocument()
    expect(screen.queryByText('还没跑过任何一条臂')).not.toBeInTheDocument()
    expect(screen.queryByText('成稿不进产物表，只记跑过哪几条臂')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '打开 →' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Agent' })).toBeVisible()
    expect(screen.getByRole('button', { name: '删除《测试书》项目' })).toBeVisible()
    expect(screen.getByText('Agent 并发上限 3 本')).toBeVisible()

    expect(screen.getByTestId('stage-state-stage0')).toHaveTextContent('✓')
    expect(screen.getByTestId('stage-state-stage0')).not.toHaveTextContent('已完成')
    expect(screen.getByTestId('stage-state-stage7')).toHaveTextContent('✓')
    const stage0Artifact = screen.getByTestId('artifact-stage0-removal_report')
    expect(within(stage0Artifact).getByRole('img', { name: '新鲜' })).toBeVisible()
    expect(within(stage0Artifact).queryByText('新鲜')).not.toBeInTheDocument()
  })

  it('Stage5 展示 canonical 已写章数，不再出现写作臂', async () => {
    const book = compactBook()
    book.chapters_done.stage5 = [1, 2]
    renderCorpus([book])

    expect(await screen.findByText('canonical 2 章')).toBeVisible()
    expect(screen.queryByText('deep')).not.toBeInTheDocument()
    expect(screen.queryByText('shallow')).not.toBeInTheDocument()
  })

  it('打开时只落在完成度已知的现役屏，不越过 done=null 的层', async () => {
    const book = compactBook()
    for (const stage of ['stage0', 'stage1', 'stage2', 'stage3', 'stage4']) {
      book.stage_done[stage] = true
    }
    renderCorpus([book])

    fireEvent.click(await screen.findByRole('button', { name: '测试书' }))

    // stage5 完成度是 null（unknown）时不猜、也不送去已退役的屏；
    // 现役只剩 stage0/stage1，落点是完成度已知的最后一层。
    expect(await screen.findByText('Stage1 分章报告落点')).toBeVisible()
    expect(screen.queryByText('Stage5 未知状态落点')).not.toBeInTheDocument()
  })

  it('stage1 判不了时回退 stage0，不送人进尚不可审阅的层', async () => {
    const book = compactBook()
    book.stage_done = { ...book.stage_done, stage1: null }
    renderCorpus([book])

    fireEvent.click(await screen.findByRole('button', { name: '测试书' }))

    expect(await screen.findByText('Stage0 人工审校落点')).toBeVisible()
    expect(screen.queryByText('Stage1 分章报告落点')).not.toBeInTheDocument()
  })

  it('单本：上传前授权，成功后带幂等键 uploadCorpus 并进入进度屏', async () => {
    const upload = vi.spyOn(api, 'uploadCorpus').mockResolvedValue({
      ...uploaded('fq-a-1001'),
      autostart: 'started',
      idempotency_key: 'key-1',
      run: null,
    })
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities())

    renderCorpus()
    await screen.findByText('一本书都还没有')

    choose([new File(['第一本正文'], 'FQ_甲_1001.txt', { type: 'text/plain' })])
    fireEvent.click(screen.getByRole('button', { name: '授权后上传并自动开跑' }))

    await confirmUploadAuth()

    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1))
    const [file, options] = upload.mock.calls[0]!
    expect(file).toBeInstanceOf(File)
    expect(file.name).toBe('FQ_甲_1001.txt')
    expect(options).toMatchObject({
      authorized: true,
      egressAuthorized: true,
      profiles: READY_PROFILES,
    })
    expect(typeof options.idempotencyKey).toBe('string')
    expect(options.idempotencyKey.length).toBeGreaterThan(8)
    expect(await screen.findByText('Run 进度落点')).toBeVisible()
    expect(screen.queryByText('让 agent 跑这本书')).not.toBeInTheDocument()
  })

  it('批量：共享授权与 profiles，每本独立幂等键', async () => {
    const upload = vi
      .spyOn(api, 'uploadCorpus')
      .mockResolvedValueOnce({ ...uploaded('fq-a-1001'), autostart: 'started' })
      .mockResolvedValueOnce({ ...uploaded('fq-b-1002'), autostart: 'started' })
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities())

    renderCorpus()
    await screen.findByText('一本书都还没有')

    const first = new File(['第一本正文'], 'FQ_甲_1001.txt', { type: 'text/plain' })
    const second = new File(['第二本正文'], 'FQ_乙_1002.txt', { type: 'text/plain' })
    const input = choose([first, second])

    expect(input).toHaveAttribute('multiple')
    expect(screen.getByText('已选择 2 本原作')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '授权后上传这 2 本' }))

    await confirmUploadAuth()

    await waitFor(() => expect(upload).toHaveBeenCalledTimes(2))
    expect(upload.mock.calls[0]![0]).toBe(first)
    expect(upload.mock.calls[1]![0]).toBe(second)
    const key1 = upload.mock.calls[0]![1].idempotencyKey
    const key2 = upload.mock.calls[1]![1].idempotencyKey
    expect(key1).not.toEqual(key2)
    expect(upload.mock.calls[0]![1]).toMatchObject({
      authorized: true,
      egressAuthorized: true,
      profiles: READY_PROFILES,
    })
    expect(upload.mock.calls[1]![1]).toMatchObject({
      authorized: true,
      egressAuthorized: true,
      profiles: READY_PROFILES,
      idempotencyKey: key2,
    })
    expect(screen.queryByText('让 agent 跑这 2 本')).not.toBeInTheDocument()
  })

  it('一本失败不回滚成功项；不再二次弹出 agent 批跑框', async () => {
    const upload = vi
      .spyOn(api, 'uploadCorpus')
      .mockResolvedValueOnce({ ...uploaded('fq-a-1001'), autostart: 'started' })
      .mockRejectedValueOnce(new Error('文件名无法建立 sid'))
    vi.spyOn(api, 'getAgentCapabilities').mockResolvedValue(agentCapabilities())

    renderCorpus()
    await screen.findByText('一本书都还没有')

    const first = new File(['第一本正文'], 'FQ_甲_1001.txt', { type: 'text/plain' })
    const second = new File(['第二本正文'], '命名错误.txt', { type: 'text/plain' })
    choose([first, second])
    fireEvent.click(screen.getByRole('button', { name: '授权后上传这 2 本' }))

    await confirmUploadAuth()

    expect(await screen.findByText(/成功 1 本 · 失败 1 本/)).toBeVisible()
    expect(screen.getByText('命名错误.txt')).toBeVisible()
    expect(screen.getByText(/文件名无法建立 sid/)).toBeVisible()
    expect(screen.getByText(/成功项已按授权自动开跑/)).toBeVisible()
    expect(screen.queryByText('让 agent 跑这 1 本')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /交给 agent/ })).not.toBeInTheDocument()
    expect(upload).toHaveBeenCalledTimes(2)
  })
})
