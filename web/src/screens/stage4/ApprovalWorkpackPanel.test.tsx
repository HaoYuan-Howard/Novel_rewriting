import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, realApi } from '@/api/endpoints'
import { renderRoute } from '@/test/render'
import type {
  ApprovalWorkpack,
  ApprovalWorkpackGroupKind,
  WorkpackChapterReading,
  WorkpackSceneReading,
} from '@/types/approvals'
import { ApprovalWorkpackPanel } from './ApprovalWorkpackPanel'

const SCENE: WorkpackSceneReading = {
  scene_ref: 'c03s01',
  weight: null,
  budget_chars: 800,
  purpose: '以新场景落下事实',
  role: 'setup',
  delivery: 'scene',
  transition: 'start',
  land: ['f03_001'],
  context: [],
  turn: 'f03_001',
  entry_state: [],
  exit_state: [],
  stages: [{ ref: 'b03_01', found: true, time_layer: 'present', place: '门口', present: ['char_001'] }],
  materials: [],
  missing_material_refs: [],
}

function chapterReading(kind: 'design' | 'structure'): WorkpackChapterReading {
  if (kind === 'design') {
    return {
      kind: 'stage4_design_chapter_reading_v1',
      chapter_function: '建立冲突',
      weight: 'CH_EVEN',
      chapter_rewrite_brief: '直接推进',
      structure_decision: { mode: 'retain' },
      facts: [{
        fact_alias: 'a_' + '1'.repeat(16), claim: '她发现门已经打开。',
        kind: 'event', obligation: 'land', participant_aliases: [],
      }],
      goals: { chapter: { intent: 'advance', refs: ['a_' + '1'.repeat(16)] } },
      causal_edges: [],
      narrative_relations: [],
      entry_state: [],
      ending_state: [],
      scenes: [{ ...SCENE, land: ['a_' + '1'.repeat(16)], turn: null, stages: [] }],
    }
  }
  return {
    kind: 'stage4_structure_chapter_reading_v1',
    chapter_function: '建立冲突',
    chapter_rewrite_brief: '直接推进',
    structure_decision: { mode: 'retain' },
    facts: [{ ref: 'f03_001', claim: '她发现门已经打开。', kind: 'event', obligation: 'land' }],
    goals: { chapter: { intent: 'advance', refs: ['f03_001'] } },
    scenes: [SCENE],
    structure_metrics: { state: 'ok', alignment: { mode: 'retain' } },
    binding_concerns: [],
    signature_conflict_codes: [],
  }
}

const GROUPS: ApprovalWorkpackGroupKind[] = [
  'blocking',
  'semantic_attention',
  'disclosure',
  'metric_only',
  'phase_deferred',
  'coverage',
]

function workpack(kind: 'design' | 'structure'): ApprovalWorkpack {
  return {
    v: 2,
    sid: 'book-1',
    kind,
    candidate_sha256: kind === 'design' ? 'b'.repeat(64) : null,
    subject_sha256: 'a'.repeat(64),
    review_token: (kind === 'design' ? 's4dr1_' : 's4sr1_') + 'a'.repeat(64),
    chapter_ordinals: [3],
    disposition: 'review_required',
    current_decision_sha256: null,
    groups: GROUPS.map((group) => ({
      group,
      scanned: '已扫描 ' + group,
      items: group === 'blocking' ? [{
        code: 'entry_anchor_missing',
        rule: 'entry_anchor_missing',
        sev: 'hard',
        status: 'hit',
        chapter_ordinal: 3,
        scene_refs: ['c03s01'],
        refs: ['alias-1'],
        repair_owner: 'human',
        detail: '入口锚点没有绑定到关闭目录。',
        effect: kind === 'design' ? 'block_b' : 'block_context',
        allowed_actions: [kind === 'design' ? 'request_repair' : 'request_replan'],
        changed: false,
        historical_accept_reusable: false,
        source_decision_sha256: null,
      }] : group === 'disclosure' ? [{
        code: 'stage_signature_conflict',
        rule: 'stage_signature_conflict',
        sev: 'info',
        status: 'hit',
        chapter_ordinal: 3,
        scene_refs: ['c03s01'],
        refs: ['alias-1'],
        repair_owner: null,
        detail: '同一材料存在多个时空签名，完整披露给人审。',
        effect: 'disclose',
        allowed_actions: [],
        changed: false,
        historical_accept_reusable: false,
        source_decision_sha256: null,
      }] : group === 'coverage' ? [{
        code: 'chapter_coverage',
        rule: null,
        sev: null,
        status: null,
        chapter_ordinal: 3,
        scene_refs: [],
        refs: [],
        repair_owner: null,
        detail: '当前主体的第 3 章需要重新审阅。',
        effect: 'coverage',
        allowed_actions: ['accept'],
        changed: true,
        historical_accept_reusable: false,
        source_decision_sha256: null,
        reading: chapterReading(kind),
      }] : [],
    })),
    reading: {
      kind: 'stage4_workpack_reading_v1',
      legend: { reading: '每个 coverage 行的 reading 是本章裁决材料' },
      reconstruction_strategy: kind === 'design' ? '按事实义务重新组织场景' : null,
      chapter_weights: null,
      global_binding_concerns: [],
    },
  }
}

afterEach(() => vi.restoreAllMocks())

describe('Stage4 exception-first 审阅工作包', () => {
  it('按服务端固定顺序展示 A 的六组、扫描口径与 typed 入口，但不生成动作按钮', async () => {
    vi.spyOn(api, 'getDesignApprovalWorkpack').mockResolvedValue(workpack('design'))
    renderRoute(<ApprovalWorkpackPanel kind="design" />, '/n/book-1/stage4/3')

    expect(await screen.findByRole('region', { name: 'Stage4A 候选工作包' })).toBeVisible()
    expect(screen.getAllByRole('region').slice(1).map((node) => node.getAttribute('aria-label')))
      .toEqual(['阻断项', '语义关注', '完整披露', '只读指标', '后续阶段判定', '覆盖清单'])
    expect(screen.getByText('已扫描 disclosure')).toBeVisible()
    expect(screen.getByText('stage_signature_conflict')).toBeVisible()
    expect(screen.getByText('披露保留 · disclose')).toBeVisible()
    expect(screen.getAllByText('scene_refs：c03s01')).toHaveLength(2)
    expect(screen.getAllByText('refs：alias-1')).toHaveLength(2)
    expect(screen.getByText('服务端允许入口：request_repair')).toBeVisible()
    expect(screen.getAllByText('服务端允许入口：（无；只读/披露项不造动作）')).toHaveLength(1)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getAllByText(/不把空数组翻译成“全部通过”/)).toHaveLength(3)
  })

  it('B hook 只调用结构工作包 GET，并绑定 sid 的独立 query key', async () => {
    const design = vi.spyOn(api, 'getDesignApprovalWorkpack')
    const structure = vi.spyOn(api, 'getStructureApprovalWorkpack')
      .mockResolvedValue(workpack('structure'))
    renderRoute(<ApprovalWorkpackPanel kind="structure" />, '/n/book-1/stage4/3')

    expect(await screen.findByRole('region', { name: 'Stage4B 结构工作包' })).toBeVisible()
    expect(screen.getByText('阻断 Context · block_context')).toBeVisible()
    expect(structure).toHaveBeenCalledWith('book-1')
    expect(design).not.toHaveBeenCalled()
  })

  it('明确呈现同一当前工作包已批准及其决定摘要', async () => {
    vi.spyOn(api, 'getDesignApprovalWorkpack').mockResolvedValue({
      ...workpack('design'),
      disposition: 'already_current',
      current_decision_sha256: '9'.repeat(64),
    })
    renderRoute(<ApprovalWorkpackPanel kind="design" />, '/n/book-1/stage4/3')

    expect(await screen.findByText(/同一当前工作包已批准/)).toBeVisible()
    expect(screen.getByText('当前决定 SHA-256')).toBeVisible()
    expect(screen.getByText('9'.repeat(64))).toBeVisible()
  })

  it('明确呈现 frozen-v6 raw 决定被暂停且无需重复整读', async () => {
    vi.spyOn(api, 'getDesignApprovalWorkpack').mockResolvedValue({
      ...workpack('design'),
      disposition: 'suspended',
      current_decision_sha256: '7'.repeat(64),
    })
    renderRoute(<ApprovalWorkpackPanel kind="design" />, '/n/book-1/stage4/3')

    expect(await screen.findByText(/raw 审阅已记录/)).toBeVisible()
    expect(screen.getByText(/暂停下游授权且无需重复整读/)).toBeVisible()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('coverage 行的逐章裁决材料与顶层 legend 只作折叠呈现，不生成按钮', async () => {
    vi.spyOn(api, 'getStructureApprovalWorkpack').mockResolvedValue(workpack('structure'))
    renderRoute(<ApprovalWorkpackPanel kind="structure" />, '/n/book-1/stage4/3')

    expect(await screen.findByText('逐章裁决材料（reading）')).toBeInTheDocument()
    expect(screen.getByText('字段说明（reading.legend）与整书材料')).toBeInTheDocument()
    expect(screen.getAllByText(/她发现门已经打开/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/门口/).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('真实 API 缝使用两条只读 workpack 路由', async () => {
    const response = workpack('design')
    const send = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(
      JSON.stringify(response),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ))

    await realApi.getDesignApprovalWorkpack('book/one')
    await realApi.getStructureApprovalWorkpack('book/one')

    expect(send.mock.calls.map(([input]) => String(input))).toEqual([
      '/api/novels/book%2Fone/approvals/design/workpack',
      '/api/novels/book%2Fone/approvals/structure/workpack',
    ])
    expect(send.mock.calls.every(([, init]) => (init?.method ?? 'GET') === 'GET')).toBe(true)
  })
})
