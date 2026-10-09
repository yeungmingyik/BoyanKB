<template>
  <section v-if="jobs.length" class="business-documents" aria-label="博研文化正式文件">
    <article v-for="job in jobs" :key="job.id" class="document-job" :data-document-state="job.state">
      <div class="document-heading">
        <strong>博研文化 · {{ job.format.toUpperCase() }}</strong>
        <span role="status">{{ states[job.state] || job.state }}</span>
      </div>
      <details :open="job.state === 'awaiting_confirmation'">
        <summary>查看待确认大纲</summary>
        <pre class="document-outline">{{ job.outline_markdown }}</pre>
      </details>
      <p v-if="job.state === 'awaiting_confirmation'">确认此版大纲后生成文件，并自动交由内容审校官检查。修改大纲请继续对话，修改后需要重新确认。</p>
      <p v-if="job.state === 'blocked'">审校发现阻断问题，文件尚不可交付。请根据下方建议修改大纲后重新确认。</p>
      <p v-if="job.state === 'failed'">生成或审校未完成，文件尚不可交付。请重新提出方案；已有确认不会自动用于新版。</p>
      <div v-if="job.review?.issues.length" class="review-table-wrapper">
        <table class="review-table">
          <thead><tr><th>位置</th><th>问题</th><th>严重程度</th><th>修改建议</th></tr></thead>
          <tbody><tr v-for="(issue, index) in job.review.issues" :key="index"><td>{{ issue.location }}</td><td>{{ issue.problem }}</td><td>{{ issue.severity === 'blocking' ? '阻断' : '建议' }}</td><td>{{ issue.suggestion }}</td></tr></tbody>
        </table>
      </div>
      <div class="document-actions">
        <t-button v-if="job.state === 'awaiting_confirmation'" :loading="pending === job.id" :disabled="!!pending || busy" @click="confirmAndGenerate(job)">确认大纲并生成</t-button>
        <t-button v-else-if="job.state === 'approved'" :loading="pending === job.id" :disabled="!!pending || busy" @click="generate(job)">生成已确认方案</t-button>
        <t-button v-else-if="job.state === 'published'" :loading="pending === job.id" :disabled="!!pending" @click="download(job)">下载已审校文件</t-button>
      </div>
    </article>
    <p v-if="error" class="document-error" role="alert">{{ error }}</p>
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { confirmDocumentJob, downloadDocumentJob, generateDocumentJob, listDocumentJobs, type DocumentJob } from '@/api/business-workflow'

const props = defineProps<{ sessionId: string; busy: boolean }>()
const jobs = ref<DocumentJob[]>([])
const pending = ref('')
const error = ref('')
const states: Record<string, string> = {
  awaiting_confirmation: '等待确认大纲', approved: '大纲已确认', generating: '正在生成', reviewing: '内容审校中', published: '审校通过', blocked: '审校阻断', failed: '未完成', superseded: '已被新版替代',
}
let timer: ReturnType<typeof setInterval> | undefined
let disposed = false

async function refresh() {
  const session = props.sessionId
  if (!session) return
  try {
    const result = await listDocumentJobs(session)
    if (!disposed && session === props.sessionId && result.success) jobs.value = result.data.filter(job => job.state !== 'superseded').slice(0, 5)
  } catch {
    if (!disposed && session === props.sessionId && jobs.value.length) error.value = '暂时无法读取文件任务，请稍后重试。'
  }
}

async function confirmAndGenerate(job: DocumentJob) {
  const session = props.sessionId
  pending.value = job.id
  error.value = ''
  try {
    await confirmDocumentJob(session, job)
    await refresh()
    await generateDocumentJob(session, job)
  } catch {
    error.value = '当前版本未能完成确认或生成。请检查任务状态，来源或大纲变化后需要重新确认。'
  } finally {
    pending.value = ''
    await refresh()
  }
}

async function generate(job: DocumentJob) {
  const session = props.sessionId
  pending.value = job.id
  error.value = ''
  try { await generateDocumentJob(session, job) } catch { error.value = '生成或审校未完成，文件未发布。' } finally { pending.value = ''; await refresh() }
}

async function download(job: DocumentJob) {
  pending.value = job.id
  error.value = ''
  try {
    const data = await downloadDocumentJob(props.sessionId, job)
    const url = URL.createObjectURL(data)
    const link = document.createElement('a')
    link.href = url
    link.download = job.filename || `博研文化.${job.format}`
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch { error.value = '文件不可下载，请检查审校状态和知识库访问权限。' } finally { pending.value = '' }
}

watch(() => props.sessionId, () => { jobs.value = []; error.value = ''; void refresh() })
watch(() => props.busy, () => { void refresh() })
onMounted(() => {
  void refresh()
  timer = setInterval(() => {
    if (props.busy || pending.value || jobs.value.some(job => ['generating', 'reviewing'].includes(job.state))) void refresh()
  }, 4000)
})
onBeforeUnmount(() => { disposed = true; if (timer) clearInterval(timer) })
</script>

<style scoped>
.business-documents { display: grid; gap: 16px; margin: 20px 0; color: var(--td-text-color-primary); }
.document-job { padding: 20px; border: 1px solid var(--td-component-border); border-radius: 12px; background: var(--td-bg-color-container); }
.document-heading { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 14px; }
.document-heading span { color: var(--td-text-color-secondary); }
.document-outline { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; line-height: 1.65; }
.document-job p { line-height: 1.65; color: var(--td-text-color-secondary); }
.document-actions { margin-top: 16px; }
.review-table-wrapper { overflow-x: auto; }
.review-table { border-collapse: collapse; width: 100%; text-align: left; }
.review-table th, .review-table td { padding: 10px; border: 1px solid var(--td-component-border); vertical-align: top; }
.document-error { color: var(--td-error-color); }
</style>
