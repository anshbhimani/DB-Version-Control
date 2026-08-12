const { app, BrowserWindow, ipcMain } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

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
let watchProcess = null;

function runPython(args) {
  return new Promise((resolve) => {
    const proc = spawn(PYTHON_BIN, args, { cwd: CLI_DIR });
    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', (d) => { stdout += d.toString(); });
    proc.stderr.on('data', (d) => { stderr += d.toString(); });
    proc.on('close', (code) => resolve({ code, stdout, stderr }));
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1000,
    height: 720,
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
  if (watchProcess) watchProcess.kill();
  if (process.platform !== 'darwin') app.quit();
});

// --- IPC: checkpoints ---

ipcMain.handle('checkpoints:list', async () => {
  const { code, stdout, stderr } = await runPython(['list_checkpoints.py']);
  if (code !== 0) return { error: stderr || 'list_checkpoints.py failed' };
  try {
    return { checkpoints: JSON.parse(stdout) };
  } catch (e) {
    return { error: `Failed to parse checkpoints: ${e.message}` };
  }
});

// --- IPC: revert ---

ipcMain.handle('revert:plan', async (_event, timestamp) => {
  const { code, stdout, stderr } = await runPython(['revert.py', '--at', timestamp, '--json']);
  if (code !== 0) return { error: stderr || 'revert.py --json failed' };
  try {
    return { plan: JSON.parse(stdout) };
  } catch (e) {
    return { error: `Failed to parse plan: ${e.message}` };
  }
});

ipcMain.handle('revert:apply', async (_event, { timestamp, force }) => {
  const args = ['revert.py', '--at', timestamp, '--apply'];
  if (force) args.push('--force');
  const { code, stdout, stderr } = await runPython(args);
  return { success: code === 0, stdout, stderr };
});

// --- IPC: watch.py lifecycle ---

ipcMain.handle('watch:status', () => {
  return { running: watchProcess !== null && !watchProcess.killed };
});

ipcMain.handle('watch:start', () => {
  if (watchProcess) return { running: true };
  watchProcess = spawn(PYTHON_BIN, ['watch.py'], { cwd: CLI_DIR });
  watchProcess.stdout.on('data', (d) => {
    if (mainWindow) mainWindow.webContents.send('watch:log', d.toString());
  });
  watchProcess.stderr.on('data', (d) => {
    if (mainWindow) mainWindow.webContents.send('watch:log', d.toString());
  });
  watchProcess.on('close', () => {
    watchProcess = null;
    if (mainWindow) mainWindow.webContents.send('watch:stopped');
  });
  return { running: true };
});

ipcMain.handle('watch:stop', () => {
  if (watchProcess) {
    watchProcess.kill();
    watchProcess = null;
  }
  return { running: false };
});
