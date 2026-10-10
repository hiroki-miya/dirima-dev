import { z } from 'zod'

export const Case = z.object({ id: z.string(), project_id: z.string(), task_id: z.string(),
  title: z.string(), question: z.string(), reason_human_required: z.string(),
  risk_level: z.enum(['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']), reversibility: z.string(),
  attention_class: z.string(), status: z.string(), created_at: z.string(),
  affected_task: z.string().optional(), option_labels: z.array(z.string()).optional(), evidence_summary: z.string().optional() })
export type Case = z.infer<typeof Case>
export const Option = z.object({ id: z.string(), label: z.string(), description: z.string(),
  supporting_evidence: z.array(z.string()), opposing_evidence: z.array(z.string()),
  downstream_impact: z.string(), reversibility: z.string() })
export type Option = z.infer<typeof Option>
export const Evidence = z.object({ id: z.string(), type: z.string(), source_ref: z.string(), content: z.string(), run_id: z.string().nullable() })
export const Finding = z.object({ id: z.string(), subject: z.string(), claim: z.string(), severity: z.string(), run_id: z.string(), evidence: z.array(Evidence) })
export const CaseDetail = Case.extend({ options: z.array(Option), conflict: z.unknown().nullable(),
  findings: z.array(Finding), affected_task: z.object({ id: z.string(), title: z.string() }) })
export type CaseDetail = z.infer<typeof CaseDetail>
export const Project = z.object({ id: z.string(), name: z.string(), description: z.string() })
export const Run = z.object({ id: z.string(), agent_name: z.string(), model_family: z.string(), status: z.string(), created_at: z.string() })
export const Task = z.object({ id: z.string(), project_id: z.string(), title: z.string(), description: z.string(), status: z.string(), runs: z.array(Run), findings: z.array(z.object({ id: z.string(), subject: z.string(), claim: z.string(), severity: z.string() })) })
export const Attention = z.object({ id: z.string(), title: z.string(), detail: z.string(), classification: z.string(), kind: z.string() })

export function setToken(value: string) { sessionStorage.setItem('dirima-token', value) }
export function getToken() { return sessionStorage.getItem('dirima-token') || '' }

export async function api<T>(path: string, schema: z.ZodType<T>, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json',
    Authorization: `Bearer ${getToken()}`, ...init?.headers } })
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`)
  return schema.parse(await response.json())
}
