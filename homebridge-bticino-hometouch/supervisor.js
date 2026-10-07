'use strict';
// Runs the bundled Python listener as a child of Homebridge: one installation,
// one process tree. Its private files live in Homebridge's storage directory.
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');

const RESTART_DELAYS = [1, 2, 5, 10, 30, 60];

function listenerDirectory() {
  // npm package: bundled copy; repository checkout: the sources next to the plugin.
  for (const candidate of [path.join(__dirname, 'listener'), path.join(__dirname, '..', 'src')]) {
    if (fs.existsSync(path.join(candidate, 'bticino_hometouch_listener.py'))) return candidate;
  }
  throw Error('Listener files not found');
}

function probePath(directory) {
  for (const candidate of [path.join(directory, 'probe-camera.py'), path.join(directory, '..', 'scripts', 'probe-camera.py')]) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

function writePrivateJson(file, value) {
  const temporary = `${file}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(value, null, 2) + '\n', {mode: 0o600});
  fs.renameSync(temporary, file);
  fs.chmodSync(file, 0o600);
}

class ListenerSupervisor {
  constructor({storage, python = 'python3', ffmpeg, settings = {}, log, spawnFn = spawn, setTimer = setTimeout}) {
    Object.assign(this, {storage, python, ffmpeg, settings, log, spawnFn, setTimer});
    this.directory = listenerDirectory();
    this.socket = path.join(storage, 'hometouch.sock');
    this.restarts = 0;
    this.stopping = false;
  }

  // The onboarding wrote onboarding.json (SIP account and private file paths);
  // the plugin options are layered on top.
  listenerConfig() {
    const onboarding = path.join(this.storage, 'onboarding.json');
    if (!fs.existsSync(onboarding)) return null;
    const base = JSON.parse(fs.readFileSync(onboarding, 'utf8'));
    const entrances = Object.fromEntries((this.settings.entrances || []).map(e => [e.id, String(e.address)]));
    const config = {
      ...base,
      base_dir: this.storage,
      ffmpeg: this.ffmpeg,
      audio_ffmpeg: this.ffmpeg,
      incoming_audio: true,
      entrance_open_enabled: Object.keys(entrances).length > 0,
      entrances,
    };
    if (this.settings.api) config.api = this.settings.api;
    return config;
  }

  start() {
    const config = this.listenerConfig();
    if (!config) {
      this.log.warn('BTicino HOMETOUCH: not set up yet. Open the plugin settings and sign in with the dedicated Door Entry account.');
      return false;
    }
    fs.mkdirSync(this.storage, {recursive: true, mode: 0o700});
    const file = path.join(this.storage, 'listener.json');
    writePrivateJson(file, config);
    this.launch(file);
    return true;
  }

  launch(file) {
    const probe = probePath(this.directory);
    const env = {
      ...process.env,
      BTICINO_SNIFFER_CONFIG: file,
      BTICINO_IPC_ENABLED: '1',
      BTICINO_IPC_ENABLE_CALLS: '1',
      BTICINO_IPC_SOCKET: this.socket,
      BTICINO_SNAPSHOT_DIR: path.join(this.storage, 'snapshots'),
      BTICINO_CAMERA_CANDIDATES: path.join(this.storage, 'camera-candidates.json'),
      BTICINO_CAMERA_LOG: path.join(this.storage, 'camera-calls.log'),
      BTICINO_LISTENER: path.join(this.directory, 'bticino_hometouch_listener.py'),
      PYTHONPATH: this.directory,
      PYTHONUNBUFFERED: '1',
    };
    if (probe) env.BTICINO_CAMERA_PROBE = probe;
    const child = this.spawnFn(this.python, [path.join(this.directory, 'bticino_hometouch_listener.py')],
      {cwd: this.storage, env, stdio: ['ignore', 'pipe', 'pipe']});
    this.child = child;
    this.started = Date.now();
    const relay = level => data => {
      for (const line of data.toString().split('\n')) if (line.trim()) this.log[level](`[listener] ${line.trim()}`);
    };
    child.stdout?.on('data', relay('info'));
    child.stderr?.on('data', relay('warn'));
    child.on('error', error => this.log.error(`BTicino HOMETOUCH listener could not start (${this.python}): ${error.message}`));
    child.on('exit', (code, signal) => {
      this.child = null;
      if (this.stopping) return;
      // A listener that stayed up for a while starts again from the shortest delay.
      if (Date.now() - this.started > 120000) this.restarts = 0;
      const delay = RESTART_DELAYS[Math.min(this.restarts++, RESTART_DELAYS.length - 1)];
      this.log.warn(`BTicino HOMETOUCH listener ended (${code ?? signal}); restarting in ${delay} s`);
      this.timer = this.setTimer(() => { if (!this.stopping) this.launch(file); }, delay * 1000);
    });
  }

  // SIGTERM lets the listener end calls with BYE; kill only if it hangs.
  stop() {
    this.stopping = true;
    clearTimeout(this.timer);
    const child = this.child;
    if (!child) return Promise.resolve();
    return new Promise(resolve => {
      const timer = setTimeout(() => { try { child.kill('SIGKILL'); } catch (_) {} }, 10000);
      child.once('exit', () => { clearTimeout(timer); resolve(); });
      try { child.kill('SIGTERM'); } catch (_) { clearTimeout(timer); resolve(); }
    });
  }
}

module.exports = {ListenerSupervisor, listenerDirectory, probePath, writePrivateJson};
