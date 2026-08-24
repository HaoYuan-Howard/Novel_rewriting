import { describe, expect, it } from 'vitest'
import { qk } from './queryKeys'
import { adaptNovel, splitLog } from './endpoints'
import { projectGlobalSpoilers, SPOILER_PLACEHOLDER } from './spoilers'
import type { GlobalFactEvidence, Stage2Global } from '@/types/stages'

function evidence(text: string): GlobalFactEvidence {
  return {
    chapter_ordinal: 2,
    source: { text, start: 3, end: 3 + text.length, normalized: text, recovery: 'exact' },
  }
}

function globalFixture(): Stage2Global {
  return {
    sid: 's',
    title: 't',
    setting: {
      时代背景: '古代',
      era_evidence: evidence('古代证据'),
      世界观: '现实世界',
      worldview_evidence: evidence('世界观证据'),
    },
    characters: [
      {
        char_id: 'c1',
        canonical: '甲',
        name_distinctiveness: 'high',
        role: '主角',
        role_evidence: evidence('主角证据'),
        fate: '真实 fate',
        fate_evidence: evidence('真实 fate 证据'),
        address: [],
      },
    ],
    relations: [],
    world_rules: [],
    secrets: [
      {
        secret_id: 'secret-1',
        fact: '真实秘密',
        fact_evidence: evidence('真实秘密证据'),
        knowledge_events: [],
        group_id: null,
        setup_kind: 'foreshadowed',
        setup_chapters: [1],
        first_reveal_chapter: 4,
        reader_reveal_chapters: [4],
      },
    ],
    arc: {
      conflict_stages: [],
      climax: '真实高潮',
      ending: '真实结局',
      arc_tracks: [
        {
          subject: { raw: 'c1', display: '甲', resolved: true },
          dimension: '处境',
          change_type: 'development',
          trajectory: '真实弧线',
          trajectory_evidence: evidence('真实弧线证据'),
        },
      ],
    },
    unresolved_refs: 0,
    invalid_exists: false,
    freshness: 'fresh',
    reason: '',
    ghosts: [],
    _provenance: { root_clean_sha1: '0000000000000000000000000000000000000000' },
  }
}

describe('request-shaped cache and diagnostics', () => {
  it('includes runtime chapter args in preview cache identity', () => {
    const base = { kind: 'stage5.write', label: '写', extra: null, sid: 's' }
    expect(qk.jobPreview({ ...base, args: ['--chapters', '1'] })).not.toEqual(
      qk.jobPreview({ ...base, args: ['--chapters', '2'] }),
    )
  })

  it('takes the dynamic chapter count from the stage1-backed overview', () => {
    const adapted = adaptNovel({
      sid: 's',
      title: 't',
      dirname: 's',
      chapters: 11,
      stages: [],
      summary: {
        verdict: 'pending',
        text: '',
        detail: '',
        artifact_count: 0,
        latest_mtime: null,
      },
    })
    expect(adapted.chapters).toBe(11)
  })

  it('keeps null, malformed, unknown and error diagnostics expandable', () => {
    const lines = splitLog(
      [
        '@observe null',
        '@observe {bad',
        '@observe {"event":"new_event","chapter":7,"detail":"chapter detail"}',
        '@observe {"event":"llm_attempt_finished","state":"error","attempt":1,"max_attempts":1,"purpose":"judge","error_kind":"Timeout","detail":"wire failed"}',
      ].join('\n'),
    )
    expect(lines).toHaveLength(4)
    expect(lines[0]?.kind).toBe('malformed')
    expect(lines[1]?.details).toHaveProperty('raw_payload')
    expect(lines[2]?.text).toContain('new_event')
    expect(lines[2]?.details).toMatchObject({ chapter: 7, detail: 'chapter detail' })
    expect(lines[3]?.text).toContain('error_kind=Timeout')
    expect(lines[3]?.details).toMatchObject({ detail: 'wire failed' })
  })
})

describe('display-only spoiler projection', () => {
  it('projects all four configured paths before screens consume them', () => {
    const raw = globalFixture()
    const hidden = projectGlobalSpoilers(raw, false)
    expect(hidden.characters[0]?.fate).toBe(SPOILER_PLACEHOLDER)
    expect(hidden.characters[0]?.fate_evidence?.source?.text).toBe(SPOILER_PLACEHOLDER)
    expect(hidden.characters[0]?.fate_evidence?.source?.normalized).toBe(SPOILER_PLACEHOLDER)
    expect(hidden.secrets).toHaveLength(1)
    expect(hidden.secrets[0]?.fact).toBe(SPOILER_PLACEHOLDER)
    expect(hidden.arc.climax).toBe(SPOILER_PLACEHOLDER)
    expect(hidden.arc.ending).toBe(SPOILER_PLACEHOLDER)
    expect(hidden.arc.arc_tracks[0]?.trajectory_evidence.source?.text).toBe(
      SPOILER_PLACEHOLDER,
    )
    expect(JSON.stringify(hidden)).not.toContain('真实秘密')
    expect(JSON.stringify(hidden)).not.toContain('真实 fate 证据')
    expect(JSON.stringify(hidden)).not.toContain('真实弧线证据')
    expect(projectGlobalSpoilers(raw, true)).toBe(raw)
  })
})
