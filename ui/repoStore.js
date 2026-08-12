const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const os = require('os');

// App data lives outside this tool's own source repo entirely -- each tracked
// DB gets its own directory with its own independent git history, so a hard
// reset on one DB's checkpoints can never touch this tool's code or any
// other tracked DB (this is also what fixed the earlier incident for good).
const APP_DATA_ROOT = path.join(os.homedir(), '.dvc');
const REPOS_DIR = path.join(APP_DATA_ROOT, 'repos');
const REGISTRY_PATH = path.join(APP_DATA_ROOT, 'repos.json');

function loadRegistry() {
  if (!fs.existsSync(REGISTRY_PATH)) return { repos: [], activeRepoId: null };
  return JSON.parse(fs.readFileSync(REGISTRY_PATH, 'utf8'));
}

function saveRegistry(registry) {
  fs.mkdirSync(APP_DATA_ROOT, { recursive: true });
  fs.writeFileSync(REGISTRY_PATH, JSON.stringify(registry, null, 2));
}

function listRepos() {
  return loadRegistry().repos;
}

function getActiveRepo() {
  const registry = loadRegistry();
  return registry.repos.find((r) => r.id === registry.activeRepoId) || null;
}

function setActiveRepo(id) {
  const registry = loadRegistry();
  if (!registry.repos.some((r) => r.id === id)) throw new Error(`No repo with id ${id}`);
  registry.activeRepoId = id;
  saveRegistry(registry);
  return registry.repos.find((r) => r.id === id);
}

function _safeDirName(name) {
  return name.replace(/[^a-zA-Z0-9_-]/g, '_') + '-' + Date.now().toString(36);
}

function createRepo({ host, port, user, password, database }) {
  const id = `${database}-${Date.now().toString(36)}`;
  const dataDir = path.join(REPOS_DIR, _safeDirName(database));
  fs.mkdirSync(dataDir, { recursive: true });
  for (const sub of ['snapshots', 'migrations', 'checkpoints', 'data_snapshots']) {
    fs.mkdirSync(path.join(dataDir, sub), { recursive: true });
  }
  fs.writeFileSync(
    path.join(dataDir, '.gitignore'),
    // Only local bookkeeping state is ignored -- changelog.jsonl, index.json,
    // and baseline dumps are the actual tracked history and must be committed.
    'checkpoints/poller_state.json\ncheckpoints/watch_state.json\n__pycache__/\n*.pyc\n'
  );

  spawnSync('git', ['init', '-q'], { cwd: dataDir });

  // Don't rely on the host having global git identity configured -- a repo
  // with no commits behaves oddly for hard-reset/checkpoint logic later, so
  // make sure this first commit always succeeds regardless of host setup.
  const identityCheck = spawnSync('git', ['config', 'user.email'], { cwd: dataDir });
  if (identityCheck.status !== 0 || !identityCheck.stdout.toString().trim()) {
    spawnSync('git', ['config', 'user.email', 'dvc@localhost'], { cwd: dataDir });
    spawnSync('git', ['config', 'user.name', 'DB Version Control'], { cwd: dataDir });
  }

  spawnSync('git', ['add', '.gitignore'], { cwd: dataDir });
  const commitResult = spawnSync(
    'git', ['commit', '-q', '-m', `Initialize DB Version Control repo for ${database}`], { cwd: dataDir }
  );
  if (commitResult.status !== 0) {
    throw new Error(`Failed to create initial commit for ${database}: ${commitResult.stderr}`);
  }

  const dbUrl = `mysql+mysqlconnector://${user}:${password}@${host}:${port}/${database}`;

  const repo = {
    id,
    name: database,
    dialect: 'mysql',
    host,
    port,
    user,
    password,
    database,
    dbUrl,
    dataDir,
    createdAt: new Date().toISOString(),
  };

  const registry = loadRegistry();
  registry.repos.push(repo);
  if (!registry.activeRepoId) registry.activeRepoId = id;
  saveRegistry(registry);
  return repo;
}

function removeRepo(id) {
  const registry = loadRegistry();
  registry.repos = registry.repos.filter((r) => r.id !== id);
  if (registry.activeRepoId === id) {
    registry.activeRepoId = registry.repos.length ? registry.repos[0].id : null;
  }
  saveRegistry(registry);
}

// Password stripped before anything goes to the renderer -- it only needs to
// live in the main process / on disk, same trust boundary as the old .env file.
function redact(repo) {
  if (!repo) return repo;
  const { password, ...rest } = repo;
  return rest;
}

module.exports = {
  listRepos,
  getActiveRepo,
  setActiveRepo,
  createRepo,
  removeRepo,
  redact,
};
