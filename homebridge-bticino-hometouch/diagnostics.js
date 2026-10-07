'use strict';
// One anonymised text file for support: versions, checks, a summary of the
// configuration and the recent logs. Built on demand from the settings page.
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const {execFile} = require('node:child_process');
const ipc = require('./ipc');
const {PluginLog, redact} = require('./plugin-log');

function run(file, args) {
  return new Promise(resolve => execFile(file, args, {timeout: 10000, maxBuffer: 4 << 20},
    (error, stdout, stderr) => resolve(error ? `non disponibile (${error.code || error.message})` : `${stdout}${stderr}`.trim())));
}

function tcpReachable(host, port, timeout = 3000) {
  return new Promise(resolve => {
    const socket = net.connect({host, port});
    const done = result => { socket.destroy(); resolve(result); };
    socket.setTimeout(timeout, () => done('timeout'));
    socket.once('connect', () => done('raggiungibile'));
    socket.once('error', error => done(`errore ${error.code}`));
  });
}

async function ask(socket, command) {
  try { return JSON.stringify(await ipc.request(socket, command, {}, 3000)); }
  catch (error) { return `non disponibile (${error.code || error.message})`; }
}

function readJson(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (_) { return null; }
}

function tailFile(file, limit) {
  try { return fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).slice(-limit); } catch (_) { return []; }
}

async function buildDiagnostics({storage, config = {}, versions = {}, ffmpeg, python}) {
  const lines = [];
  const section = title => lines.push('', `## ${title}`);
  const onboarding = readJson(path.join(storage, 'onboarding.json'));
  const socket = config.ipcSocket || path.join(storage, 'hometouch.sock');
  lines.push('# BTicino HOMETOUCH – diagnostica', `Generata: ${new Date().toISOString()}`);

  section('Versioni');
  lines.push(`plugin ${versions.plugin || '?'}, Homebridge UI ${versions.ui || '?'}, Node ${process.version}`);
  lines.push(`sistema ${os.type()} ${os.release()} ${os.arch()}`);
  lines.push(`python: ${python} → ${(await run(python, ['--version'])).split('\n')[0]}`);
  const encoders = ffmpeg ? await run(ffmpeg, ['-hide_banner', '-encoders']) : '';
  const protocols = ffmpeg ? await run(ffmpeg, ['-hide_banner', '-protocols']) : '';
  lines.push(`ffmpeg: ${ffmpeg || 'non trovato'}; libspeex=${/libspeex/.test(encoders)} libopus=${/libopus/.test(encoders)} `
    + `libx264=${/libx264/.test(encoders)} srtp=${/\bsrtp\b/.test(protocols)}`);

  section('Configurazione');
  lines.push(`collegato all'impianto: ${onboarding ? 'sì' : 'no (procedura in Impostazioni non completata)'}`);
  for (const key of ['credentials_file', 'certificate_file', 'private_key_file', 'ca_file']) {
    const value = onboarding?.[key];
    const file = value && !path.isAbsolute(value) ? path.join(storage, value) : value;
    lines.push(`${key}: ${file ? (fs.existsSync(file) ? 'presente' : 'MANCANTE') : 'non impostato'}`);
  }
  const candidates = readJson(path.join(storage, 'camera-candidates.json'));
  lines.push(`telecamere note: ${candidates?.candidates?.length ?? 0}`);
  lines.push(`nome: ${config.name || 'Videocitofono'}; audio nel live: ${config.liveAudio !== false}`);
  lines.push(`ingressi: ${(config.entrances || []).map(e => `${e.name}=${e.address}`).join(', ') || 'nessuno'}`);
  lines.push(`Home Assistant: ${config.homeAssistant?.enabled ? `attivo, porta ${config.homeAssistant.port || 8790}` : 'non attivo'}`);
  lines.push(`opzioni avanzate: ${['pythonPath', 'ffmpegPath', 'storagePath', 'ipcSocket'].filter(k => config[k]).join(', ') || 'nessuna'}`);

  section('Controlli');
  if (onboarding?.sip_server) lines.push(`gateway ${onboarding.sip_server}:${onboarding.sip_port || 5061}: ${await tcpReachable(onboarding.sip_server, onboarding.sip_port || 5061)}`);
  lines.push(`listener (IPC ping): ${await ask(socket, 'ping')}`);
  lines.push(`chiamata a richiesta: ${await ask(socket, 'status')}`);
  lines.push(`suonata in corso: ${await ask(socket, 'incoming_status')}`);
  lines.push(`ultime aperture: ${await ask(socket, 'entrance_status')}`);

  section('Log del plugin (ultime 2000 righe)');
  lines.push(...new PluginLog(path.join(storage, 'plugin.log')).tail(2000));
  section('Chiamate alla telecamera (ultime 300 righe)');
  lines.push(...tailFile(path.join(storage, 'camera-calls.log'), 300)
    .filter(line => !/\[h264 @|\[s16le @|Last message repeated|no frame!|non-existing PPS/.test(line)));
  return redact(lines.join('\n')) + '\n';
}

module.exports = {buildDiagnostics};
