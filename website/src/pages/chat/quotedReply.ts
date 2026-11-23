import type { Nodes as MdastNodes, Root as MdastRoot } from 'mdast'
import remarkParse from 'remark-parse'
import { unified } from 'unified'
import { REMARK_BASE_PLUGINS } from '../../components/markdown/remarkBase'
import type { ChatMessage } from '../../types'
import { capWhitespaceRuns } from '../../utils/markdownDepthBound'

/**
 * Where a selection quote came from. Quote on selected reply text puts the words
 * into the draft as `> ` lines (`chat-core/composer/quoteDraft.ts`), which name
 * no message, so the sent quote finds its source by its words: the nearest
 * earlier reply that holds them. The quote is text the browser rendered, so the
 * reply is compared as rendered text too, read from its parsed markdown, and
 * both sides keep letters and digits only: emphasis, list numbering, line
 * breaks and link destinations cannot stop a match.
 */

const NOT_A_WORD_CHAR = /[^\p{L}\p{N}]+/gu

function wordChars(text: string): string {
  return text.replace(NOT_A_WORD_CHAR, '')
}

const parser = unified().use(remarkParse).use(REMARK_BASE_PLUGINS)

/** The text a markdown node renders: raw HTML, image alts and link definitions draw none. */
function renderedText(node: MdastNodes): string {
  if (node.type === 'text' || node.type === 'inlineCode' || node.type === 'code') return node.value
  return 'children' in node ? (node.children as MdastNodes[]).map(renderedText).join('') : ''
}

/** Per row object, which the store keeps by reference while it is unchanged. */
const replyWordsCache = new WeakMap<ChatMessage, string>()

function replyWords(m: ChatMessage): string {
  let words = replyWordsCache.get(m)
  if (words === undefined) {
    words = wordChars(renderedText(parser.runSync(parser.parse(capWhitespaceRuns(m.content))) as MdastRoot))
    replyWordsCache.set(m, words)
  }
  return words
}

/** The reply a selection quote sent in `messages[fromIdx]` was taken from, or null. */
export function findQuotedReply(messages: ChatMessage[], quoted: string, fromIdx: number): ChatMessage | null {
  const words = wordChars(quoted)
  if (!words) return null
  for (let i = fromIdx - 1; i >= 0; i--) {
    const m = messages[i]
    if (m.role === 'assistant' && m.ts && replyWords(m).includes(words)) return m
  }
  return null
}

/** The span of `quoted`'s words in `root`'s text, across element boundaries, or
 *  null. Chrome marked `data-message-search-skip` is not message text. */
export function quotedTextRange(root: HTMLElement, quoted: string): Range | null {
  const words = wordChars(quoted)
  if (!words) return null
  let text = ''
  const at: Array<[Text, number]> = []
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: n => (n.parentElement?.closest('[data-message-search-skip]') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  })
  for (let n = walker.nextNode() as Text | null; n; n = walker.nextNode() as Text | null) {
    for (const m of n.data.matchAll(/[\p{L}\p{N}]/gu)) {
      text += m[0]
      for (let k = 0; k < m[0].length; k++) at.push([n, m.index + k])
    }
  }
  const start = text.indexOf(words)
  if (start < 0) return null
  const [first, firstOffset] = at[start]
  const [last, lastOffset] = at[start + words.length - 1]
  const range = document.createRange()
  range.setStart(first, firstOffset)
  range.setEnd(last, lastOffset + 1)
  return range
}

/**
 * The ref of the row a quote jump lands on: marks the quote's words in the
 * row's bubble while it is attached, and unmarks them when React detaches it.
 * The mark is a CSS Custom Highlight (`::highlight(mc-quote-mark)`), so no node
 * React owns is split. Where the API is absent, or the words are not in the
 * bubble's text, nothing is marked and the row's ring alone shows the landing.
 */
export function quoteMarkRef(quoted: string): (el: HTMLElement | null) => void {
  return el => {
    if (typeof Highlight === 'undefined' || typeof CSS === 'undefined' || !CSS.highlights) return
    const range = el && quotedTextRange(el.querySelector<HTMLElement>('.message-bubble') ?? el, quoted)
    if (range) CSS.highlights.set('mc-quote-mark', new Highlight(range))
    else CSS.highlights.delete('mc-quote-mark')
  }
}
