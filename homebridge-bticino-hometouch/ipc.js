'use strict';

const net = require('node:net');

function request(socketPath, command, payload = {}, timeoutMs = 1500) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(socketPath);
    let data = '';
    const timer = setTimeout(() => { socket.destroy(); reject(new Error('IPC timeout')); }, timeoutMs);
    socket.on('connect', () => socket.end(JSON.stringify({ command, ...payload }) + '\n'));
    socket.on('data', chunk => { data += chunk.toString(); });
    socket.on('end', () => {
      clearTimeout(timer);
      try { resolve(JSON.parse(data)); } catch (err) { reject(err); }
    });
    socket.on('error', err => { clearTimeout(timer); reject(err); });
  });
}

module.exports = { request };
