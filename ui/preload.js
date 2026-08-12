const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('dvc', {
  listCheckpoints: () => ipcRenderer.invoke('checkpoints:list'),
  getRevertPlan: (timestamp) => ipcRenderer.invoke('revert:plan', timestamp),
  applyRevert: (timestamp, force) => ipcRenderer.invoke('revert:apply', { timestamp, force }),
  watchStatus: () => ipcRenderer.invoke('watch:status'),
  watchStart: () => ipcRenderer.invoke('watch:start'),
  watchStop: () => ipcRenderer.invoke('watch:stop'),
  onWatchLog: (callback) => ipcRenderer.on('watch:log', (_e, line) => callback(line)),
  onWatchStopped: (callback) => ipcRenderer.on('watch:stopped', () => callback()),
});
