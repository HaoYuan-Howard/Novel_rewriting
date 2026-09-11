/**
 * 闸门 —— 流程的核心，不是装饰。
 *
 *   stage0 剔番外 ─► stage1 切分 ─► stage2 global ─► blueprint
 *                                      └─ 人工复核 ─┘ ─► stage4 计划
 *                   ─► Stage5 写作授权 ─► stage5 ─► stage6 ─► stage7
 *
 * GateBar 只展示仍使用通用确认的页面；Stage6/7 已由专用 typed 审批面负责。
 * 审批、后续作业与最终交付分别读取后端证明，不能从 gate 推导成书或自动发起状态。
 */
import type { ApprovalDoc, GateKey } from '@/types/approvals'
import type { ScreenName } from './vocabulary'

export interface GateChipView {
  ok: boolean
  label: string
}

export interface GateView {
  key: GateKey
  done: boolean
  /** 前置没满足时按钮禁用。锁必须是真的。 */
  blocked: boolean
  title: string
  note: string
  chips: GateChipView[]
  btn: string
  /** 禁用时说明缺什么 */
  blockedReason: string
  doneNote: string | null
}

/** 屏 → 它守着哪一道闸门 */
export const SCREEN_GATE: Partial<Record<ScreenName, GateKey>> = {
  stage1: 's1',
  read: 's2',
  global: 's2',
  stage4: 's4',
  stage5: 's5',
}

export function resolveGate(screen: ScreenName, doc: ApprovalDoc): GateView | null {
  const g = doc.gate
  const key = SCREEN_GATE[screen]
  if (!key) return null
  const done = g[key]

  switch (key) {
    case 's1':
      return {
        key,
        done,
        blocked: !g.s0,
        title: done ? '切分已确认' : g.s0 ? '这一刀切对了吗？' : '番外还没剔，切了也要重切',
        note: '确认后先生成全局设定；当前 Global 接受后，服务端才会下发逐章骨架生成。',
        chips: [{ ok: g.s0, label: 'stage0 剔番外' }],
        btn: done ? '已确认' : g.s0 ? '确认切分' : '先过 stage0 剔除番外',
        blockedReason: 'stage0 还没确认删除范围。番外没剔就切分，切完还要重切。',
        doneNote: done
          ? 'stage2 已发起；若服务端仍下发 continuation，可继续生成下一份正式产物。'
          : null,
      }

    case 's2': {
      const globalAccepted = doc.stage2_reviews?.['stage2.global']?.state === 'accepted'
      const blueprintAccepted = doc.stage2_reviews?.['stage2.blueprint']?.state === 'accepted'
      const both = globalAccepted && blueprintAccepted
      return {
        key,
        done,
        blocked: !both,
        title: done ? 'stage2 已确认' : 'stage2 两个节点都须接受',
        note: '先接受当前全局设定，再生成并独立审阅整本骨架。两份当前节点接受齐全后，才可继续 stage4；已读记录不能替代接受。',
        chips: [
          { ok: globalAccepted, label: 'Global 接受' },
          { ok: blueprintAccepted, label: 'Blueprint 接受' },
        ],
        btn: done ? '已确认' : both ? '确认 stage2，启动 stage4' : '两个节点都接受后才能继续',
        blockedReason: '服务端尚未确认两份当前节点接受；请在全局设定与骨架页分别提交审阅决定。',
        doneNote: done ? '两份节点接受已保存；下一动作由后端运行入口决定。' : null,
      }
    }

    case 's4':
      return {
        key,
        done,
        blocked: false,
        title: done ? '计划已确认' : '这份重构计划能写吗？',
        note: '逐章接受当前计划后，可以授权 Stage5 全书闭卷写作；机械风格从 Stage1 即时计算。',
        chips: [],
        btn: done ? '已确认' : '确认计划',
        blockedReason: '',
        doneNote: done ? '计划已确认 → 可以授权 Stage5 全书闭卷写作。' : null,
      }

    case 's5': {
      const ready = g.s4 && doc.writing
      return {
        key,
        done,
        blocked: !ready,
        title: done ? '成稿已确认' : ready ? '这些章可以定稿了吗？' : '上游还没齐，先别定稿',
        note: ready
          ? '判据在左、成稿在右，改完再确认。确认后发起 stage6 开篇文案。'
          : '成稿必须建立在当前已确认的 Stage4 计划与全书写作授权上。',
        chips: [
          { ok: g.s4, label: 'stage4 计划' },
          { ok: doc.writing, label: doc.writing ? '全书写作已授权' : '全书写作未授权' },
        ],
        btn: done ? '已确认' : ready ? '确认成稿，启动 stage6' : '等计划确认与写作授权',
        blockedReason: '当前计划或全书写作授权尚未成立。',
        doneNote: done ? 'stage6 已发起。' : null,
      }
    }

    default:
      return null
  }
}

/**
 * 左导航的锁。上游没确认时下游禁用 ——
 * 「导航上写着锁、屏上却能点」是评审揪出来的真实缺陷，别复现。
 */
export const RAIL_LOCK: Partial<Record<ScreenName, { need: GateKey; why: string }>> = {
  stage1: { need: 's0', why: '先过 stage0：番外还没剔，切了也要重切' },
  stage6: { need: 's5', why: '先确认 stage5 成稿：文案是给成稿写的招牌' },
  stage7: { need: 's6', why: '先确认 stage6 文案：不然拼出来的是半本' },
}

/** gate key → 一句人话。左导航锁定项右侧的小字用它，别在屏里散落硬编码。 */
export const GATE_LABEL: Record<GateKey, string> = {
  s0: 'stage0 剔除番外',
  s1: 'stage1 切分确认',
  s2: 'stage2 两屏审阅',
  s4: 'stage4 计划确认',
  s5: 'stage5 成稿确认',
  s6: 'stage6 文案确认',
  s7: 'stage7 人名定稿',
}

/** 锁定项右侧的常驻小字：「先过 stage0 剔除番外」 */
export function gateNeedText(key: GateKey): string {
  return '先过 ' + GATE_LABEL[key]
}

/** 左导航上打 ✓ 的依据 */
export const RAIL_GATE: Partial<Record<ScreenName, GateKey>> = {
  stage0: 's0',
  stage1: 's1',
  read: 's2',
  global: 's2',
  stage4: 's4',
  stage5: 's5',
  stage6: 's6',
  stage7: 's7',
}
