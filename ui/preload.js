const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('dvc', {
  // Repos
  listRepos: () => ipcRenderer.invoke('repos:list'),
  getActiveRepo: () => ipcRenderer.invoke('repos:getActive'),
  setActiveRepo: (id) => ipcRenderer.invoke('repos:setActive', id),
  listDatabases: (conn) => ipcRenderer.invoke('repos:listDatabases', conn),
  createRepos: (conn) => ipcRenderer.invoke('repos:create', conn),
  removeRepo: (id) => ipcRenderer.invoke('repos:remove', id),

  // Checkpoints / revert
  listCheckpoints: () => ipcRenderer.invoke('checkpoints:list'),
  getRevertPlan: (timestamp) => ipcRenderer.invoke('revert:plan', timestamp),
  applyRevert: (timestamp, force) => ipcRenderer.invoke('revert:apply', { timestamp, force }),

  // Capture (watch.py) lifecycle
  watchStatus: () => ipcRenderer.invoke('watch:status'),
  watchStart: () => ipcRenderer.invoke('watch:start'),
  watchStop: () => ipcRenderer.invoke('watch:stop'),
  onWatchLog: (callback) => ipcRenderer.on('watch:log', (_e, payload) => callback(payload)),
  onWatchStopped: (callback) => ipcRenderer.on('watch:stopped', (_e, payload) => callback(payload)),
});
