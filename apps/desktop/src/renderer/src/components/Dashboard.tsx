import { useState, type JSX } from 'react'
import type { Platform } from '@quant-screen-trader/shared-types'
import { PLATFORMS, PLATFORM_DETAILS } from '../platforms'
import { useAppStore } from '../state/appStore'
import { EngineStatus } from './EngineStatus'

export function Dashboard(): JSX.Element {
  const engineHealth = useAppStore((state) => state.engineHealth)
  const [actionError, setActionError] = useState<string | null>(null)
  const [opening, setOpening] = useState<Platform | null>(null)
  const openWorkspace = async (platform: Platform): Promise<void> => {
    setOpening(platform)
    setActionError(null)
    try { await window.quantScreenTrader.openWorkspace(platform) }
    catch { setActionError(`เปิดพื้นที่ทำงาน ${PLATFORM_DETAILS[platform].name} ไม่สำเร็จ กรุณาลองอีกครั้ง`) }
    finally { setOpening(null) }
  }
  return <div className="app-layout">
    <aside className="app-sidebar">
      <div className="app-sidebar__brand"><div className="app-logo">Q</div><div className="app-name">QuantScreen<span>พื้นที่วิเคราะห์ตลาด</span></div></div>
      <nav className="app-sidebar__nav" aria-label="เมนูหลัก">
        <a className="nav-item" href="#workspaces">พื้นที่ทำงาน <span>01</span></a>
        <a className="nav-item" href="#system-health">สถานะระบบ <span>02</span></a>
        <a className="nav-item" href="#getting-started">เริ่มต้นใช้งาน <span>03</span></a>
      </nav>
      <div className="sidebar-note">พื้นที่ส่วนตัวของคุณ<br /><small>ประมวลผลบนเครื่องนี้</small></div>
    </aside>
    <main className="app-main">
      <header className="page-header"><div><p className="eyebrow">QUANTSCREEN / ภาพรวม</p><h1>มองตลาดได้ชัดขึ้น</h1><p className="page-subtitle">เลือกแพลตฟอร์ม แล้วเริ่มวิเคราะห์ในพื้นที่ทำงานของคุณ</p></div><EngineStatus health={engineHealth} /></header>
      <section id="workspaces" aria-labelledby="workspace-title">
        <div className="section-heading"><h2 id="workspace-title">พื้นที่ทำงาน</h2><span>2 แพลตฟอร์ม · แยกเซสชันอิสระ</span></div>
        <div className="platform-grid">{PLATFORMS.map(platform => {
          const details = PLATFORM_DETAILS[platform]
          return <article className="platform-card" key={platform}>
            <div className="platform-card__title"><div className={`platform-mark platform-mark--${platform}`} aria-hidden="true">{details.shortName}</div><div><h2>{details.name}</h2><p className="page-subtitle">เข้าสู่ระบบด้วยบัญชีของคุณ</p></div></div>
            <dl className="metric-grid"><div><dt>ช่องกราฟที่รองรับ</dt><dd>9 ช่อง</dd></div><div><dt>พื้นที่ทำงาน</dt><dd>เซสชันแยก</dd></div></dl>
            <p className="card-hint">ดูสินทรัพย์ ติดตามสัญญาณ และจัดการการทำงานได้ในที่เดียว</p>
            <button className="primary-button" disabled={opening !== null} onClick={() => void openWorkspace(platform)}>{opening === platform ? 'กำลังเปิด…' : `เปิดพื้นที่ทำงาน ${details.name}`} <span aria-hidden="true">↗</span></button>
          </article>
        })}</div>
      </section>
      {actionError && <p role="alert" className="error-banner">{actionError}</p>}
      <section id="system-health" className="health-panel" aria-labelledby="system-health-title">
        <div className="health-panel__heading"><div><p className="eyebrow">การเชื่อมต่อ</p><h2 id="system-health-title">สถานะระบบ</h2></div><EngineStatus health={engineHealth} /></div>
        <dl className="health-grid"><div><dt>ระบบวิเคราะห์</dt><dd>{engineHealth.engine?.version ?? 'ยังไม่เชื่อมต่อ'}</dd></div><div><dt>ฐานข้อมูล</dt><dd>{engineHealth.engine?.database ?? 'ยังไม่มีข้อมูล'}</dd></div><div><dt>เวลาตอบสนอง</dt><dd>{engineHealth.latencyMs === undefined ? '—' : `${engineHealth.latencyMs} ms`}</dd></div><div><dt>อัปเดตล่าสุด</dt><dd>{engineHealth.engine?.timestamp ? new Date(engineHealth.engine.timestamp).toLocaleTimeString('th-TH') : 'รอข้อมูล'}</dd></div></dl>
        {engineHealth.message && <details className="health-message"><summary>รายละเอียดการเชื่อมต่อ</summary><p>{engineHealth.message}</p></details>}
      </section>
      <section id="getting-started" className="getting-started" aria-labelledby="guide-title"><p className="eyebrow">เริ่มต้นใน 3 ขั้นตอน</p><h2 id="guide-title">พร้อมเมื่อคุณพร้อม</h2><ol className="guide-grid"><li><b>เปิดพื้นที่ทำงาน</b><p>เข้าสู่ระบบโบรกเกอร์และเลือกกราฟที่ต้องการด้วยตนเอง</p></li><li><b>เตรียมข้อมูล</b><p>ซิงก์สินทรัพย์และปรับพื้นที่อ่านกราฟให้ตรงกับหน้าจอ</p></li><li><b>เริ่มติดตามตลาด</b><p>เริ่มสังเกตการณ์ แล้วดูสัญญาณและผลจำลองก่อนเลือกโหมดส่งคำสั่ง</p></li></ol></section>
    </main>
  </div>
}
