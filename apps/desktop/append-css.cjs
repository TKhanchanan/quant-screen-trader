const fs = require('fs');
const css = `
/* COMBINED PLATFORM CONTROL WINDOW STYLES */
.combined-workspace-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #080d18;
  color: #edf3ff;
  overflow: hidden;
}

.combined-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 24px;
  background: #0f172a;
  border-bottom: 1px solid #1e293b;
}

.combined-topbar__brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.combined-topbar__info {
  display: flex;
  align-items: center;
  gap: 24px;
  font-size: 0.8rem;
  color: #94a3b8;
}

.combined-topbar__actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.combined-content {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.combined-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 24px;
  overflow-y: auto;
}

.slot-grid-modern {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}

.slot-card-modern {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 12px;
}

.panel-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 24px;
  border-bottom: 1px solid #1e293b;
  padding-bottom: 12px;
  overflow-x: auto;
}

.panel-tab {
  padding: 8px 16px;
  background: transparent;
  color: #94a3b8;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

.panel-tab:hover {
  background: #1e293b;
}

.panel-tab.active {
  background: #3b82f620;
  color: #60a5fa;
}

.panels-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 24px;
}

.dashboard-panel, .dashboard-panel-wrapper > section, .dashboard-panel-wrapper > main {
  background: #0f172a;
  border: 1px solid #1e293b;
  border-radius: 12px;
  padding: 16px;
  height: 100%;
}

.dashboard-panel__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.dashboard-panel__header h3 {
  margin: 0;
  font-size: 0.9rem;
}

.combined-sidebar {
  width: 320px;
  background: #0f172a;
  border-left: 1px solid #1e293b;
  padding: 24px 16px;
  overflow-y: auto;
}

.sidebar-section {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 16px;
}

.sidebar-section__header {
  font-size: 0.85rem;
  font-weight: 600;
  color: #60a5fa;
  margin-bottom: 12px;
}

.sidebar-button {
  background: #334155;
  color: #f8fafc;
  border: none;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 0.75rem;
  cursor: pointer;
}

.sidebar-button:hover {
  background: #475569;
}

.status-badge {
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 0.65rem;
  font-weight: bold;
}
.status-badge--online { background: #064e3b; color: #34d399; }
.status-badge--paper { background: #1e3a8a; color: #60a5fa; }
.status-badge--shadow { background: #78350f; color: #fbbf24; }
.status-badge--noevidence { background: #334155; color: #94a3b8; }

.trading-controls-wrapper section, .trading-controls-wrapper main, .trading-controls-wrapper div.trading-controls {
  margin: 0 !important;
  padding: 0 !important;
  background: transparent !important;
  border: none !important;
}
`;
fs.appendFileSync('/Users/thanyanan/Downloads/quant/apps/desktop/src/renderer/src/styles.css', css);
