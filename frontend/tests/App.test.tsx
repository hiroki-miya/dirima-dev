import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from '../src/App'

beforeEach(() => { sessionStorage.clear(); window.history.pushState({}, '', '/') })
afterEach(() => vi.restoreAllMocks())

test('requires a bearer token before showing the workspace', () => {
  render(<BrowserRouter><QueryClientProvider client={new QueryClient()}><App/></QueryClientProvider></BrowserRouter>)
  expect(screen.getByLabelText('Bearer token')).toBeInTheDocument()
  expect(screen.queryByText('Decision Inbox')).not.toBeInTheDocument()
})

test('shows concise decision inbox without raw transcript', async () => {
  sessionStorage.setItem('dirima-token', 'test-token')
  const responses: Record<string, unknown> = {
    '/api/v1/decision-cases': [{ id: '1', project_id: '2', task_id: '3', title: 'Choose lock strategy',
      question: 'PostgreSQL or Redis?', reason_human_required: 'Conflicting evidence', risk_level: 'MEDIUM',
      reversibility: 'unknown', attention_class: 'HUMAN_REQUIRED', status: 'AWAITING_HUMAN',
      created_at: '2026-10-02T00:00:00Z' }],
    '/api/v1/attention': [], '/api/v1/metrics': { dirima_auto_resolutions_total: 0,
      dirima_human_decisions_total: 0, dirima_runs_total: 2 }
  }
  vi.stubGlobal('fetch', vi.fn(async (path: string) => ({ ok: true, json: async () => responses[path] })))
  render(<BrowserRouter><QueryClientProvider client={new QueryClient()}><App/></QueryClientProvider></BrowserRouter>)
  await waitFor(() => expect(screen.getByText('Choose lock strategy')).toBeInTheDocument())
  expect(screen.getByText('PostgreSQL or Redis?')).toBeInTheDocument()
  expect(screen.queryByText('raw transcript')).not.toBeInTheDocument()
})
