import { useState, type JSX } from 'react'
import { SlotConfigurationSchema, type Platform, type PlatformSlot } from '@quant-screen-trader/shared-types'
import { useWorkspaceStore } from '../state/workspaceStore'

export function AssetSetup({ platform, initialSlots, onClose }: {
  platform: Platform; initialSlots: PlatformSlot[]; onClose: () => void
}): JSX.Element {
  const { data, busy, execute } = useWorkspaceStore()
  const [slots, setSlots] = useState(initialSlots)
  const [selected, setSelected] = useState('')
  const [name, setName] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const valid = SlotConfigurationSchema.safeParse({ platform, slots }).success
  const preset = data?.presets.find((p) => p.id === selected)
  const edit = (id: number, patch: Partial<PlatformSlot>): void =>
    setSlots(slots.map((s) => s.id === id ? { ...s, ...patch } : s))
  const savePreset = async (update: boolean, source = slots): Promise<void> => {
    const result = await execute({ operation: 'savePreset', platform, name, slots: source,
      ...(update && preset ? { id: preset.id } : {}) })
    if (result && !update) setSelected(result.presets.find((p) => !data?.presets.some((old) => old.id === p.id))?.id ?? '')
  }
  return <section className="configuration-panel" aria-label="Asset Setup">
    <div className="toolbar"><h2>Configure assets</h2><button onClick={onClose}>Cancel / Close</button></div>
    <p>These names label charts; they do not select instruments on the platform. Login and chart selection remain manual.</p>
    <fieldset disabled={busy}>
      <table className="asset-table"><thead><tr><th>Slot</th><th>Enabled</th><th>Asset name</th><th>Display name (optional)</th></tr></thead>
        <tbody>{slots.map((slot) => <tr key={slot.id}><th>{slot.id}</th>
          <td><input aria-label={`Enable slot ${slot.id}`} type="checkbox" checked={slot.enabled} onChange={(e) => edit(slot.id, { enabled: e.target.checked })} /></td>
          <td><input aria-label={`Slot ${slot.id} asset`} maxLength={120} value={slot.assetName} onChange={(e) => edit(slot.id, { assetName: e.target.value })} /></td>
          <td><input aria-label={`Slot ${slot.id} display name`} maxLength={120} value={slot.displayName ?? ''} onChange={(e) => edit(slot.id, { displayName: e.target.value })} /></td>
        </tr>)}</tbody></table>
      {!valid && <p role="alert">Every enabled slot needs an asset name.</p>}
      <button disabled={!valid} onClick={() => void execute({ operation: 'slots', platform, slots }).then((result) => { if (result) onClose() })}>Save assets</button>
      <h3>Platform asset presets</h3>
      <div className="toolbar">
        <select aria-label="Asset preset" value={selected} onChange={(e) => { setSelected(e.target.value); setName(data?.presets.find((p) => p.id === e.target.value)?.name ?? ''); setConfirmDelete(false) }}>
          <option value="">New preset</option>{data?.presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <input aria-label="Preset name" placeholder="Preset name" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
        <button disabled={!name.trim() || !valid} onClick={() => void savePreset(false)}>Create preset</button>
        <button disabled={!preset || !name.trim() || !valid} onClick={() => void savePreset(true)}>Update preset</button>
        <button disabled={!preset || !name.trim()} onClick={() => { if (preset) void savePreset(true, preset.slots) }}>Rename</button>
        <button disabled={!preset || !name.trim()} onClick={() => { if (preset) void savePreset(false, preset.slots) }}>Duplicate</button>
        <button disabled={!preset} onClick={() => { if (preset) void execute({ operation: 'loadPreset', platform, id: preset.id }).then((r) => { if (r) setSlots(r.configuration.slots) }) }}>Load preset (replaces assets)</button>
        <button disabled={!preset} onClick={() => setConfirmDelete(true)}>Delete</button>
        {confirmDelete && preset && <button onClick={() => void execute({ operation: 'deletePreset', platform, id: preset.id }).then((r) => { if (r) { setSelected(''); setConfirmDelete(false) } })}>Confirm delete preset</button>}
      </div>
    </fieldset>
  </section>
}
