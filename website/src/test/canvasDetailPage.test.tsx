import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import ArtifactDetailPage from '../pages/ArtifactDetailPage'
import { NavigationLeaveGuardProvider } from '../components/NavigationLeaveGuard'
import { emptyCanvas, serializeCanvas } from '../components/canvas/canvasModel'
import { api } from '../api/client'
import type { Artifact, ArtifactComment } from '../types'
import { renderWithProviders } from './helpers'

vi.mock('../api/client')
vi.mock('../pages/ChatPage', () => ({ default: () => <div />, PREFILL_STORAGE_KEY: 'kirocrew_prefill' }))
let mobile = false
vi.mock('../hooks/useIsMobile', () => ({ useIsMobile: () => mobile }))

const canvas: Artifact = {
  slug: 'board', name: 'Board', kind: 'canvas', source: 'manual', description: 'Sprint plan', tags: [],
  version: 1, content_token: 't1', content: serializeCanvas(emptyCanvas()),
  created_at: '2026-11-13T12:00:00Z', updated_at: '2026-11-13T12:00:00Z',
}
const notes: Artifact = { ...canvas, slug: 'notes', name: 'Notes', kind: 'markdown', content: '# Notes', content_token: undefined }

async function renderPage(artifact: Artifact, comments: ArtifactComment[] = [], versions: number[] | Error = [1]) {
  vi.mocked(api).artifact = vi.fn().mockResolvedValue(artifact)
  vi.mocked(api).artifactVersions = versions instanceof Error
    ? vi.fn().mockRejectedValue(versions)
    : vi.fn().mockResolvedValue({ slug: artifact.slug, versions })
  vi.mocked(api).artifactVersion = vi.fn().mockResolvedValue(artifact)
  vi.mocked(api).artifactEvents = vi.fn().mockResolvedValue({ slug: artifact.slug, events: [] })
  vi.mocked(api).artifactComments = vi.fn().mockResolvedValue({ comments })
  vi.mocked(api).chatSlots = vi.fn().mockResolvedValue([])
  renderWithProviders(
    <NavigationLeaveGuardProvider>
      <Routes>
        <Route path="/artifacts/:slug" element={<ArtifactDetailPage />} />
        <Route path="/artifacts" element={<div data-testid="library" />} />
      </Routes>
    </NavigationLeaveGuardProvider>,
    { route: `/artifacts/${artifact.slug}` },
  )
  await screen.findByRole('button', { name: 'More actions' })
  await waitFor(() => expect(api.artifactEvents).toHaveBeenCalled())
}

beforeEach(() => { vi.mocked(api).artifactFolders = vi.fn().mockResolvedValue({ folders: [] }) })
afterEach(() => { cleanup(); vi.restoreAllMocks(); mobile = false })

function openMore() {
  fireEvent.keyDown(screen.getByRole('button', { name: 'More actions' }), { key: 'Enter' })
}

describe('canvas detail page', () => {
  it('fills the page edge to edge, with no header row, border or rounding', async () => {
    await renderPage(canvas)
    const box = await screen.findByTestId('canvas-artifact')
    expect(box.className).toContain('absolute inset-0')
    expect(box.className).not.toMatch(/\b(border|rounded)/)
    expect(screen.getByTestId('canvas-page-pill')).toBeInTheDocument()
  })

  it('floats Back, title, version, chat and More in one pill, and leaves the rest out', async () => {
    await renderPage(canvas)
    await screen.findByTestId('canvas-artifact')
    const pill = within(screen.getByTestId('canvas-page-pill'))
    expect(pill.getByRole('button', { name: 'Back' })).toBeInTheDocument()
    expect(pill.getByRole('button', { name: 'Board' })).toBeInTheDocument()
    expect(pill.getByRole('combobox', { name: 'Version' })).toBeInTheDocument()
    expect(pill.queryByRole('button', { name: 'Star artifact' })).toBeNull()
    expect(pill.getByRole('button', { name: 'Toggle agent chat' })).toHaveAttribute('aria-pressed', 'false')
    expect(pill.getByRole('button', { name: 'More actions' })).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Compare with' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Add a tag' })).toBeNull()
    expect(screen.queryByText('Activity')).toBeNull()
    expect(screen.queryByText(/^Created /)).toBeNull()
  })

  it('opens the agent chat as a column beside the canvas', async () => {
    vi.mocked(api).createChatSlot = vi.fn().mockResolvedValue({ key: 'chat-new' })
    await renderPage(canvas)
    await screen.findByTestId('canvas-artifact')
    expect(screen.queryByTestId('canvas-page-chat-panel')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Toggle agent chat' }))
    expect(await screen.findByTestId('canvas-page-chat-panel')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Toggle agent chat' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('canvas-artifact')).toBeInTheDocument()
  })

  it('stars from the More menu', async () => {
    vi.mocked(api).setArtifactPinned = vi.fn().mockResolvedValue({})
    await renderPage(canvas)
    await screen.findByTestId('canvas-artifact')
    openMore()
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Star artifact' }))
    await waitFor(() => expect(api.setArtifactPinned).toHaveBeenCalledWith('board', true))
  })

  it('shows a failed star under the pill', async () => {
    vi.mocked(api).setArtifactPinned = vi.fn().mockRejectedValue(new Error('star refused'))
    await renderPage(canvas)
    await screen.findByTestId('canvas-artifact')
    openMore()
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Star artifact' }))
    const notice = (await screen.findByText('star refused')).closest('[role="alert"]') as HTMLElement
    expect(notice).toHaveTextContent(/Save failed/)
    expect(within(notice).queryByRole('button', { name: /ask the agent/i })).toBeNull()
  })

  it('shows a versions-load failure under the pill without a draft-losing hand-off', async () => {
    await renderPage(canvas, [], new Error('versions unavailable'))
    const notice = (await screen.findByText('versions unavailable')).closest('[role="alert"]') as HTMLElement
    expect(notice).toHaveTextContent('Versions failed to load')
    const pill = screen.getByTestId('canvas-page-pill')
    expect(pill.parentElement).toContainElement(notice)
    expect(pill).not.toContainElement(notice)
    expect(within(notice).queryByRole('button', { name: /ask the agent/i })).toBeNull()
  })

  it.each(['Activity', 'Hide comments', 'Collapse comments', 'Toggle agent chat', 'Draft a prompt in chat'])(
    'keeps an unsent comment mounted when choosing %s, and restores it on return', async target => {
      vi.mocked(api).createChatSlot = vi.fn().mockResolvedValue({ key: 'chat-new' })
      await renderPage(canvas, [{
        id: 'c1', origin: 'local', scope: 'private', author: 'me', is_agent: false, body: 'nice board',
        thread_id: 'c1', status: 'open', sync_state: 'local', created_at: '2026-11-13T12:00:00Z',
      } as ArtifactComment])
      openMore()
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Show comments' }))
      const comments = await screen.findByTestId('canvas-page-comments')
      fireEvent.click(within(comments).getByRole('button', { name: 'Add comment' }))
      const draft = within(comments).getByRole('textbox', { name: 'Add comment' })
      fireEvent.change(draft, { target: { value: 'Keep this unfinished thought' } })

      if (target === 'Activity' || target === 'Hide comments') {
        openMore()
        fireEvent.click(await screen.findByRole('menuitem', { name: target }))
      } else {
        fireEvent.click(screen.getByRole('button', { name: target, exact: true }))
      }
      if (target === 'Activity') await screen.findByTestId('canvas-page-activity')
      if (target === 'Toggle agent chat' || target === 'Draft a prompt in chat') {
        await screen.findByTestId('canvas-page-chat-panel')
      }
      expect(draft).toBeInTheDocument()
      expect(comments).toHaveClass('hidden')
      expect(screen.queryByRole('textbox', { name: 'Add comment' })).toBeNull()
      expect(draft).toHaveValue('Keep this unfinished thought')
      openMore()
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Show comments' }))
      expect(screen.getByRole('textbox', { name: 'Add comment' })).toBe(draft)
      expect(draft).toHaveValue('Keep this unfinished thought')
    },
  )

  it.each(['Reply', 'Edit'])('keeps an unsent %s draft when switching to Activity', async action => {
    await renderPage(canvas, [{
      id: 'c1', origin: 'local', scope: 'private', author: 'me', is_agent: false, body: 'nice board',
      thread_id: 'c1', status: 'open', sync_state: 'local', created_at: '2026-11-13T12:00:00Z',
    } as ArtifactComment])
    openMore()
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Show comments' }))
    const comments = await screen.findByTestId('canvas-page-comments')
    fireEvent.click(within(comments).getByRole('button', { name: action, exact: true }))
    const draft = within(comments).getByRole('textbox')
    fireEvent.change(draft, { target: { value: 'Unfinished revision' } })
    openMore()
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Activity' }))
    await screen.findByTestId('canvas-page-activity')
    expect(draft).toBeInTheDocument()
    expect(comments).toHaveClass('hidden')
    expect(screen.queryByRole('textbox')).toBeNull()
    openMore()
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Show comments' }))
    expect(within(comments).getByRole('textbox')).toBe(draft)
    expect(draft).toHaveValue('Unfinished revision')
  })

  it('on a phone, an open chat takes the whole width and the board steps aside', async () => {
    mobile = true
    vi.mocked(api).createChatSlot = vi.fn().mockResolvedValue({ key: 'chat-new' })
    await renderPage(canvas)
    await screen.findByTestId('canvas-artifact')
    expect(screen.getByTestId('canvas-page-board')).not.toHaveClass('hidden')
    fireEvent.click(screen.getByRole('button', { name: 'Toggle agent chat' }))
    await screen.findByTestId('canvas-page-chat-panel')
    const column = screen.getByTestId('canvas-page-column')
    expect(column).toHaveClass('flex-1')
    expect(column).not.toHaveClass('shrink-0')
    expect(screen.getByTestId('canvas-page-board')).toHaveClass('hidden')
    expect(screen.getByTestId('canvas-artifact')).toBeInTheDocument()
  })

  it('opens comments from the More menu as the right-hand column, in place of the chat', async () => {
    vi.mocked(api).createChatSlot = vi.fn().mockResolvedValue({ key: 'chat-new' })
    await renderPage(canvas)
    await screen.findByTestId('canvas-artifact')
    fireEvent.click(screen.getByRole('button', { name: 'Toggle agent chat' }))
    await screen.findByTestId('canvas-page-chat-panel')
    openMore()
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Show comments' }))
    expect(await screen.findByTestId('canvas-page-comments')).toBeInTheDocument()
    expect(screen.queryByTestId('canvas-page-chat-panel')).toBeNull()
    expect(screen.getByTestId('canvas-artifact')).toBeInTheDocument()
    openMore()
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Hide comments' }))
    await waitFor(() => expect(screen.queryByTestId('canvas-page-comments')).toBeNull())
  })

  it('does not open the comments column by itself when the canvas has comments', async () => {
    await renderPage(canvas, [{
      id: 'c1', origin: 'local', scope: 'private', author: 'me', is_agent: false, body: 'nice board',
      thread_id: 'c1', status: 'open', sync_state: 'local', created_at: '2026-11-13T12:00:00Z',
    } as ArtifactComment])
    await screen.findByTestId('canvas-artifact')
    await waitFor(() => expect(api.artifactComments).toHaveBeenCalled())
    expect(screen.queryByTestId('canvas-page-comments')).toBeNull()
    expect(screen.queryByTestId('canvas-page-column')).toBeNull()
  })

  it('shows the activity log from the More menu, and closes it', async () => {
    await renderPage(canvas)
    await screen.findByTestId('canvas-artifact')
    openMore()
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Activity' }))
    const activity = await screen.findByTestId('canvas-page-activity')
    expect(within(activity).getByText('Activity')).toBeInTheDocument()
    fireEvent.click(within(activity).getByRole('button', { name: 'Close' }))
    expect(screen.queryByTestId('canvas-page-activity')).toBeNull()
  })

  it('edits tags in a popover under the pill', async () => {
    await renderPage({ ...canvas, tags: ['planning'] })
    await screen.findByTestId('canvas-artifact')
    expect(screen.queryByText('planning')).toBeNull()
    openMore()
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Tags' }))
    const tags = within(await screen.findByTestId('canvas-page-tags'))
    expect(tags.getByText('planning')).toBeInTheDocument()
    expect(tags.getByRole('button', { name: 'Add a tag' })).toBeInTheDocument()
  })

  it('offers Revert from the More menu on a past version only, and reverts after the question', async () => {
    const live = { ...canvas, version: 2 }
    await renderPage(live, [], [1, 2])
    vi.mocked(api).artifactVersion = vi.fn().mockResolvedValue({ ...canvas, version: 1, content: 'v1 board' })
    vi.mocked(api).updateArtifact = vi.fn().mockResolvedValue({ ...live, version: 3 })
    await screen.findByTestId('canvas-artifact')
    openMore()
    await screen.findByRole('menuitem', { name: 'Show comments' })
    expect(screen.queryByRole('menuitem', { name: /^Revert/ })).toBeNull()
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())

    fireEvent.click(screen.getByRole('combobox', { name: 'Version' }))
    fireEvent.click(await screen.findByRole('option', { name: 'v1' }))
    await waitFor(() => expect(api.artifactVersion).toHaveBeenCalledWith('board', 1))
    // The past version's board replaces the live one; wait for its pill before using it.
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Version' })).toHaveTextContent('v1'))
    await screen.findByRole('button', { name: 'More actions' })
    openMore()
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Revert to v1' }))
    const question = await screen.findByRole('dialog', { name: 'Revert' })
    expect(api.updateArtifact).not.toHaveBeenCalled()
    fireEvent.click(within(question).getByRole('button', { name: 'Revert' }))
    await waitFor(() => expect(api.updateArtifact).toHaveBeenCalledWith('board', {
      content: 'v1 board', event_type: 'reverted', from_version: 1,
    }))
  })

  it('leaves through Back', async () => {
    await renderPage(canvas)
    await screen.findByTestId('canvas-artifact')
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(await screen.findByTestId('library')).toBeInTheDocument()
  })

  it('opens an empty canvas on the same page', async () => {
    await renderPage(canvas)
    expect(await screen.findByTestId('canvas-editor')).toBeInTheDocument()
    expect(screen.queryAllByTestId('canvas-shape')).toHaveLength(0)
  })
})

describe('a page of any other kind', () => {
  it('keeps the generic header, its chips and its footer', async () => {
    await renderPage(notes)
    expect(screen.queryByTestId('canvas-page-pill')).toBeNull()
    expect(screen.getByRole('button', { name: 'Star artifact' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add a tag' })).toBeInTheDocument()
    expect(screen.getByText(/Created /)).toBeInTheDocument()
    expect(screen.getByText('Activity')).toBeInTheDocument()
  })
})
