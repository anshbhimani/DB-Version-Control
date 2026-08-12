let checkpoints = [];
let selectedTimestamp = null;
let pendingForce = false;

const checkpointListEl = document.getElementById('checkpointList');
const detailPanelEl = document.getElementById('detailPanel');
const captureStatusEl = document.getElementById('captureStatus');
const captureToggleEl = document.getElementById('captureToggle');
const logPanelEl = document.getElementById('logPanel');
const logToggleEl = document.getElementById('logToggle');
const refreshBtnEl = document.getElementById('refreshBtn');
const overlayEl = document.getElementById('confirmOverlay');
const confirmWarningTextEl = document.getElementById('confirmWarningText');
const ackCheckboxEl = document.getElementById('ackCheckbox');
const confirmProceedEl = document.getElementById('confirmProceed');
const confirmCancelEl = document.getElementById('confirmCancel');

function fmtDate(iso) {
  if (!iso) return 'unknown time';
  const d = new Date(iso.replace(' ', 'T'));
  return isNaN(d) ? iso : d.toLocaleString();
}

async function loadCheckpoints() {
  const result = await window.dvc.listCheckpoints();
  if (result.error) {
    checkpointListEl.innerHTML = `<p class="empty-state">Error loading checkpoints: ${result.error}</p>`;
    return;
  }
  checkpoints = result.checkpoints;
  renderCheckpointList();
}

function renderCheckpointList() {
  if (!checkpoints.length) {
    checkpointListEl.innerHTML = '<p class="empty-state">No checkpoints yet. Start capture to begin recording schema snapshots.</p>';
    return;
  }
  checkpointListEl.innerHTML = '';
  for (const cp of checkpoints) {
    const item = document.createElement('div');
    item.className = 'checkpoint-item' + (cp.timestamp === selectedTimestamp ? ' selected' : '');
    item.innerHTML = `
      <div class="cp-time">${fmtDate(cp.date || cp.timestamp)}</div>
      <div class="cp-message">${cp.message || cp.timestamp}</div>
      <div class="cp-badges">
        ${cp.schema_snapshot_path ? '<span class="badge">schema</span>' : ''}
        ${cp.baseline_dump_path ? '<span class="badge">data baseline</span>' : ''}
        ${cp.privilege_snapshot_path ? '<span class="badge">privileges</span>' : ''}
      </div>
    `;
    item.addEventListener('click', () => selectCheckpoint(cp.timestamp));
    checkpointListEl.appendChild(item);
  }
}

function renderStatements(title, statements) {
  const empty = !statements || statements.length === 0;
  return `
    <div class="plan-section ${empty ? 'empty' : ''}">
      <h3>${title} (${statements ? statements.length : 0})</h3>
      <pre>${empty ? 'none' : statements.join('\n')}</pre>
    </div>
  `;
}

async function selectCheckpoint(timestamp) {
  selectedTimestamp = timestamp;
  renderCheckpointList();
  detailPanelEl.innerHTML = '<p class="empty-state">Computing revert plan…</p>';

  const cp = checkpoints.find((c) => c.timestamp === timestamp);
  const result = await window.dvc.getRevertPlan(timestamp);
  if (result.error) {
    detailPanelEl.innerHTML = `<p class="empty-state">Error computing plan: ${result.error}</p>`;
    return;
  }
  renderPlan(timestamp, result.plan, cp ? cp.migration_sql : null);
}

function renderPlan(timestamp, plan, migrationSql) {
  const dataLossNote = plan.tables_dropped > 0
    ? `${plan.tables_dropped} table(s) will be recreated from the data baseline + changelog replay for this point in time.`
    : 'No tables need to be dropped -- all changes are in-place alterations.';

  const isNoOp = plan.fk_drops.length + plan.drop_tables.length + plan.create_tables.length
    + plan.alters.length + plan.fk_adds.length === 0;

  let html = '';

  html += `
    <div class="plan-section">
      <h3>What changed in this checkpoint</h3>
      <pre>${migrationSql ? migrationSql.trim() : 'No migration recorded for this checkpoint (likely the first-ever snapshot).'}</pre>
    </div>
  `;

  html += `
    <div class="summary-banner">
      <strong>Revert plan: current live state → ${fmtDate(timestamp)}</strong><br>
      ${isNoOp ? 'This checkpoint matches the current live state -- reverting to it is a no-op.<br>' : ''}
      This is a hard reset: every checkpoint after this point will be removed from the list, and git history will be reset to here (a safety tag is kept, but they won't show up in the normal log).<br>
      ${plan.tables_altered_in_place} table(s) altered in place (no data touched, safe at any row count).<br>
      ${plan.tables_created} table(s) recreated, ${plan.tables_dropped} table(s) dropped.<br>
      ${dataLossNote}
    </div>
  `;

  if (plan.blocked) {
    html += `
      <div class="warning-box">
        <strong>⚠️ This revert is blocked</strong> -- the diff found something it can't confidently apply automatically:
        <pre>${plan.warnings.join('\n')}</pre>
        Review manually before forcing this through.
      </div>
    `;
  }

  html += renderStatements('Drop foreign keys', plan.fk_drops);
  html += renderStatements('Drop tables', plan.drop_tables);
  html += renderStatements('Create tables', plan.create_tables);
  html += renderStatements('Alter tables in place', plan.alters);
  html += renderStatements('Add foreign keys', plan.fk_adds);

  html += `<div class="revert-btn-row">`;
  if (isNoOp) {
    html += `<button disabled>Nothing to revert -- already at this state</button>`;
  } else if (plan.blocked) {
    html += `<button id="forceRevertBtn" class="danger">Force revert anyway (not recommended)</button>`;
  } else {
    html += `<button id="revertBtn" class="danger">Revert to this point</button>`;
  }
  html += `</div>`;

  detailPanelEl.innerHTML = html;

  const revertBtn = document.getElementById('revertBtn');
  if (revertBtn) revertBtn.addEventListener('click', () => openConfirm(false));
  const forceBtn = document.getElementById('forceRevertBtn');
  if (forceBtn) forceBtn.addEventListener('click', () => openConfirm(true));
}

function openConfirm(force) {
  pendingForce = force;
  ackCheckboxEl.checked = false;
  confirmProceedEl.disabled = true;
  confirmWarningTextEl.textContent = force
    ? 'This revert has unresolved warnings and may apply an incomplete or incorrect migration. This is also a hard reset: every checkpoint after this point disappears from the list and git history moves back to here (a safety tag is kept, but this is not shown in the normal log).'
    : 'This is a hard reset, like git reset --hard: every checkpoint made after this point will be removed from the list, and git history moves back to here. A safety tag is created first so nothing is permanently lost, but this action should be treated as irreversible in normal use.';
  overlayEl.classList.remove('hidden');
}

ackCheckboxEl.addEventListener('change', () => {
  confirmProceedEl.disabled = !ackCheckboxEl.checked;
});

confirmCancelEl.addEventListener('click', () => {
  overlayEl.classList.add('hidden');
});

confirmProceedEl.addEventListener('click', async () => {
  overlayEl.classList.add('hidden');
  detailPanelEl.innerHTML = '<p class="empty-state">Applying revert…</p>';
  const result = await window.dvc.applyRevert(selectedTimestamp, pendingForce);
  if (result.success) {
    detailPanelEl.innerHTML = `<p class="empty-state">✅ Revert applied.</p><pre>${result.stdout}</pre>`;
  } else {
    detailPanelEl.innerHTML = `<p class="empty-state">❌ Revert failed.</p><pre>${result.stderr || result.stdout}</pre>`;
  }
  loadCheckpoints();
});

// --- Capture controls ---

function setCaptureUI(running) {
  captureStatusEl.textContent = running ? 'Capture: Running' : 'Capture: Stopped';
  captureStatusEl.className = 'status-pill ' + (running ? 'status-running' : 'status-stopped');
  captureToggleEl.textContent = running ? 'Stop Capture' : 'Start Capture';
}

captureToggleEl.addEventListener('click', async () => {
  const status = await window.dvc.watchStatus();
  if (status.running) {
    await window.dvc.watchStop();
    setCaptureUI(false);
  } else {
    await window.dvc.watchStart();
    setCaptureUI(true);
  }
});

logToggleEl.addEventListener('click', () => {
  logPanelEl.classList.toggle('hidden');
});

window.dvc.onWatchLog((line) => {
  logPanelEl.textContent += line;
  logPanelEl.scrollTop = logPanelEl.scrollHeight;
  // A schema-change commit means new checkpoints exist -- refresh the list.
  if (line.includes('Schema change detected')) {
    loadCheckpoints();
  }
});

window.dvc.onWatchStopped(() => setCaptureUI(false));

// --- Init ---

refreshBtnEl.addEventListener('click', loadCheckpoints);

// Safety net: the checkpoint list only auto-refreshes when the UI's own
// managed watch.py process logs a schema change. If capture is running
// separately (a terminal, a cron job, another machine), this catches it too.
setInterval(loadCheckpoints, 15000);

(async function init() {
  const status = await window.dvc.watchStatus();
  setCaptureUI(status.running);
  await loadCheckpoints();
})();
