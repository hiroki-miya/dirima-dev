import { useState } from 'react'
import { Link, NavLink, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { api, Attention, Case, CaseDetail, getToken, Project, Run, setToken, Task } from './api'

function useData<T>(path: string, schema: z.ZodType<T>) {
  return useQuery({ queryKey: [path], queryFn: () => api(path, schema), retry: false, refetchInterval: 5000 })
}
function ErrorView({ error }: { error: Error | null }) { return error && <p role="alert" className="error">{error.message}</p> }
function DateText({ value }: { value: string }) { return <time dateTime={value}>{new Date(value).toLocaleString()}</time> }

function Login({ onLogin }: { onLogin: () => void }) {
  const [token, set] = useState('')
  return <main className="login"><div className="login-panel"><span className="eyebrow">DIRIMA / ACCESS</span>
    <h1>Decisions, in focus.</h1><p>Enter the workspace bearer token to review work.</p>
    <form onSubmit={e => { e.preventDefault(); setToken(token); onLogin() }}>
      <label htmlFor="token">Bearer token</label><input id="token" type="password" required value={token} onChange={e => set(e.target.value)} autoComplete="off" />
      <button>Open workspace</button></form></div></main>
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="shell"><aside className="sidebar"><Link className="brand" to="/">◈ <span>Dirima</span></Link>
    <p className="side-caption">CONTROL PLANE</p><nav>
      <NavLink to="/" end>Decision Inbox</NavLink><NavLink to="/projects">Projects</NavLink>
      <NavLink to="/system/metrics">System metrics</NavLink></nav>
    <p className="side-caption">CONFIGURATION</p><nav><NavLink to="/settings/providers">Providers</NavLink><NavLink to="/settings/adapters">Adapters</NavLink></nav>
    <div className="side-foot">Observation · Reduction · Decision<br/>v0.1</div>
  </aside><div className="main-area"><header className="topbar"><span>Human attention control plane</span><button className="text-button" onClick={() => { sessionStorage.removeItem('dirima-token'); location.reload() }}>Sign out</button></header><main className="content">{children}</main></div></div>
}

function Inbox() {
  const cases = useData('/api/v1/decision-cases', z.array(Case))
  const attention = useData('/api/v1/attention', z.array(Attention))
  const metrics = useData('/api/v1/metrics', z.record(z.string(), z.number().nullable()))
  const items = cases.data || []
  const notices = attention.data || []
  return <><div className="page-heading"><div><span className="eyebrow">OVERVIEW</span><h1>Decision Inbox</h1><p>Only matters requiring your attention appear here.</p></div><span className="live-pill">● LIVE WORKSPACE</span></div>
    <div className="stats"><div><span>Human decisions</span><strong>{items.length}</strong></div><div><span>Blocking</span><strong>{items.filter(c => c.attention_class === 'BLOCKING').length}</strong></div><div><span>Attention</span><strong>{notices.length}</strong></div></div>
    <div className="section-title"><h2>Needs a decision</h2><span>{items.length} open</span></div><ErrorView error={cases.error} />
    {items.length ? <div className="cards">{items.map(item => <Link className="decision-card" key={item.id} to={`/decisions/${item.id}`}>
      <div className="card-top"><span className={`risk ${item.risk_level.toLowerCase()}`}>{item.risk_level} RISK</span><span className="arrow">↗</span></div>
      <h3>{item.title}</h3><p>{item.question}</p><p className="card-reason">{item.reason_human_required}</p>
      <div className="card-facts"><span>Reversibility: {item.reversibility}</span><span>Options: {item.option_labels?.join(' / ') || 'Review details'}</span><span>Evidence: {item.evidence_summary || 'Review details'}</span><span>Task: {item.affected_task || item.task_id}</span></div>
      <div className="card-bottom"><span>Needs your decision</span><span><DateText value={item.created_at}/></span></div>
    </Link>)}</div> : <div className="empty">No decisions waiting. Verified work is summarized in task receipts.</div>}
    <div className="section-title"><h2>Attention</h2><span>{notices.length} items</span></div>
    {notices.map(item => <article className="attention" key={item.id}><span className="risk medium">{item.classification}</span><div><strong>{item.title}</strong><p>{item.detail}</p></div></article>)}
    <div className="summary-strip"><span>Auto resolved <strong>{metrics.data?.dirima_auto_resolutions_total ?? '—'}</strong></span><span>Completed tasks <strong>{metrics.data?.completed_tasks ?? '—'}</strong></span><span>Runs <strong>{metrics.data?.dirima_runs_total ?? '—'}</strong></span></div></>
}

function Decision() {
  const { id } = useParams()
  const query = useData(`/api/v1/decision-cases/${id}`, CaseDetail)
  const client = useQueryClient()
  const navigate = useNavigate()
  const [selected, setSelected] = useState('')
  const [actor, setActor] = useState('')
  const [rationale, setRationale] = useState('')
  const mutation = useMutation({ mutationFn: () => api(`/api/v1/decision-cases/${id}/resolve`, z.unknown(), {
    method: 'POST', body: JSON.stringify({ selected_option_id: selected, actor, rationale }) }),
    onSuccess: () => { void client.invalidateQueries(); navigate('/') } })
  const caseAction = useMutation({ mutationFn: (action: 'dismiss'|'investigate') => api(`/api/v1/decision-cases/${id}/${action}`, z.unknown(), { method: 'POST', body: JSON.stringify({ actor, rationale }) }), onSuccess: () => { void client.invalidateQueries(); navigate('/') } })
  const data = query.data
  if (query.isLoading) return <p>Loading decision…</p>
  if (!data) return <ErrorView error={query.error} />
  return <><div className="breadcrumb"><Link to="/">Decision Inbox</Link> / Decision</div><div className="page-heading"><div><span className="eyebrow">HUMAN DECISION</span><h1>{data.title}</h1></div><span className={`risk ${data.risk_level.toLowerCase()}`}>{data.risk_level} RISK</span></div>
    <div className="detail-grid"><section className="panel"><h2>What happened?</h2><p>{data.question}</p><h2>Why do you need me?</h2><p>{data.reason_human_required}</p><h2>Evidence summary</h2><p>{data.findings.length} findings · {data.findings.reduce((n, f) => n + f.evidence.length, 0)} evidence items. Open the drill-down below for sources.</p><div className="meta"><span>Reversibility <strong>{data.reversibility}</strong></span><span>Affected task <Link to={`/tasks/${data.task_id}`}>{data.affected_task.title}</Link></span></div></section>
    <section className="panel"><h2>Choose an option</h2><form onSubmit={e => { e.preventDefault(); mutation.mutate() }}>
      <div className="options">{data.options.map(option => <label className={`option ${selected === option.id ? 'selected' : ''}`} key={option.id}><input type="radio" name="option" value={option.id} checked={selected === option.id} onChange={() => setSelected(option.id)} required /><span><strong>{option.label}</strong><small>{option.description}</small><small>Impact: {option.downstream_impact}</small></span></label>)}</div>
      <label htmlFor="actor">Your name</label><input id="actor" value={actor} onChange={e => setActor(e.target.value)} required />
      <label htmlFor="rationale">Rationale {['HIGH','CRITICAL'].includes(data.risk_level) && '(required)'}</label><textarea id="rationale" value={rationale} onChange={e => setRationale(e.target.value)} required={['HIGH','CRITICAL'].includes(data.risk_level)} />
      <button disabled={!selected || mutation.isPending || !['OPEN','AWAITING_HUMAN'].includes(data.status)}>Save decision</button><ErrorView error={mutation.error} /></form>
      {['OPEN','AWAITING_HUMAN'].includes(data.status) ? <div className="case-actions"><button className="text-button" disabled={!actor.trim() || !rationale.trim() || caseAction.isPending} onClick={() => caseAction.mutate('investigate')}>Request investigation</button><button className="text-button" disabled={!actor.trim() || !rationale.trim() || caseAction.isPending} onClick={() => caseAction.mutate('dismiss')}>Dismiss with rationale</button></div> : <p>Case {data.status.toLowerCase()}.</p>}<ErrorView error={caseAction.error}/></section></div>
    <details className="drill"><summary>Findings and evidence ({data.findings.length})</summary>{data.findings.map(f => <div className="finding" key={f.id}><strong>{f.subject}</strong><p>{f.claim}</p><span className="risk low">{f.severity}</span>
      {f.evidence.map(e => <details key={e.id}><summary>{e.type} · {e.source_ref}</summary><pre>{e.content}</pre></details>)}<Link to={`/runs/${f.run_id}`}>Open run →</Link></div>)}</details></>
}

function Projects() {
  const query = useData('/api/v1/projects', z.array(Project))
  const client = useQueryClient()
  const [name, setName] = useState('')
  const mutation = useMutation({ mutationFn: () => api('/api/v1/projects', Project, { method: 'POST', body: JSON.stringify({ name }) }), onSuccess: () => { setName(''); void client.invalidateQueries() } })
  return <><div className="page-heading"><div><span className="eyebrow">WORKSPACE</span><h1>Projects</h1></div></div>
    <section className="panel"><h2>Create project</h2><form className="inline-form" onSubmit={e => { e.preventDefault(); mutation.mutate() }}><input aria-label="Project name" placeholder="Project name" value={name} onChange={e => setName(e.target.value)} required /><button>Create</button></form><ErrorView error={mutation.error}/></section>
    <ErrorView error={query.error}/><div className="cards">{query.data?.map(project => <Link className="decision-card" to={`/projects/${project.id}`} key={project.id}><h3>{project.name}</h3><p>{project.description || 'Project workspace'}</p><span className="arrow">↗</span></Link>)}</div></>
}

function ProjectDetail() {
  const { id } = useParams()
  const query = useData(`/api/v1/projects/${id}`, Project)
  const constraints = useData(`/api/v1/projects/${id}/constraints`, z.array(z.object({ id: z.string(), statement: z.string(), type: z.string(), status: z.string() })))
  const goals = useData(`/api/v1/projects/${id}/goals`, z.array(z.object({ id: z.string(), title: z.string() })))
  const tasks = useData(`/api/v1/projects/${id}/tasks`, z.array(z.object({ id: z.string(), title: z.string(), status: z.string() })))
  const client = useQueryClient()
  const [statement, setStatement] = useState('')
  const [type, setType] = useState<'HARD'|'SOFT'>('HARD')
  const [goalTitle, setGoalTitle] = useState('')
  const [taskTitle, setTaskTitle] = useState('')
  const [goalId, setGoalId] = useState('')
  const mutation = useMutation({ mutationFn: () => api(`/api/v1/projects/${id}/constraints`, z.unknown(), { method: 'POST', body: JSON.stringify({ statement, type }) }), onSuccess: () => { setStatement(''); void client.invalidateQueries() } })
  const addGoal = useMutation({ mutationFn: () => api(`/api/v1/projects/${id}/goals`, z.unknown(), { method: 'POST', body: JSON.stringify({ title: goalTitle }) }), onSuccess: () => { setGoalTitle(''); void client.invalidateQueries() } })
  const addTask = useMutation({ mutationFn: () => api('/api/v1/tasks', z.unknown(), { method: 'POST', body: JSON.stringify({ project_id: id, title: taskTitle, goal_id: goalId || null }) }), onSuccess: () => { setTaskTitle(''); void client.invalidateQueries() } })
  return <><div className="breadcrumb"><Link to="/projects">Projects</Link> / Project</div><h1>{query.data?.name || 'Project'}</h1><ErrorView error={query.error}/><section className="panel"><h2>Human constraints</h2>{constraints.data?.map(c => <p key={c.id}><span className="risk low">{c.type}</span> {c.statement}</p>)}
    <form onSubmit={e => { e.preventDefault(); mutation.mutate() }}><label htmlFor="statement">Add constraint</label><textarea id="statement" value={statement} onChange={e => setStatement(e.target.value)} required/><select value={type} onChange={e => setType(e.target.value as 'HARD'|'SOFT')}><option>HARD</option><option>SOFT</option></select><button>Add</button></form><ErrorView error={mutation.error}/></section>
    <section className="panel"><h2>Goals</h2>{goals.data?.map(goal => <p key={goal.id}>{goal.title}</p>)}<form className="inline-form" onSubmit={e => { e.preventDefault(); addGoal.mutate() }}><input aria-label="Goal title" placeholder="Goal title" value={goalTitle} onChange={e => setGoalTitle(e.target.value)} required/><button>Add goal</button></form><ErrorView error={addGoal.error}/></section>
    <section className="panel"><h2>Tasks</h2>{tasks.data?.map(task => <p key={task.id}><Link to={`/tasks/${task.id}`}>{task.title}</Link> · {task.status}</p>)}<form onSubmit={e => { e.preventDefault(); addTask.mutate() }}><label htmlFor="task-title">Task title</label><input id="task-title" value={taskTitle} onChange={e => setTaskTitle(e.target.value)} required/><label htmlFor="goal">Goal</label><select id="goal" value={goalId} onChange={e => setGoalId(e.target.value)}><option value="">No goal</option>{goals.data?.map(goal => <option key={goal.id} value={goal.id}>{goal.title}</option>)}</select><button>Create task</button></form><ErrorView error={addTask.error}/></section></>
}

function TaskDetail() {
  const { id } = useParams()
  const query = useData(`/api/v1/tasks/${id}`, Task)
  const receipt = useData(`/api/v1/tasks/${id}/receipt`, z.object({ content: ReceiptContent }))
  const data = query.data
  return <><div className="breadcrumb"><Link to="/">Decision Inbox</Link> / Task</div><h1>{data?.title || 'Task'}</h1><ErrorView error={query.error}/><p>{data?.description}</p>
    <section className="panel"><h2>Completion receipt</h2>{receipt.data ? <ReceiptView content={receipt.data.content}/> : <p>Receipt will appear after the runs finish.</p>}</section>
    <section className="panel"><h2>Runs</h2>{data?.runs.map(run => <p key={run.id}><Link to={`/runs/${run.id}`}>{run.agent_name}</Link> · {run.status} · {run.model_family}</p>)}</section></>
}

const ReceiptContent = z.object({ outcome: z.string(), reduction_status: z.string(),
  summary: z.string().optional(), artifacts: z.array(z.object({ id: z.string(), name: z.string(), sha256: z.string() })),
  verification: z.array(z.object({ method: z.string(), status: z.string() })),
  residual_risks: z.array(z.object({ level: z.string(), reason: z.string() })),
  unresolved_items: z.array(z.string()), resolved_conflicts: z.array(z.string()), human_decisions: z.array(z.string()),
  runs_involved: z.array(z.string()), agent_reports: z.array(z.object({ agent_name: z.string(), summary: z.string() })).optional() })

function ReceiptView({ content }: { content: z.infer<typeof ReceiptContent> }) {
  return <><p>Outcome: <strong>{content.outcome}</strong> · Reduction: {content.reduction_status.toLowerCase()}</p>
    {content.summary && <p><strong>Model summary</strong><br/>{content.summary}</p>}
    <p>{content.runs_involved.length} runs · {content.resolved_conflicts.length} resolved conflicts · {content.human_decisions.length} human decisions</p>
    <h2>Verification</h2>{content.verification.length ? content.verification.map((v, i) => <p key={i}>{v.method} · <strong>{v.status}</strong></p>) : <p>No independent verification recorded.</p>}
    {!!content.residual_risks.length && <><h2>Residual risks</h2>{content.residual_risks.map((r, i) => <p key={i}><span className={`risk ${r.level.toLowerCase()}`}>{r.level}</span> {r.reason}</p>)}</>}
    <p>Unresolved items: {content.unresolved_items.length}</p>
    {!!content.artifacts.length && <><h2>Artifacts</h2>{content.artifacts.map(a => <ArtifactPreview key={a.id} artifact={{ id: a.id, original_name: a.name, sha256: a.sha256 }}/>)}</>}
    {!!content.agent_reports?.length && <details className="drill"><summary>Agent-reported summaries</summary>{content.agent_reports.map((r, i) => <p key={i}><strong>{r.agent_name}</strong><br/>{r.summary}</p>)}</details>}
    <details className="drill"><summary>Receipt details</summary><pre>{JSON.stringify(content, null, 2)}</pre></details></>
}

function RunDetail() {
  const { id } = useParams()
  const query = useData(`/api/v1/runs/${id}`, Run.extend({ events: z.array(z.object({ event_id: z.string(), type: z.string(), occurred_at: z.string(), payload: z.unknown() })), artifacts: z.array(z.object({ id: z.string(), original_name: z.string(), sha256: z.string() })) }))
  const run = query.data
  return <><div className="breadcrumb"><Link to="/">Decision Inbox</Link> / Run</div><h1>{run?.agent_name || 'Run'}</h1><ErrorView error={query.error}/><p>{run?.status} · {run?.model_family}</p>
    <section className="panel"><h2>Artifacts</h2>{run?.artifacts.map(a => <ArtifactPreview key={a.id} artifact={a}/>)}</section>
    <details className="drill"><summary>Raw events ({run?.events.length || 0})</summary>{run?.events.map(e => <div className="finding" key={e.event_id}><strong>{e.type}</strong> · <DateText value={e.occurred_at}/><pre>{JSON.stringify(e.payload, null, 2)}</pre></div>)}</details></>
}

function ArtifactPreview({ artifact }: { artifact: { id: string, original_name: string, sha256: string } }) {
  const [opened, setOpened] = useState(false)
  const query = useQuery({ queryKey: ['artifact', artifact.id], enabled: opened,
    queryFn: () => api(`/api/v1/artifacts/${artifact.id}/content`, z.object({ content: z.string(), truncated: z.boolean() })), retry: false })
  return <details className="drill" onToggle={e => setOpened(e.currentTarget.open)}><summary>{artifact.original_name}</summary><p>SHA-256: {artifact.sha256}</p><ErrorView error={query.error}/>{query.data && <><pre>{query.data.content}</pre>{query.data.truncated && <p>Preview truncated.</p>}</>}</details>
}

function Metrics() {
  const query = useData('/api/v1/metrics', z.record(z.string(), z.number().nullable()))
  return <><span className="eyebrow">SYSTEM</span><h1>Metrics</h1><ErrorView error={query.error}/><div className="stats metrics">{Object.entries(query.data || {}).map(([name, value]) => <div key={name}><span>{name}</span><strong>{value ?? '—'}</strong></div>)}</div></>
}

function Settings({ kind }: { kind: 'providers' | 'adapters' }) {
  const query = useData('/api/v1/configuration', z.object({ provider: z.string(), endpoint_configured: z.boolean(), roles: z.record(z.string(), z.string().nullable()), adapters: z.array(z.string()), durevi_enabled: z.boolean() }))
  return <><span className="eyebrow">SETTINGS</span><h1>{kind === 'providers' ? 'Providers' : 'Adapters'}</h1><ErrorView error={query.error}/><section className="panel">{kind === 'providers' ? <><p>Provider: {query.data?.provider} · {query.data?.endpoint_configured ? 'Endpoint configured' : 'Endpoint unavailable'}</p>{Object.entries(query.data?.roles || {}).map(([role, model]) => <p key={role}>{role}: {model || 'Deterministic / disabled'}</p>)}</> : <><p>Active adapters: {query.data?.adapters.join(', ')}</p><p>GenericAdapter accepts events, completion receipts, transcripts, stdout and stderr.</p><p>Durevi: {query.data?.durevi_enabled ? 'Connected' : 'Optional integration disabled'}</p></>}</section></>
}

export default function App() {
  const [authenticated, setAuthenticated] = useState(Boolean(getToken()))
  if (!authenticated) return <Login onLogin={() => setAuthenticated(true)}/>
  return <Shell><Routes><Route path="/" element={<Inbox/>}/><Route path="/projects" element={<Projects/>}/><Route path="/projects/:id" element={<ProjectDetail/>}/><Route path="/tasks/:id" element={<TaskDetail/>}/><Route path="/decisions/:id" element={<Decision/>}/><Route path="/runs/:id" element={<RunDetail/>}/><Route path="/settings/providers" element={<Settings kind="providers"/>}/><Route path="/settings/adapters" element={<Settings kind="adapters"/>}/><Route path="/system/metrics" element={<Metrics/>}/></Routes></Shell>
}
