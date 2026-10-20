/** Markdown 渲染：与旧版一致的配置（不开 html、linkify、代码高亮）。 */

import hljs from 'highlight.js'
import MarkdownIt from 'markdown-it'

import 'highlight.js/styles/github.css'

function escapeHtml(text: string): string {
  const map: Record<string, string> = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }
  return text.replace(/[&<>"']/g, (c) => map[c] as string)
}

const md = new MarkdownIt({
  html: false,
  linkify: true,
  highlight(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return `<pre><code class="hljs">${
          hljs.highlight(code, { language: lang, ignoreIllegals: true }).value
        }</code></pre>`
      } catch {
        /* 高亮失败时退回纯转义 */
      }
    }
    return `<pre><code class="hljs">${escapeHtml(code)}</code></pre>`
  },
})

export function renderMarkdown(text: string): string {
  return md.render(text || '')
}
