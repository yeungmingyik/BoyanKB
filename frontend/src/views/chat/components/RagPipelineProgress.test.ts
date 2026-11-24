import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import { compileScript, parse } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as vue from 'vue'
import * as history from '../../../utils/rag-pipeline-history.ts'
import * as state from '../../../utils/rag-pipeline-state.ts'
import * as icons from '../../../utils/agent-tool-icons.ts'
import * as display from '../../../utils/agent-tool-display.ts'
import * as attachments from '../../../utils/attachmentParsingDisplay.ts'
import * as references from '../../../utils/referenceSources.ts'
import zhCN from '../../../i18n/locales/zh-CN.ts'

const source = readFileSync(new URL('./RagPipelineProgress.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const compiled = ts.transpileModule(compileScript(descriptor, {
  id: 'rag-error-test', inlineTemplate: true,
}).content, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText

type Host = { type: string; props: Record<string, any>; children: Host[]; text: string; parent?: Host; querySelectorAll: () => [] }
const node = (type: string): Host => ({ type, props: {}, children: [], text: '', querySelectorAll: () => [] })
const renderer = vue.createRenderer<Host, Host>({
  createElement: node,
  createText: text => ({ ...node('#text'), text }),
  createComment: text => ({ ...node('#comment'), text }),
  patchProp: (el, key, _old, value) => { el.props[key] = value },
  setText: (el, text) => { el.text = text },
  setElementText: (el, text) => { el.children = []; el.text = text },
  parentNode: el => el.parent || null,
  nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1] || null,
  insert(el, parent, anchor) {
    if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1)
    el.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    parent.children.splice(index < 0 ? parent.children.length : index, 0, el)
  },
  remove(el) { el.parent?.children.splice(el.parent.children.indexOf(el), 1); el.parent = undefined },
})
const textOf = (el: Host): string => el.type === '#comment' ? '' : el.text + el.children.map(textOf).join('')
const all = (el: Host, predicate: (node: Host) => boolean): Host[] => [
  ...(predicate(el) ? [el] : []), ...el.children.flatMap(child => all(child, predicate)),
]
const translate = (key: string) => key.split('.').reduce<any>((value, part) => value?.[part], zhCN) || key

function fixture(initial: Record<string, unknown> = {}, memoryOnly = false) {
  const session = vue.reactive<Record<string, any>>({ is_completed: false, agentEventStream: [], ...initial })
  const exports: { default?: vue.Component } = {}
  const modules: Record<string, unknown> = {
    vue,
    'vue-i18n': { useI18n: () => ({ t: translate }) },
    '@/utils/rag-pipeline-history': history,
    '@/utils/rag-pipeline-state': state,
    '@/utils/agent-tool-icons': icons,
    '@/utils/agent-tool-display': display,
    '@/utils/attachmentParsingDisplay': attachments,
    '@/utils/referenceSources': references,
    '@/composables/useChatReferencesDrawer': { useChatReferencesDrawer: () => null },
    '@/composables/useChatMemoryRow': { useChatMemoryRow: () => ({
      memoryItems: vue.ref([]), hasMemory: vue.ref(false), expanded: vue.ref(false), forgettingId: vue.ref(''),
      toggle() {}, forget() {},
    }) },
    './ChatMemoryStep.vue': { __esModule: true, default: vue.defineComponent({ render: () => null }) },
  }
  runInNewContext(compiled, { exports, console, require(name: string) {
    assert.ok(name in modules, `unexpected import ${name}`)
    return modules[name]
  } })
  const app = renderer.createApp({ setup: () => () => vue.h(exports.default!, { session, memoryOnly }) })
  app.component('t-icon', vue.defineComponent({ setup: () => () => vue.h('icon') }))
  app.component('t-alert', vue.defineComponent({
    props: ['title', 'message'],
    setup: props => () => vue.h('aside', { role: 'alert' }, [props.title, props.message]),
  }))
  const root = node('root')
  app.mount(root)
  return { session, root, close: () => app.unmount(), settle: async () => { await vue.nextTick(); await vue.nextTick() } }
}

for (const [code, text] of [
  ['model_timeout', zhCN.chat.quickAnswerError.modelTimeout],
  ['model_unavailable', zhCN.chat.quickAnswerError.modelUnavailable],
  ['model_stream_interrupted', zhCN.chat.quickAnswerError.modelStreamInterrupted],
  ['model_request_failed', zhCN.chat.quickAnswerError.modelRequestFailed],
  ['<img src=x onerror=alert(1)>', zhCN.chat.quickAnswerError.modelRequestFailed],
]) {
  test(`${code}: live failure replaces preparing with a safe persistent error`, async t => {
    const f = fixture(); t.after(f.close)
    assert.ok(textOf(f.root).includes(zhCN.chat.preparingAnswer))
    f.session.knowledge_qa_error = code
    f.session.is_completed = true
    await f.settle()
    const alerts = all(f.root, el => el.props.role === 'alert')
    assert.equal(alerts.length, 1)
    assert.ok(textOf(alerts[0]).includes(text))
    assert.ok(!textOf(f.root).includes(zhCN.chat.preparingAnswer))
    assert.equal(all(f.root, el => el.type === 'img').length, 0)
    assert.equal(all(f.root, el => String(el.props.class).includes('action-pending')).length, 0)
  })
}

test('restored failure retains reasoning and removes pending step animations and success row', async t => {
  const f = fixture({
    is_completed: true, knowledge_qa_error: 'model_timeout', content: 'Partial answer',
    agentEventStream: [
      { type: 'tool_call', tool_name: 'knowledge_search', pending: false, success: true },
      { type: 'thinking', content: 'Existing reasoning', done: true },
    ],
  }); t.after(f.close)
  const expand = all(f.root, el => el.type === 'button')[0]
  assert.ok(expand)
  expand.props.onClick()
  await f.settle()
  assert.ok(textOf(f.root).includes('Existing reasoning'))
  assert.ok(textOf(f.root).includes(zhCN.chat.quickAnswerError.modelTimeout))
  assert.equal(all(f.root, el => String(el.props.class).includes('action-pending')).length, 0)
  assert.equal(all(f.root, el => String(el.props.class).includes('is-running')).length, 0)
  assert.equal(all(f.root, el => String(el.props.class).includes('agent-step-done')).length, 0)
  assert.equal(f.session.content, 'Partial answer')
})

test('successful and user-stopped turns do not display a model error or wait placeholder', t => {
  for (const event of [{ type: 'answer', content: 'Answer', done: true }, { type: 'stop', reason: 'user_requested' }]) {
    const f = fixture({ is_completed: true, agentEventStream: [event] }); t.after(f.close)
    assert.equal(all(f.root, el => el.props.role === 'alert').length, 0)
    assert.ok(!textOf(f.root).includes(zhCN.chat.preparingAnswer))
  }
})

test('memory-only hosts do not repeat a quick-answer failure', t => {
  const f = fixture({ is_completed: true, knowledge_qa_error: 'model_timeout' }, true); t.after(f.close)
  assert.equal(all(f.root, el => el.props.role === 'alert').length, 0)
})
