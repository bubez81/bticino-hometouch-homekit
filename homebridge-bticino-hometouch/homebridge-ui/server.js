'use strict';
// Settings page backend: signs in with the dedicated Door Entry account and
// sets up the bridge through the bundled Python setup helper. The password is
// passed to the helper in its environment only and is never stored.
const fs = require('node:fs');
const path = require('node:path');
const {execFile} = require('node:child_process');
const {HomebridgePluginUiServer, RequestError} = require('@homebridge/plugin-ui-utils');
const {listenerDirectory} = require('../supervisor');
const {normalizeEntrances} = require('../entrance-locks');
const {buildDiagnostics} = require('../diagnostics');
const ipc = require('../ipc');

function run(file, args, options) {
  return new Promise(resolve => execFile(file, args, {timeout: 180000, maxBuffer: 1 << 20, ...options},
    (error, stdout, stderr) => resolve({error, stdout: String(stdout), stderr: String(stderr)})));
}

class UiServer extends HomebridgePluginUiServer {
  constructor() {
    super();
    this.storage = path.join(this.homebridgeStoragePath, 'bticino-hometouch');
    this.onRequest('/status', () => this.status());
    this.onRequest('/plants', body => this.helper('plants', [], body));
    this.onRequest('/apply', body => this.helper('apply', ['--plant-id', String(body?.plantId || ''), '--storage', this.storage], body));
    this.onRequest('/test-open', body => this.testOpen(body));
    this.onRequest('/diagnostics', body => this.diagnostics(body));
    this.ready();
  }

  // Opens one saved entrance through the running listener, so the user can
  // check that the address is the right gate.
  async testOpen(body) {
    const entrances = normalizeEntrances(body?.entrances);
    const entrance = entrances.find(e => e.name === String(body?.name || '').trim());
    if (!entrance) throw new RequestError('Ingresso non trovato: salvare la configurazione e riavviare Homebridge', {});
    let result;
    const socket = body?.ipcSocket || path.join(this.storage, 'hometouch.sock');
    try { result = await ipc.request(socket, 'open_entrance', {entrance: entrance.id}, 6000); }
    catch (_) { throw new RequestError('Il plugin non è in esecuzione: salvare e riavviare Homebridge', {}); }
    if (!result?.ok) throw new RequestError(`Apertura non riuscita (${result?.error || 'errore'}); se l'ingresso è nuovo, riavviare Homebridge`, {});
    return {ok: true};
  }

  // Anonymised support file: versions, checks, configuration summary, logs.
  async diagnostics(body) {
    const config = body?.config || {};
    const storage = config.storagePath || this.storage;
    let ffmpeg = config.ffmpegPath;
    if (!ffmpeg) { try { ffmpeg = require('ffmpeg-for-homebridge'); } catch (_) { ffmpeg = null; } }
    const venv = path.join(storage, 'python', 'bin', 'python3');
    const python = config.pythonPath || (fs.existsSync(venv) ? venv : 'python3');
    const text = await buildDiagnostics({storage, config, ffmpeg, python,
      versions: {plugin: require('../package.json').version, ui: this.homebridgeUiVersion}});
    return {filename: `bticino-hometouch-diagnostica-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-')}.txt`, text};
  }

  status() {
    return {configured: fs.existsSync(path.join(this.storage, 'onboarding.json')), storage: this.storage};
  }

  // A private Python environment with pyzipper, needed to read some plants'
  // encrypted configuration archives. The listener uses the same interpreter.
  async python() {
    const venv = path.join(this.storage, 'python');
    const interpreter = path.join(venv, 'bin', 'python3');
    if (fs.existsSync(interpreter)) return interpreter;
    fs.mkdirSync(this.storage, {recursive: true, mode: 0o700});
    let result = await run('python3', ['-m', 'venv', venv]);
    if (result.error) throw new RequestError('Python 3 non trovato: installare python3 sul computer di Homebridge', {});
    result = await run(interpreter, ['-m', 'pip', 'install', '--quiet', 'pyzipper==0.3.6']);
    if (result.error) throw new RequestError('Installazione di pyzipper non riuscita (serve internet la prima volta)', {});
    return interpreter;
  }

  async helper(command, args, body) {
    const email = String(body?.email || '').trim();
    const password = String(body?.password || '');
    if (!email || !password) throw new RequestError('Inserire email e password dell\'account dedicato', {});
    const directory = listenerDirectory();
    const result = await run(await this.python(), [path.join(directory, 'bticino_plugin_setup.py'), command, ...args], {
      cwd: directory,
      env: {...process.env, PYTHONPATH: directory, BTICINO_DOORENTRY_EMAIL: email, BTICINO_DOORENTRY_PASSWORD: password},
    });
    let reply;
    try { reply = JSON.parse(result.stdout.trim().split('\n').pop()); } catch (_) { reply = null; }
    if (!reply) throw new RequestError('Risposta non valida dal programma di configurazione', {});
    if (!reply.ok) throw new RequestError(reply.error || 'Configurazione non riuscita', {});
    return reply;
  }
}

(() => new UiServer())();
