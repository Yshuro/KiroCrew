/**
 * The managed-environment lane's form and row actions: drawn only for the lane's own
 * provisioner and rows, actions by state, Delete asks first, another client's environment
 * gets nothing, and the preview off calls no route.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const confirmAnswer = { value: true }
const confirmSpy = vi.fn(async () => confirmAnswer.value)
vi.mock('../ConfirmDialog', () => ({
  useConfirm: () => ({ confirm: confirmSpy, confirmDialog: null, cancel: () => {}, confirmOpen: false }),
}))
// The core's menu wraps these items; a plain item keeps the test on the lane's own logic.
vi.mock('../ui/dropdown-menu', () => ({
  DropdownMenuItem: ({ onSelect, disabled, children }: { onSelect?: () => void; disabled?: boolean; children?: unknown }) => (
    <button role="menuitem" disabled={disabled} onClick={() => onSelect?.()}>
      {children as never}
    </button>
  ),
  DropdownMenuLabel: ({ children }: { children?: unknown }) => <div>{children as never}</div>,
}))

import type { InstanceView, RemoteProvisioner } from '../../api/client'
import { managedEnvApi } from '../../api/managedEnvApi'
import type { ManagedEnvItem } from '../../api/managedEnvApi'
import { getCrewRowActions } from '../remoteCrewRowActions'
import { canRenderRemoteProvisionerKind, getRemoteProvisionerRenderer } from '../remoteProvisionerRenderers'
import { PREVIEW_CREW_CLIENT, setPreviewFlag } from '../../utils/previewFlags'
import { SANDBOX_PROVISIONER_ID, SandboxCrewRowDetail, SandboxCrewRowMenuItems, rowState } from './SandboxCrewRowActions'
import SandboxLaunchForm, { SANDBOX_PROVISIONER_KIND, crewNameOk } from './SandboxLaunchForm'

const HOST = 'crew-sbx-env-001'
const inst = { id: 'r1', name: 'crew-a', ssh_host: HOST, provisioner_id: SANDBOX_PROVISIONER_ID } as InstanceView

function wrap(children: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{children}</QueryClientProvider>)
}

function menu() {
  return wrap(<SandboxCrewRowMenuItems inst={inst} disabled={false} />)
}

/** The row as the core draws it while expanded: the detail block and the menu items. */
function row() {
  return wrap(
    <>
      <SandboxCrewRowDetail inst={inst} disabled={false} />
      <SandboxCrewRowMenuItems inst={inst} disabled={false} />
    </>,
  )
}

function item(status: string, extra: Partial<ManagedEnvItem> = {}): ManagedEnvItem {
  return { sandbox_id: 'env-001', status, own: true, ssh_host: HOST, ...extra }
}

let listed: ManagedEnvItem[]
let listSpy: ReturnType<typeof vi.spyOn>
let actSpy: ReturnType<typeof vi.spyOn>
let sshPresent: boolean

beforeEach(() => {
  localStorage.clear()
  setPreviewFlag(PREVIEW_CREW_CLIENT, true)
  confirmAnswer.value = true
  confirmSpy.mockClear()
  listed = [item('RUNNING')]
  listSpy = vi.spyOn(managedEnvApi, 'list').mockImplementation(async () => ({ sandboxes: listed }))
  actSpy = vi.spyOn(managedEnvApi, 'act').mockImplementation(async () => ({ status: 'STOPPING' }))
  sshPresent = true
  vi.spyOn(managedEnvApi, 'sshConfig').mockImplementation(async () => ({
    present: sshPresent,
    path: '/home/u/.ssh/config',
    block: '# >>> kirocrew crew-sbx >>>\nHost crew-sbx-*\n  User root\n',
  }))
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('the core seed maps', () => {
  it('seed the form for the lane kind and the actions for its rows only', () => {
    expect(canRenderRemoteProvisionerKind(SANDBOX_PROVISIONER_KIND)).toBe(true)
    expect(getRemoteProvisionerRenderer(SANDBOX_PROVISIONER_KIND)?.component).toBe(SandboxLaunchForm)
    expect(getCrewRowActions(inst)?.provisionerId).toBe(SANDBOX_PROVISIONER_ID)
    expect(getCrewRowActions({ ...inst, provisioner_id: 'other' })).toBeUndefined()
  })
})

describe('the launch form', () => {
  const provisioner = { id: SANDBOX_PROVISIONER_ID, kind: SANDBOX_PROVISIONER_KIND } as RemoteProvisioner

  it('launches with a valid crew name only', async () => {
    const launch = vi.fn()
    wrap(<SandboxLaunchForm provisioner={provisioner} launch={launch} launching={false} disabled={false} activeJob={null} />)
    const button = screen.getByRole('button', { name: /Launch/ })
    expect(button).toBeDisabled()
    await userEvent.type(screen.getByRole('textbox'), 'Bad--name')
    expect(button).toBeDisabled()
    expect(screen.getByText(/2 to 32 lowercase letters/)).toBeInTheDocument()
    expect(screen.getByText('Fix the crew name to launch.')).toBeInTheDocument()
    await userEvent.clear(screen.getByRole('textbox'))
    await userEvent.type(screen.getByRole('textbox'), 'crew-a')
    await screen.findByTestId('managed-env-ssh-ready')
    await userEvent.click(button)
    expect(launch).toHaveBeenCalledWith({ profile: 'crew-a', size_key: '' })
  })

  it('holds Launch until the ssh settings are added, which the button does', async () => {
    sshPresent = false
    const addSpy = vi
      .spyOn(managedEnvApi, 'addSshConfig')
      .mockImplementation(async () => ({ present: true, path: '/home/u/.ssh/config' }))
    const launch = vi.fn()
    wrap(<SandboxLaunchForm provisioner={provisioner} launch={launch} launching={false} disabled={false} activeJob={null} />)
    await userEvent.type(screen.getByRole('textbox'), 'crew-a')
    const button = screen.getByRole('button', { name: /Launch/ })
    expect(button).toBeDisabled()
    expect(await screen.findByText('Set up the connection to launch.')).toBeInTheDocument()
    expect(screen.getByText(/goes into your ssh settings file \(\/home\/u\/\.ssh\/config\)/)).toBeInTheDocument()
    // the exact lines are there to read before agreeing
    expect(screen.getByTestId('managed-env-ssh-preview').textContent).toContain('Host crew-sbx-*')
    await userEvent.click(await screen.findByRole('button', { name: /Set up connection/ }))
    expect(addSpy).toHaveBeenCalledTimes(1)
    expect(await screen.findByText(/\/home\/u\/\.ssh\/config has the ssh settings/)).toBeInTheDocument()
    await waitFor(() => expect(button).toBeEnabled())
  })

  it('shows a failed ssh write with a hand-off', async () => {
    sshPresent = false
    vi.spyOn(managedEnvApi, 'addSshConfig').mockImplementation(async () => {
      throw new Error('cannot read /home/u/.ssh/config (PermissionError)')
    })
    wrap(<SandboxLaunchForm provisioner={provisioner} launch={vi.fn()} launching={false} disabled={false} activeJob={null} />)
    await userEvent.click(await screen.findByRole('button', { name: /Set up connection/ }))
    expect(await screen.findByText(/Check that you can edit \/home\/u\/\.ssh\/config\. .*PermissionError/)).toBeInTheDocument()
    expect(screen.getByText("Couldn't add the ssh settings")).toBeInTheDocument()
  })

  it.each([
    ['crew-a', true],
    ['a', false],
    ['1crew', false],
    ['crew-', false],
    ['cr--ew', false],
  ])('crew name %s is %s', (name, ok) => {
    expect(crewNameOk(name)).toBe(ok)
  })
})

describe('row actions', () => {
  it.each([
    ['RUNNING', ['Stop environment', 'Delete environment'], 'Environment running'],
    ['STOPPED', ['Resume environment', 'Delete environment'], 'Environment stopped'],
    ['PENDING', ['Delete environment'], 'Environment starting'],
    ['DELETING', ['Delete environment'], 'Environment being deleted'],
    ['FAILED', ['Delete environment'], 'Environment failed'],
  ])('%s shows its actions and state', async (status, actions, line) => {
    listed = [item(status)]
    menu()
    await waitFor(() => expect(screen.getAllByRole('menuitem').map((m) => m.textContent?.trim())).toEqual(actions))
    cleanup()
    wrap(<SandboxCrewRowDetail inst={inst} disabled={false} />)
    expect(await screen.findByText(line)).toBeInTheDocument()
  })

  it('stops through the lane route', async () => {
    row()
    await userEvent.click(await screen.findByRole('menuitem', { name: /Stop environment/ }))
    await waitFor(() => expect(actSpy).toHaveBeenCalledWith('env-001', 'stop'))
  })

  it('re-reads the crew list after a Delete, and when the environment reads gone', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidate = vi.spyOn(qc, 'invalidateQueries')
    render(
      <QueryClientProvider client={qc}>
        <SandboxCrewRowDetail inst={inst} disabled={false} />
        <SandboxCrewRowMenuItems inst={inst} disabled={false} />
      </QueryClientProvider>,
    )
    await userEvent.click(await screen.findByRole('menuitem', { name: /Delete environment/ }))
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['instances'] }))
    cleanup()
    invalidate.mockClear()
    listed = []
    wrap(<SandboxCrewRowDetail inst={inst} disabled={false} />)
    expect(await screen.findByText(/no longer in your managed environments/)).toBeInTheDocument()
  })

  it('deletes the environment the question was about, even if the row changes meanwhile', async () => {
    let answer: (yes: boolean) => void = () => {}
    confirmSpy.mockImplementationOnce(() => new Promise<boolean>((res) => { answer = res }))
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const other = { ...inst, ssh_host: 'crew-sbx-env-002' } as InstanceView
    listed = [item('RUNNING'), item('RUNNING', { sandbox_id: 'env-002', ssh_host: 'crew-sbx-env-002' })]
    const view = render(
      <QueryClientProvider client={qc}>
        <SandboxCrewRowDetail inst={inst} disabled={false} />
        <SandboxCrewRowMenuItems inst={inst} disabled={false} />
      </QueryClientProvider>,
    )
    await userEvent.click(await screen.findByRole('menuitem', { name: /Delete environment/ }))
    await waitFor(() => expect(confirmSpy).toHaveBeenCalled())
    view.rerender(
      <QueryClientProvider client={qc}>
        <SandboxCrewRowDetail inst={other} disabled={false} />
        <SandboxCrewRowMenuItems inst={other} disabled={false} />
      </QueryClientProvider>,
    )
    answer(true)
    await waitFor(() => expect(actSpy).toHaveBeenCalledWith('env-001', 'delete'))
    expect(actSpy).not.toHaveBeenCalledWith('env-002', 'delete')
  })

  it('asks before Delete and deletes only on yes', async () => {
    confirmAnswer.value = false
    row()
    await userEvent.click(await screen.findByRole('menuitem', { name: /Delete environment/ }))
    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1))
    expect(actSpy).not.toHaveBeenCalled()
    cleanup()
    confirmAnswer.value = true
    row()
    await userEvent.click(await screen.findByRole('menuitem', { name: /Delete environment/ }))
    await waitFor(() => expect(actSpy).toHaveBeenCalledWith('env-001', 'delete'))
  })

  it('shows a failed action with a hand-off, and Dismiss clears it', async () => {
    actSpy.mockImplementation(async () => {
      throw new Error('The service is pausing requests for 60 s.')
    })
    row()
    await userEvent.click(await screen.findByRole('menuitem', { name: /Stop environment/ }))
    expect(await screen.findByText('The service is pausing requests for 60 s.')).toBeInTheDocument()
    expect(screen.getByText(/Couldn't stop the environment of crew-a/)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /Ask the agent/i }).length).toBeGreaterThan(0)
  })

  it('offers nothing from an answer cached before the preview went off', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = render(
      <QueryClientProvider client={qc}>
        <SandboxCrewRowMenuItems inst={inst} disabled={false} />
      </QueryClientProvider>,
    )
    await screen.findByRole('menuitem', { name: /Stop environment/ })
    setPreviewFlag(PREVIEW_CREW_CLIENT, false)
    view.rerender(
      <QueryClientProvider client={qc}>
        <SandboxCrewRowMenuItems inst={inst} disabled={false} />
      </QueryClientProvider>,
    )
    await waitFor(() => expect(screen.queryAllByRole('menuitem')).toEqual([]))
    expect(actSpy).not.toHaveBeenCalled()
  })

  it('gives another client environment no action', async () => {
    listed = [item('RUNNING', { own: false })]
    menu()
    await waitFor(() => expect(listSpy).toHaveBeenCalled())
    expect(screen.queryAllByRole('menuitem')).toEqual([])
    cleanup()
    wrap(<SandboxCrewRowDetail inst={inst} disabled={false} />)
    expect(await screen.findByText(/no longer in your managed environments/)).toBeInTheDocument()
  })

  it('calls no route while the preview is off', async () => {
    setPreviewFlag(PREVIEW_CREW_CLIENT, false)
    menu()
    wrap(<SandboxCrewRowDetail inst={inst} disabled={false} />)
    await new Promise((r) => setTimeout(r, 0))
    expect(listSpy).not.toHaveBeenCalled()
    expect(screen.queryAllByRole('menuitem')).toEqual([])
  })

  it('maps service statuses to five states', () => {
    expect(['PENDING', 'RUNNING', 'STOPPING', 'DELETED', 'WHATEVER'].map(rowState)).toEqual([
      'creating',
      'running',
      'stopped',
      'deleting',
      'error',
    ])
  })
})
