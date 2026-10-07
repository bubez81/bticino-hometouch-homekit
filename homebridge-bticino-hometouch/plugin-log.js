'use strict';
// The plugin's own log file (plugin.log, rotated), so support does not depend
// on Homebridge's log, which mixes every plugin and is rotated away quickly.
// Lines are the same as in the Homebridge log; no keys or passwords are ever
// logged, and redact() anonymises text before it is shared.
const fs = require('node:fs');
const path = require('node:path');

class PluginLog {
  constructor(file, {maxBytes = 1024 * 1024, files = 3} = {}) {
    Object.assign(this, {file, maxBytes, files});
    this.size = fs.existsSync(file) ? fs.statSync(file).size : 0;
  }

  write(level, message) {
    const line = `${new Date().toISOString()} ${level.toUpperCase().padEnd(5)} ${message}\n`;
    try {
      if (this.size + line.length > this.maxBytes) this.rotate();
      fs.appendFileSync(this.file, line, {mode: 0o600});
      this.size += Buffer.byteLength(line);
    } catch (_) { /* logging must never break the plugin */ }
  }

  rotate() {
    for (let n = this.files - 1; n >= 1; n--) {
      const from = n === 1 ? this.file : `${this.file}.${n - 1}`;
      if (fs.existsSync(from)) fs.renameSync(from, `${this.file}.${n}`);
    }
    this.size = 0;
  }

  // Oldest first, at most `limit` lines.
  tail(limit) {
    const parts = [];
    for (let n = this.files - 1; n >= 0; n--) {
      const file = n === 0 ? this.file : `${this.file}.${n}`;
      if (fs.existsSync(file)) parts.push(fs.readFileSync(file, 'utf8'));
    }
    return parts.join('').split('\n').filter(Boolean).slice(-limit);
  }
}

// A Homebridge logger that also writes to the plugin's file.
function teeLogger(log, file) {
  const levels = ['info', 'warn', 'error', 'debug', 'success'];
  const tee = {};
  for (const level of levels) {
    tee[level] = (message, ...rest) => {
      (log[level] || log.info).call(log, message, ...rest);
      if (level !== 'debug') file.write(level, String(message));
    };
  }
  return tee;
}

// Replace identifying values with stable placeholders (<ip-1>, <ip-2>, …).
function redact(text) {
  const maps = new Map();
  const token = (kind, value) => {
    if (!maps.has(kind)) maps.set(kind, new Map());
    const known = maps.get(kind);
    if (!known.has(value)) known.set(value, `<${kind}-${known.size + 1}>`);
    return known.get(value);
  };
  return String(text)
    .replace(/inline:\S+/g, 'inline:<key>')
    .replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, value => token('account', value))
    .replace(/\b[\w-]+(\.[\w-]+)*\.bs\.iotleg\.com\b/gi, value => token('sip-domain', value))
    .replace(/\b(?:[0-9A-F]{2}:){5}[0-9A-F]{2}\b/gi, value => token('mac', value))
    .replace(/\b\d{3}-\d{2}-\d{3}\b/g, '<setup-code>')
    .replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g, value => (value === '127.0.0.1' || value === '0.0.0.0' ? value : token('ip', value)))
    .replace(/\/(Users|home)\/[^/\s]+/g, '/$1/<user>')
    // Long tokens without slashes (paths stay readable).
    .replace(/(^|[^\w/])[A-Za-z0-9+_-]{32,}={0,2}/g, '$1<redacted>');
}

module.exports = {PluginLog, teeLogger, redact};
