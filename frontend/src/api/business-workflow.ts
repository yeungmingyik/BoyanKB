import { get, post, getDown } from '@/utils/request'

export interface DocumentIssue {
  location: string
  problem: string
  severity: 'blocking' | 'suggestion'
  suggestion: string
}

export interface DocumentJob {
  id: string
  session_id: string
  state: string
  outline_hash: string
  outline_markdown: string
  format: string
  filename?: string
  failure_code?: string
  review?: { passed: boolean; issues: DocumentIssue[] }
}

const base = (session: string) => `/api/v1/sessions/${encodeURIComponent(session)}/documents`

export const listDocumentJobs = (session: string) => get<{ success: boolean; data: DocumentJob[] }>(base(session))

export const confirmDocumentJob = (session: string, job: DocumentJob) => post(`${base(session)}/${encodeURIComponent(job.id)}/confirm`, { outline_hash: job.outline_hash })

export const generateDocumentJob = (session: string, job: DocumentJob) => post(`${base(session)}/${encodeURIComponent(job.id)}/generate`, { outline_hash: job.outline_hash }, { timeout: 200000 })

export const downloadDocumentJob = (session: string, job: DocumentJob) => getDown(`${base(session)}/${encodeURIComponent(job.id)}/download`)
