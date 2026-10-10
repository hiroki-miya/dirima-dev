import { expect, test, type APIRequestContext } from '@playwright/test'
import { randomUUID } from 'node:crypto'

const apiBase = process.env.DIRIMA_API_URL || 'http://127.0.0.1:8000'
const workspace = process.env.DIRIMA_WORKSPACE_ID || '00000000-0000-4000-8000-000000000001'
const token = process.env.DIRIMA_TOKEN || 'test-only-token'

async function call(request: APIRequestContext, method: 'get'|'post', path: string, data?: unknown) {
  const response = await request[method](`${apiBase}${path}`, { headers: { Authorization: `Bearer ${token}` }, data })
  expect(response.ok(), `${path}: ${await response.text()}`).toBeTruthy()
  return response.json()
}
async function task(request: APIRequestContext) {
  const project = await call(request, 'post', '/api/v1/projects', { name: `E2E-${randomUUID()}` })
  const task = await call(request, 'post', '/api/v1/tasks', { project_id: project.id, title: `Review-${randomUUID()}` })
  return { project, task }
}
function envelope(project: {id:string}, task: {id:string}, run: string, external: string, type: string, payload: unknown) {
  return { schema_version: '1.0', event_id: randomUUID(), workspace_id: workspace,
    project_id: project.id, task_id: task.id, run_id: run,
    source: { adapter: 'e2e', runtime: 'browser-test', external_run_id: external },
    type, occurred_at: new Date().toISOString(), idempotency_key: randomUUID(), payload }
}
async function addRun(request: APIRequestContext, project: {id:string}, task: {id:string}, name: string, family: string, finding: unknown) {
  const run = randomUUID(), external = randomUUID()
  for (const [type, payload] of [
    ['run.created', { agent_name: name, model_family: family }], ['run.started', {}],
    ['run.completed', { receipt: { schema_version: '1.0', status: 'succeeded', summary: 'Done',
      artifacts: [], reported_findings: [finding], reported_verifications: [], reported_risks: [], unresolved: [] } }]
  ] as const) await call(request, 'post', '/api/v1/events', envelope(project, task, run, external, type, payload))
  return { run, external }
}
async function waitCase(request: APIRequestContext, taskId: string) {
  for (let i = 0; i < 60; i++) {
    const cases: {id:string, task_id:string}[] = await call(request, 'get', '/api/v1/decision-cases')
    const found = cases.find(c => c.task_id === taskId)
    if (found) return found
    await new Promise(resolve => setTimeout(resolve, 500))
  }
  throw new Error('Decision case not generated')
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(value => sessionStorage.setItem('dirima-token', value), token)
})

test('agreement is compressed to one auto-resolved cluster and receipt', async ({ page, request }, testInfo) => {
  const { project, task: work } = await task(request)
  const finding = { subject: `Agreement-${randomUUID()}`, claim: 'Use PostgreSQL advisory lock', severity: 'LOW' }
  const first = await addRun(request, project, work, 'Claude', 'claude', finding)
  await addRun(request, project, work, 'Codex', 'gpt', finding)
  await call(request, 'post', '/api/v1/events', envelope(project, work, first.run, first.external,
    'verification.reported', { subject: finding.subject, method: 'pytest', command_result: { command: ['pytest'], exit_code: 0, stdout: '412 passed', stderr: '', observer: 'e2e' } }))
  await expect.poll(async () => {
    const response = await request.get(`${apiBase}/api/v1/tasks/${work.id}/receipt`, { headers: { Authorization: `Bearer ${token}` } })
    return response.ok() ? (await response.json()).content?.finding_count : 0
  }, { timeout: 30000 }).toBe(2)
  const cases: {task_id:string}[] = await call(request, 'get', '/api/v1/decision-cases')
  expect(cases.filter(c => c.task_id === work.id)).toHaveLength(0)
  await page.goto(`/tasks/${work.id}`)
  await expect(page.getByText('Completion receipt')).toBeVisible()
  await expect(page.getByText('PASSED', { exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('receipt.png'), fullPage: true })
})

test('unresolved conflict reaches inbox and human resolution', async ({ page, request }, testInfo) => {
  const { project, task: work } = await task(request)
  const subject = `Lock-${randomUUID()}`
  await addRun(request, project, work, 'Claude', 'claude', { subject, claim: 'PostgreSQL lock' })
  await addRun(request, project, work, 'Codex', 'gpt', { subject, claim: 'Redis lock' })
  const decision = await waitCase(request, work.id)
  await page.goto('/')
  await expect(page.getByText(`Conflicting findings: ${subject.toLowerCase()}`)).toBeVisible()
  await page.goto(`/decisions/${decision.id}`)
  await expect(page.getByText('Choose an option')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('decision.png'), fullPage: true })
  await page.getByLabel('PostgreSQL lock').check()
  await page.getByLabel('Your name').fill('E2E reviewer')
  await page.getByLabel(/Rationale/).fill('Reviewed both findings')
  await page.getByText('Findings and evidence (2)').click()
  await page.getByText(/AGENT_ASSERTION · run:/).first().click()
  await expect(page.locator('pre').first()).toBeVisible()
  const runLink = await page.getByRole('link', { name: 'Open run →' }).first().getAttribute('href')
  expect(runLink).toContain('/runs/')
  await page.getByRole('link', { name: 'Open run →' }).first().click()
  await page.getByText('Raw events (3)').click()
  await expect(page.getByText('run.completed', { exact: true })).toBeVisible()
  await page.goto(`/decisions/${decision.id}`)
  await page.getByLabel('PostgreSQL lock').check()
  await page.getByLabel('Your name').fill('E2E reviewer')
  await page.getByLabel(/Rationale/).fill('Reviewed both findings')
  await page.getByRole('button', { name: 'Save decision' }).click()
  await expect(page).toHaveURL(/\/$/)
  const detail = await call(request, 'get', `/api/v1/decision-cases/${decision.id}`)
  expect(detail.status).toBe('RESOLVED')
  expect(detail.conflict.status).toBe('HUMAN_RESOLVED')
  const inbox: {task_id:string}[] = await call(request, 'get', '/api/v1/decision-cases')
  expect(inbox.filter(c => c.task_id === work.id)).toHaveLength(0)
})

test('high-risk unanimous claim still requires a human', async ({ page, request }) => {
  const { project, task: work } = await task(request)
  const finding = { subject: `Migration-${randomUUID()}`, claim: 'production DB destructive migration', severity: 'LOW' }
  await addRun(request, project, work, 'Claude', 'claude', finding)
  await addRun(request, project, work, 'Codex', 'gpt', finding)
  const decision = await waitCase(request, work.id)
  await page.goto(`/decisions/${decision.id}`)
  await expect(page.getByText('CRITICAL RISK')).toBeVisible()
  await expect(page.getByLabel(/Rationale/)).toHaveAttribute('required', '')
  const detail = await call(request, 'get', `/api/v1/decision-cases/${decision.id}`)
  expect(detail.status).toBe('AWAITING_HUMAN')
})
