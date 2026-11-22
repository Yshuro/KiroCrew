/**
 * Stop, Resume and Delete on a Remote Crew row the managed-environment lane created.
 *
 * Drawn only on rows stamped `kiro_managed_env`, and only while the `crew_client` preview
 * is on: off, nothing here calls a route. The environment's state comes from the lane's
 * List; an environment this Crew did not create gets no action (the server refuses it
 * too). Delete asks first.
 */
import { useEffect, useState, useSyncExternalStore } from 'react'
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, Power, Trash2 } from 'lucide-react'
import { DropdownMenuItem, DropdownMenuLabel } from '../ui/dropdown-menu'
import ErrorNotice from '../ErrorNotice'
import { useConfirm } from '../ConfirmDialog'
import type { CrewRowActionProps } from '../remoteCrewRowActions'
import { managedEnvApi } from '../../api/managedEnvApi'
import type { ManagedEnvAction, ManagedEnvItem } from '../../api/managedEnvApi'
import { usePreviewFlag } from '../../hooks/usePreviewFlag'
import { PREVIEW_CREW_CLIENT, readPreviewFlag } from '../../utils/previewFlags'
import { i18nT } from '../../i18n/t'

export const SANDBOX_PROVISIONER_ID = 'kiro_managed_env'
export const MANAGED_ENV_LIST_KEY = ['managed-env-list'] as const
const LIST_EVERY_MS = 15_000

export type RowState = 'creating' | 'running' | 'stopped' | 'deleting' | 'error'

const ACTION_FAILED_KEY = {
  stop: 'managedEnv.row.stop_failed',
  resume: 'managedEnv.row.resume_failed',
  delete: 'managedEnv.row.delete_failed',
} as const satisfies Record<ManagedEnvAction, string>

const STATE_LABEL_KEY = {
  creating: 'managedEnv.row.state_creating',
  running: 'managedEnv.row.state_running',
  stopped: 'managedEnv.row.state_stopped',
  deleting: 'managedEnv.row.state_deleting',
  error: 'managedEnv.row.state_error',
} as const satisfies Record<RowState, string>

export function rowState(status: string): RowState {
  switch (status) {
    case 'PENDING':
    case 'CREATING':
      return 'creating'
    case 'RUNNING':
      return 'running'
    case 'STOPPING':
    case 'STOPPED':
      return 'stopped'
    case 'DELETING':
    case 'DELETED':
      return 'deleting'
    default:
      return 'error'
  }
}

/** The lane's List, shared by every row. While the preview is off it is not read, and an
 *  answer cached before the switch went off gives no item, so no action is offered. */
function useManagedEnvItem(sshHost: string | undefined): {
  on: boolean
  item: ManagedEnvItem | undefined
  loaded: boolean
  error: unknown
} {
  const on = usePreviewFlag(PREVIEW_CREW_CLIENT)
  const list = useQuery({
    queryKey: MANAGED_ENV_LIST_KEY,
    queryFn: managedEnvApi.list,
    enabled: on,
    refetchInterval: LIST_EVERY_MS,
    retry: false,
  })
  const item = on ? list.data?.sandboxes.find((s) => s.own && s.ssh_host === sshHost) : undefined
  return { on, item, loaded: on && list.isSuccess, error: on ? list.error : null }
}

// The core's row menu closes, and unmounts its items, on select, so an action cannot run
// (or report its failure, or ask its question) from the menu. The menu posts what was
// chosen; the row's detail block, mounted whenever the row is expanded and so whenever
// its menu exists, runs it, asks Delete's question and shows any failure.
type ActRequest = { sandboxId: string; action: ManagedEnvAction }
let pending: ActRequest | null = null
const listeners = new Set<() => void>()
function request(next: ActRequest | null) {
  pending = next
  listeners.forEach((l) => l())
}
function usePendingRequest(): ActRequest | null {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => pending,
  )
}

/** The Remote Crew panel's crew list (`RemoteCrewPanel`). */
const INSTANCES_KEY = ['instances'] as const

const actKey = (sandboxId: string) => ['managed-env-act', sandboxId] as const

export function SandboxCrewRowMenuItems({ inst, disabled }: CrewRowActionProps) {
  const { item } = useManagedEnvItem(inst.ssh_host)
  const running = useIsMutating({ mutationKey: actKey(item?.sandbox_id ?? '') }) > 0
  if (!item) return null
  const state = rowState(item.status)
  const busy = disabled || running || state === 'deleting'
  const choose = (action: ManagedEnvAction) => request({ sandboxId: item.sandbox_id, action })
  return (
    <>
      <DropdownMenuLabel>{inst.name}</DropdownMenuLabel>
      {state === 'running' && (
        <DropdownMenuItem disabled={busy} onSelect={() => choose('stop')}>
          <Power size={14} aria-hidden="true" /> {i18nT('managedEnv.row.stop')}
        </DropdownMenuItem>
      )}
      {state === 'stopped' && (
        <DropdownMenuItem disabled={busy || item.status === 'STOPPING'} onSelect={() => choose('resume')}>
          <Play size={14} aria-hidden="true" /> {i18nT('managedEnv.row.resume')}
        </DropdownMenuItem>
      )}
      <DropdownMenuItem disabled={busy} onSelect={() => choose('delete')}>
        <Trash2 size={14} aria-hidden="true" /> {i18nT('managedEnv.row.delete')}
      </DropdownMenuItem>
    </>
  )
}

export function SandboxCrewRowDetail({ inst }: CrewRowActionProps) {
  const { on, item, loaded, error } = useManagedEnvItem(inst.ssh_host)
  const [dismissed, setDismissed] = useState(false)
  const qc = useQueryClient()
  const sandboxId = item?.sandbox_id ?? ''
  const act = useMutation({
    mutationKey: actKey(sandboxId),
    // The id travels with the request: a Delete confirmed for one environment acts on
    // that one, even if the row has since been pointed at another.
    mutationFn: (req: ActRequest) => managedEnvApi.act(req.sandboxId, req.action),
    onSettled: (_data, _err, { action }) => {
      void qc.invalidateQueries({ queryKey: MANAGED_ENV_LIST_KEY })
      // A confirmed Delete drops the Remote Crew row on the server.
      if (action === 'delete') void qc.invalidateQueries({ queryKey: INSTANCES_KEY })
    },
  })
  // The server drops the row of a deleted environment once its List no longer shows it,
  // so a row that reads gone re-reads the crew list once.
  const gone = loaded && !item
  useEffect(() => {
    if (gone) void qc.invalidateQueries({ queryKey: INSTANCES_KEY })
  }, [gone, qc])
  const { confirm, confirmDialog } = useConfirm()
  const asked = usePendingRequest()
  useEffect(() => {
    if (!asked || !sandboxId || asked.sandboxId !== sandboxId) return
    request(null)
    if (!on) return
    if (asked.action !== 'delete') {
      act.mutate(asked)
      return
    }
    void confirm({
      title: i18nT('managedEnv.row.delete_title', { name: inst.name }),
      body: i18nT('managedEnv.row.delete_body'),
      confirmLabel: i18nT('managedEnv.row.delete_confirm'),
    }).then((yes) => {
      // The switch can go off while the question is open.
      if (yes && readPreviewFlag(PREVIEW_CREW_CLIENT)) act.mutate(asked)
    })
  }, [asked, sandboxId, on, confirm, act, inst.name])

  let body = null
  if (error && !dismissed) {
    body = (
      <ErrorNotice
        askAgent
        title={i18nT('managedEnv.row.list_failed')}
        message={(error as Error).message || i18nT('managedEnv.row.list_failed')}
        onDismiss={() => setDismissed(true)}
      />
    )
  } else if (loaded && !item) {
    body = <p className="text-xs text-[var(--text-secondary)]">{i18nT('managedEnv.row.gone')}</p>
  } else if (loaded && item) {
    const state = rowState(item.status)
    body = (
      <p className="text-xs text-[var(--text-secondary)]" data-state={state}>
        {i18nT(STATE_LABEL_KEY[state])}
      </p>
    )
  }
  return (
    <>
      {body}
      {act.isError && (
        <ErrorNotice
          askAgent
          title={i18nT(ACTION_FAILED_KEY[act.variables?.action ?? 'stop'], { name: inst.name })}
          message={(act.error as Error).message}
          onDismiss={() => act.reset()}
        />
      )}
      {confirmDialog}
    </>
  )
}
