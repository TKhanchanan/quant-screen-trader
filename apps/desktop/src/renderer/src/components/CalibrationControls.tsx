import { useState, type JSX } from 'react'
import { defaultCalibration, type Platform } from '@quant-screen-trader/shared-types'
import { useWorkspaceStore } from '../state/workspaceStore'

export function CalibrationControls({ platform, onClose, onError }: {
  platform: Platform; onClose: () => void; onError: (message: string) => void
}): JSX.Element {
  const { data, execute, busy } = useWorkspaceStore()
  const [selected, setSelected] = useState(data?.activeCalibrationId ?? '')
  const [name, setName] = useState(data?.calibrations.find((p) => p.id === selected)?.name ?? 'Chart Area 3×3')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const profile = data?.calibrations.find((p) => p.id === selected)
  const run = (action: () => Promise<void>): void => { void action().catch(() => onError('ปรับพื้นที่ไม่สำเร็จ โปรไฟล์ที่บันทึกไว้ยังอยู่')) }
  const begin = async (reset = false): Promise<void> => {
    if (!data) return
    const browser = await window.quantScreenTrader.platformCommand({ operation: 'state', platform })
    await window.quantScreenTrader.platformCommand({ operation: 'beginCalibration', platform,
      draft: { assets: data.configuration, slots: reset ? defaultCalibration(platform) : profile?.slots ?? defaultCalibration(platform), zoomFactor: browser.zoomFactor } })
  }
  const save = async (update: boolean, renameOnly = false): Promise<void> => {
    const snapshot = await window.quantScreenTrader.platformCommand({ operation: 'state', platform })
    if (!snapshot.draft) throw new Error('ยังไม่มีพื้นที่กราฟที่ปรับไว้')
    const result = await execute({ operation: 'saveCalibration', platform, name,
      ...(update && profile ? { id: profile.id } : {}),
      slots: renameOnly && profile ? profile.slots : snapshot.draft.slots,
      referenceBrowserWidth: renameOnly && profile ? profile.referenceBrowserWidth : snapshot.bounds.width,
      referenceBrowserHeight: renameOnly && profile ? profile.referenceBrowserHeight : snapshot.bounds.height,
      geometrySource: renameOnly ? profile?.geometrySource ?? 'MANUAL' : 'MANUAL',
      zoomFactor: renameOnly && profile ? profile.zoomFactor : snapshot.zoomFactor })
    if (result) { setSelected(result.activeCalibrationId ?? ''); if (!renameOnly) onClose() }
  }
  return <fieldset className="calibration-controls" disabled={busy}>
    <div className="toolbar">
      <select aria-label="Calibration profile" value={selected} onChange={(e) => { setSelected(e.target.value); setName(data?.calibrations.find((p) => p.id === e.target.value)?.name ?? 'พื้นที่กราฟใหม่'); setConfirmDelete(false) }}>
        <option value="">สร้างโปรไฟล์ใหม่</option>{data?.calibrations.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
      <input aria-label="Calibration name" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
      <button disabled={!name.trim()} onClick={() => run(() => save(false))}>สร้างโปรไฟล์</button>
      <button disabled={!profile || !name.trim()} onClick={() => run(() => save(true))}>บันทึกพื้นที่กราฟ</button>
      <button disabled={!profile} onClick={() => run(async () => { if (profile && await execute({ operation: 'loadCalibration', platform, id: profile.id })) await begin() })}>โหลดโปรไฟล์</button>
      <button disabled={!profile || !name.trim()} onClick={() => run(() => save(true, true))}>เปลี่ยนชื่อ</button>
      <button disabled={!profile || !name.trim()} onClick={() => run(() => save(false, true))}>ทำสำเนา</button>
      <button disabled={!profile} onClick={() => setConfirmDelete(true)}>ลบ</button>
      {confirmDelete && profile && <button onClick={() => run(async () => { if (await execute({ operation: 'deleteCalibration', platform, id: profile.id })) { setSelected(''); setConfirmDelete(false) } })}>ยืนยันลบโปรไฟล์</button>}
      <button onClick={() => run(() => begin(true))}>รีเซ็ตพื้นที่กราฟอัตโนมัติ</button>
      <button onClick={onClose}>ปิด</button>
    </div>
    <p>ลากกรอบให้ครอบพื้นที่กราฟ 3×3 ระบบจะแบ่งช่อง 1–9 ตามแถวโดยอัตโนมัติ ใช้ปุ่มลูกศรเพื่อเลื่อน และ Shift + ลูกศรเพื่อปรับขนาด</p>
  </fieldset>
}
