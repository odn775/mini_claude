/** 导诊相关的界面规则：红旗症状识别与紧急程度展示。
 *
 * 关键词与 backend/app/core/triage_rules.py 保持一致。
 * 前端本地判定是为了「输入即提示」——不等网络、不等模型返回。
 */

import type { Urgency } from '@/api/types'

const RED_FLAGS: Record<string, string[]> = {
  胸痛或压榨性胸闷: ['胸痛', '胸口疼', '胸口痛', '压榨', '胸口压迫', '心前区痛'],
  呼吸困难: ['呼吸困难', '喘不上气', '透不过气', '不能呼吸', '窒息'],
  意识障碍或抽搐: ['意识不清', '昏迷', '叫不醒', '抽搐', '晕倒', '晕厥', '意识丧失'],
  大出血: ['大出血', '大量出血', '呕血', '咯血', '便血不止', '血流不止'],
  剧烈头痛: ['剧烈头痛', '爆炸样头痛', '头痛欲裂'],
  疑似中毒: ['中毒', '误服', '农药', '过量服药'],
  严重过敏: ['过敏性休克', '喉头水肿', '全身荨麻疹伴憋气'],
  自伤风险: ['自杀', '想死', '轻生', '不想活'],
  孕产急症: ['孕晚期出血', '胎动消失', '临产大出血'],
  儿童危重: ['高热惊厥', '婴儿抽搐', '孩子叫不醒'],
}

export function detectRedFlags(text: string): string[] {
  if (!text) return []
  return Object.entries(RED_FLAGS)
    .filter(([, words]) => words.some((w) => text.includes(w)))
    .map(([label]) => label)
}

export const URGENCY_META: Record<Urgency, { label: string }> = {
  routine: { label: '普通门诊' },
  soon: { label: '尽快就诊' },
  emergency: { label: '立即急诊' },
}
