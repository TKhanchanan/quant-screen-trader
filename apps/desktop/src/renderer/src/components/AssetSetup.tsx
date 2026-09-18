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
    <div className="toolbar"><h2>ตั้งค่าสินทรัพย์</h2><button onClick={onClose}>ปิด</button></div>
    <p>ชื่อเหล่านี้ใช้ระบุกราฟเท่านั้น กรุณาเข้าสู่ระบบและเลือกสินทรัพย์บนแพลตฟอร์มด้วยตนเอง</p>
    <fieldset disabled={busy}>
      <table className="asset-table"><thead><tr><th>ช่อง</th><th>เปิดใช้</th><th>การระบุสินทรัพย์</th><th>ชื่อสินทรัพย์</th><th>ชื่อแสดงผล (ไม่บังคับ)</th></tr></thead>
        <tbody>{slots.map((slot) => <tr key={slot.id}><th>{slot.id}</th>
          <td><input aria-label={`Enable slot ${slot.id}`} type="checkbox" checked={slot.enabled} onChange={(e) => edit(slot.id, { enabled: e.target.checked, assetMode: 'MANUAL' })} /></td>
          <td><select aria-label={`Slot ${slot.id} asset mode`} value={slot.assetMode ?? 'AUTO'} onChange={e => edit(slot.id, { assetMode: e.target.value as 'AUTO' | 'MANUAL' })}><option value="AUTO">อัตโนมัติ</option><option value="MANUAL">กำหนดเอง / ล็อก</option></select></td>
          <td><input aria-label={`Slot ${slot.id} asset`} maxLength={120} value={slot.assetName} onChange={(e) => edit(slot.id, { assetName: e.target.value, assetMode: 'MANUAL' })} /></td>
          <td><input aria-label={`Slot ${slot.id} display name`} maxLength={120} value={slot.displayName ?? ''} onChange={(e) => edit(slot.id, { displayName: e.target.value })} /></td>
        </tr>)}</tbody></table>
      {!valid && <p role="alert">กรุณาระบุชื่อสินทรัพย์ให้ครบทุกช่องที่เปิดใช้</p>}
      <button disabled={!valid} onClick={() => void execute({ operation: 'slots', platform, slots }).then((result) => { if (result) onClose() })}>บันทึกสินทรัพย์</button>
      <h3>ชุดสินทรัพย์ที่บันทึกไว้</h3>
      <div className="toolbar">
        <select aria-label="Asset preset" value={selected} onChange={(e) => { setSelected(e.target.value); setName(data?.presets.find((p) => p.id === e.target.value)?.name ?? ''); setConfirmDelete(false) }}>
          <option value="">สร้างชุดใหม่</option>{data?.presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <input aria-label="ชื่อชุดสินทรัพย์" placeholder="ชื่อชุดสินทรัพย์" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
        <button disabled={!name.trim() || !valid} onClick={() => void savePreset(false)}>สร้างชุดสินทรัพย์</button>
        <button disabled={!preset || !name.trim() || !valid} onClick={() => void savePreset(true)}>บันทึกชุดสินทรัพย์</button>
        <button disabled={!preset || !name.trim()} onClick={() => { if (preset) void savePreset(true, preset.slots) }}>เปลี่ยนชื่อ</button>
        <button disabled={!preset || !name.trim()} onClick={() => { if (preset) void savePreset(false, preset.slots) }}>ทำสำเนา</button>
        <button disabled={!preset} onClick={() => { if (preset) void execute({ operation: 'loadPreset', platform, id: preset.id }).then((r) => { if (r) setSlots(r.configuration.slots) }) }}>โหลดชุดนี้ (แทนค่าปัจจุบัน)</button>
        <button disabled={!preset} onClick={() => setConfirmDelete(true)}>ลบ</button>
        {confirmDelete && preset && <button onClick={() => void execute({ operation: 'deletePreset', platform, id: preset.id }).then((r) => { if (r) { setSelected(''); setConfirmDelete(false) } })}>ยืนยันลบชุดสินทรัพย์</button>}
      </div>
    </fieldset>
  </section>
}
