const { app, BrowserWindow, ipcMain } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const repoStore = require('./repoStore');

const REPO_ROOT = path.join(__dirname, '..');
const CLI_DIR = path.join(REPO_ROOT, 'cli');

// Prefer the project's venv if present, matching how this repo has been run
// throughout development -- override with DVC_PYTHON if the setup differs.
function resolvePython() {
  if (process.env.DVC_PYTHON) return process.env.DVC_PYTHON;
  const venvPython = path.join(REPO_ROOT, 'myenv', 'bin', 'python3');
  if (fs.existsSync(venvPython)) return venvPython;
  return 'python3';
}
const PYTHON_BIN = resolvePython();

let mainWindow;
// One watch.py subprocess per repo -- switching the active repo in the UI
// doesn't have to stop capture on the one you switched away from.
const watchProcesses = new Map(); // repoId -> ChildProcess

function scriptPath(name) {
  return path.join(CLI_DIR, name);
}

function runPython(args, repo) {
  return new Promise((resolve) => {
    const env = repo ? { ...process.env, DB_URL: repo.dbUrl } : { ...process.env };
    const cwd = repo ? repo.dataDir : CLI_DIR;
    const proc = spawn(PYTHON_BIN, args, { cwd, env });
    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', (d) => { stdout += d.toString(); });
    proc.stderr.on('data', (d) => { stderr += d.toString(); });
    proc.on('close', (code) => resolve({ code, stdout, stderr }));
  });
}

function requireActiveRepo() {
  const repo = repoStore.getActiveRepo();
  if (!repo) throw new Error('No active repo -- run onboarding first.');
  return repo;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 760,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  for (const proc of watchProcesses.values()) proc.kill();
  if (process.platform !== 'darwin') app.quit();
});

// --- IPC: onboarding / repo management ---

ipcMain.handle('repos:list', () => {
  return repoStore.listRepos().map(repoStore.redact);
});

ipcMain.handle('repos:getActive', () => {
  return repoStore.redact(repoStore.getActiveRepo());
});

ipcMain.handle('repos:setActive', (_event, id) => {
  return repoStore.redact(repoStore.setActiveRepo(id));
});

ipcMain.handle('repos:listDatabases', async (_event, { host, port, user, password }) => {
  return new Promise((resolve) => {
    const proc = spawn(PYTHON_BIN, [scriptPath('list_databases.py')], { cwd: CLI_DIR });
    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', (d) => { stdout += d.toString(); });
    proc.stderr.on('data', (d) => { stderr += d.toString(); });
    proc.on('close', () => {
      try {
        resolve(JSON.parse(stdout));
      } catch (e) {
        resolve({ error: stderr || stdout || e.message });
      }
    });
    proc.stdin.write(JSON.stringify({ host, port, user, password }));
    proc.stdin.end();
  });
});

ipcMain.handle('repos:create', async (_event, { host, port, user, password, databases }) => {
  const created = [];
  const errors = [];
  for (const database of databases) {
    let repo;
    try {
      repo = repoStore.createRepo({ host, port, user, password, database });
    } catch (e) {
      errors.push(`${database}: ${e.message}`);
      continue;
    }
    // Every new repo starts with a baseline so its first checkpoint actually
    // has restorable data, not just an empty schema snapshot.
    const dumpResult = await runPython([scriptPath('data_dump.py'), '--dump'], repo);
    if (dumpResult.code !== 0) {
      errors.push(`${database}: created, but initial baseline dump failed: ${dumpResult.stderr}`);
    }
    created.push(repoStore.redact(repo));
  }
  return { created, errors };
});

ipcMain.handle('repos:remove', (_event, id) => {
  const proc = watchProcesses.get(id);
  if (proc) {
    proc.kill();
    watchProcesses.delete(id);
  }
  repoStore.removeRepo(id);
  return repoStore.listRepos().map(repoStore.redact);
});

// --- IPC: checkpoints ---

ipcMain.handle('checkpoints:list', async () => {
  const repo = requireActiveRepo();
  const { code, stdout, stderr } = await runPython([scriptPath('list_checkpoints.py')], repo);
  if (code !== 0) return { error: stderr || 'list_checkpoints.py failed' };
  try {
    return { checkpoints: JSON.parse(stdout) };
  } catch (e) {
    return { error: `Failed to parse checkpoints: ${e.message}` };
  }
});

// --- IPC: revert ---

ipcMain.handle('revert:plan', async (_event, timestamp) => {
  const repo = requireActiveRepo();
  const { code, stdout, stderr } = await runPython([scriptPath('revert.py'), '--at', timestamp, '--json'], repo);
  if (code !== 0) return { error: stderr || 'revert.py --json failed' };
  try {
    return { plan: JSON.parse(stdout) };
  } catch (e) {
    return { error: `Failed to parse plan: ${e.message}` };
  }
});

ipcMain.handle('revert:apply', async (_event, { timestamp, force }) => {
  const repo = requireActiveRepo();
  const args = [scriptPath('revert.py'), '--at', timestamp, '--apply'];
  if (force) args.push('--force');
  const { code, stdout, stderr } = await runPython(args, repo);
  return { success: code === 0, stdout, stderr };
});

// --- IPC: watch.py lifecycle (per active repo) ---

ipcMain.handle('watch:status', () => {
  const repo = repoStore.getActiveRepo();
  if (!repo) return { running: false };
  const proc = watchProcesses.get(repo.id);
  return { running: !!proc && !proc.killed };
});

ipcMain.handle('watch:start', () => {
  const repo = requireActiveRepo();
  if (watchProcesses.has(repo.id)) return { running: true };

  const env = { ...process.env, DB_URL: repo.dbUrl };
  const proc = spawn(PYTHON_BIN, [scriptPath('watch.py')], { cwd: repo.dataDir, env });
  proc.stdout.on('data', (d) => {
    if (mainWindow) mainWindow.webContents.send('watch:log', { repoId: repo.id, line: d.toString() });
  });
  proc.stderr.on('data', (d) => {
    if (mainWindow) mainWindow.webContents.send('watch:log', { repoId: repo.id, line: d.toString() });
  });
  proc.on('close', () => {
    watchProcesses.delete(repo.id);
    if (mainWindow) mainWindow.webContents.send('watch:stopped', { repoId: repo.id });
  });
  watchProcesses.set(repo.id, proc);
  return { running: true };
});

ipcMain.handle('watch:stop', () => {
  const repo = requireActiveRepo();
  const proc = watchProcesses.get(repo.id);
  if (proc) {
    proc.kill();
    watchProcesses.delete(repo.id);
  }
  return { running: false };
});
