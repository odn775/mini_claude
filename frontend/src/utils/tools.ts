/** 工具调用的界面呈现：中文名映射 + 编程类工具不在医疗界面展示。 */

const LABELS: Record<string, string> = {
  search_knowledge: '检索医学知识库',
  run_skill: '调用专科规则',
  check_health: '系统自检',
  recommend_department: '生成分诊建议',
}

/** 这些工具在医疗场景里不展示（含分诊工具本身——它的结果已渲染为分诊卡）。 */
const HIDDEN = new Set([
  'read_file',
  'write_file',
  'run_bash',
  'glob_search',
  'grep_search',
  'recommend_department',
])

export function toolLabel(name: string): string {
  return LABELS[name] ?? name
}

export function isHiddenTool(name: string): boolean {
  return HIDDEN.has(name)
}
