import { useState, type JSX } from 'react'
import { defaultCalibration, type Platform } from '@quant-screen-trader/shared-types'
import { useWorkspaceStore } from '../state/workspaceStore'

export function CalibrationControls({ platform, onClose, onError }: {
  platform: Platform; onClose: () => void; onError: (message: string) => void
}): JSX.Element {
  const { data, execute, busy } = useWorkspaceStore()
  const [selected, setSelected] = useState(data?.activeCalibrationId ?? '')
  const [name, setName] = useState(data?.calibrations.find((p) => p.id === selected)?.name ?? 'Default 3×3')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const profile = data?.calibrations.find((p) => p.id === selected)
  const run = (action: () => Promise<void>): void => { void action().catch(() => onError('Calibration operation failed. Your saved profile is unchanged.')) }
  const begin = async (reset = false): Promise<void> => {
    if (!data) return
    await window.quantScreenTrader.platformCommand({ operation: 'beginCalibration', platform,
      draft: { assets: data.configuration, slots: reset ? defaultCalibration() : profile?.slots ?? defaultCalibration(), zoomFactor: profile?.zoomFactor ?? 1 } })
  }
  const save = async (update: boolean, renameOnly = false): Promise<void> => {
    const snapshot = await window.quantScreenTrader.platformCommand({ operation: 'state', platform })
    if (!snapshot.draft) throw new Error('No calibration draft')
    const result = await execute({ operation: 'saveCalibration', platform, name,
      ...(update && profile ? { id: profile.id } : {}),
      slots: renameOnly && profile ? profile.slots : snapshot.draft.slots,
      referenceBrowserWidth: renameOnly && profile ? profile.referenceBrowserWidth : snapshot.bounds.width,
      referenceBrowserHeight: renameOnly && profile ? profile.referenceBrowserHeight : snapshot.bounds.height,
      zoomFactor: snapshot.draft.zoomFactor })
    if (result) { setSelected(result.activeCalibrationId ?? ''); if (!renameOnly) onClose() }
  }
  return <fieldset className="calibration-controls" disabled={busy}>
    <div className="toolbar">
      <select aria-label="Calibration profile" value={selected} onChange={(e) => { setSelected(e.target.value); setName(data?.calibrations.find((p) => p.id === e.target.value)?.name ?? 'New calibration'); setConfirmDelete(false) }}>
        <option value="">New profile</option>{data?.calibrations.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
      <input aria-label="Calibration name" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
      <button disabled={!name.trim()} onClick={() => run(() => save(false))}>Create profile</button>
      <button disabled={!profile || !name.trim()} onClick={() => run(() => save(true))}>Save calibration</button>
      <button disabled={!profile} onClick={() => run(async () => { if (profile && await execute({ operation: 'loadCalibration', platform, id: profile.id })) await begin() })}>Load profile</button>
      <button disabled={!profile || !name.trim()} onClick={() => run(() => save(true, true))}>Rename</button>
      <button disabled={!profile || !name.trim()} onClick={() => run(() => save(false, true))}>Duplicate</button>
      <button disabled={!profile} onClick={() => setConfirmDelete(true)}>Delete</button>
      {confirmDelete && profile && <button onClick={() => run(async () => { if (await execute({ operation: 'deleteCalibration', platform, id: profile.id })) { setSelected(''); setConfirmDelete(false) } })}>Confirm delete profile</button>}
      <button onClick={() => run(() => begin(true))}>Reset 3×3</button>
      <button onClick={onClose}>Cancel / Close</button>
    </div>
    <p>Drag the labeled handles; resize from ↘. Arrow keys move; Shift+arrows resize. Save explicitly; Cancel discards unsaved geometry.</p>
  </fieldset>
}
