/**
 * The "Managed environment" launch form on Remote Crew's setup tab.
 *
 * The panel draws it only when the server lists the `kiro_managed_env` provisioner, which
 * it does only while the `crew_client` feature preview is on. The form asks for the crew
 * name and starts the core's launch; the server's own preflight checks every setting, the
 * sign-in and the service, and the panel shows the progress card and any error. Launch is
 * held until this computer's ssh config has the lane's block, which one button adds.
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Link2, Rocket } from 'lucide-react'
import { Btn, Input } from '../ui'
import ErrorNotice from '../ErrorNotice'
import type { RemoteProvisionerFormProps } from '../remoteProvisionerRenderers'
import { managedEnvApi } from '../../api/managedEnvApi'
import { i18nT } from '../../i18n/t'

const SSH_CONFIG_KEY = ['managed-env-ssh-config'] as const

/** The ssh block's state and the button that writes it. */
function SshSettings({ disabled }: { disabled: boolean }) {
  const qc = useQueryClient()
  const state = useQuery({ queryKey: SSH_CONFIG_KEY, queryFn: managedEnvApi.sshConfig, retry: false })
  const add = useMutation({
    mutationFn: managedEnvApi.addSshConfig,
    onSuccess: (data) => qc.setQueryData(SSH_CONFIG_KEY, data),
  })
  const failure = add.error ?? state.error
  const box = useRef<HTMLDivElement>(null)
  // The form sits in a scrolling dialog; a failure below the fold would go unseen.
  useEffect(() => {
    if (failure) box.current?.scrollIntoView?.({ block: 'nearest' })
  }, [failure])
  return (
    <div className="space-y-2" ref={box}>
      {state.data?.present ? (
        <p className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]" data-testid="managed-env-ssh-ready">
          <Check size={14} aria-hidden="true" className="text-ok" />
          {i18nT('managedEnv.form.ssh_ready', { path: state.data.path })}
        </p>
      ) : (
        <>
          <p className="text-xs text-[var(--text-secondary)]">
            {i18nT('managedEnv.form.ssh_config_note', { path: state.data?.path ?? '~/.ssh/config' })}
          </p>
          {state.data?.block && (
            <details className="text-xs text-[var(--text-secondary)]" data-testid="managed-env-ssh-preview">
              <summary className="cursor-pointer">{i18nT('managedEnv.form.ssh_preview')}</summary>
              <pre className="mt-1 whitespace-pre-wrap break-all rounded-md bg-bg-elevated p-2 font-mono text-[11px] text-text">
                {state.data.block}
              </pre>
              <p className="mt-1">{i18nT('managedEnv.form.ssh_preview_why', { hosts: 'crew-sbx-*' })}</p>
            </details>
          )}
          <Btn type="button" busy={add.isPending} disabled={disabled || add.isPending || !state.isSuccess} onClick={() => add.mutate()}>
            <Link2 size={14} aria-hidden="true" /> {i18nT('managedEnv.form.ssh_add')}
          </Btn>
        </>
      )}
      {failure && (
        <ErrorNotice
          askAgent
          title={i18nT('managedEnv.form.ssh_failed')}
          message={`${i18nT('managedEnv.form.ssh_failed_hint', { path: state.data?.path ?? '~/.ssh/config' })} ${(failure as Error).message}`}
          onDismiss={() => {
            add.reset()
            void state.refetch()
          }}
        />
      )}
    </div>
  )
}

/** Whether the ssh block is in place; a launch without it is refused before anything is made. */
function useSshReady(): boolean {
  return useQuery({ queryKey: SSH_CONFIG_KEY, queryFn: managedEnvApi.sshConfig, retry: false }).data?.present === true
}

export const SANDBOX_PROVISIONER_KIND = 'kiro_managed_env'

/** The crew-name rule the server's engine applies. */
const CREW_NAME = /^[a-z][a-z0-9-]{0,30}[a-z0-9]$/

export function crewNameOk(name: string): boolean {
  return CREW_NAME.test(name) && !name.includes('--')
}

export default function SandboxLaunchForm({ launch, launching, disabled }: RemoteProvisionerFormProps) {
  const [name, setName] = useState('')
  const ok = crewNameOk(name)
  const sshReady = useSshReady()
  const showHint = name !== '' && !ok
  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault()
        if (ok && sshReady && !launching && !disabled) launch({ profile: name, size_key: '' })
      }}
    >
      <p className="text-sm text-[var(--text-secondary)]">{i18nT('managedEnv.form.intro')}</p>
      <label className="block text-sm">
        <span className="mb-1 block font-medium">{i18nT('managedEnv.form.name_label')}</span>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value.trim().toLowerCase())}
          placeholder={i18nT('managedEnv.form.name_placeholder')}
          aria-invalid={showHint}
          aria-describedby="managed-env-name-hint"
          className="aria-invalid:border-danger aria-invalid:[--ring:var(--danger)]"
          disabled={disabled || launching}
        />
      </label>
      <p id="managed-env-name-hint" className={`text-xs ${showHint ? 'text-danger' : 'text-[var(--text-secondary)]'}`}>
        {showHint ? i18nT('managedEnv.form.name_rule') : i18nT('managedEnv.form.name_help')}
      </p>
      <SshSettings disabled={disabled || launching} />
      <div className="flex flex-wrap items-center gap-3">
        <Btn type="submit" primary busy={launching} disabled={!ok || !sshReady || disabled || launching} aria-describedby={!ok || !sshReady ? 'managed-env-launch-why' : undefined}>
          <Rocket size={14} aria-hidden="true" /> {i18nT('managedEnv.form.launch')}
        </Btn>
        {(!ok || !sshReady) && (
          <span id="managed-env-launch-why" className="text-xs text-[var(--text-secondary)]">
            {name === ''
              ? i18nT('managedEnv.form.launch_needs_name')
              : !ok
                ? i18nT('managedEnv.form.launch_fix_name')
                : i18nT('managedEnv.form.launch_needs_ssh')}
          </span>
        )}
      </div>
    </form>
  )
}
