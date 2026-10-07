'use strict';
// Restart policy of the bundled listener, without starting processes.
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {ListenerSupervisor} = require('./supervisor');

const storage = fs.mkdtempSync(path.join(os.tmpdir(), 'bticino-supervisor-test-'));
fs.writeFileSync(path.join(storage, 'onboarding.json'), '{}');
const children = [], timers = [], lines = [];
const spawnFn = () => { const child = new EventEmitter(); child.kill = signal => setImmediate(() => child.emit('exit', null, signal)); children.push(child); return child; };
const setTimer = (fn, ms) => { timers.push({fn, ms}); return timers.length; };
const log = {info: m => lines.push(m), warn: m => lines.push(m), error: m => lines.push(m)};
const supervisor = new ListenerSupervisor({storage, ffmpeg: 'ffmpeg', log, spawnFn, setTimer});
assert.equal(supervisor.start(), true);
assert.equal(children.length, 1);
// Crashes back off: 1 s, 2 s, 5 s ...
for (const expected of [1000, 2000, 5000]) {
  children.at(-1).emit('exit', 1, null);
  assert.equal(timers.at(-1).ms, expected);
  timers.at(-1).fn();
}
assert.equal(children.length, 4);
assert(lines.some(l => /restarting in 1 s/.test(l)));
(async () => {
  await supervisor.stop();
  const before = timers.length;
  children.at(-1).emit('exit', 0, null);
  assert.equal(timers.length, before, 'no restart after stop');
  fs.rmSync(storage, {recursive: true, force: true});
  console.log('SUPERVISOR_RESTART_BACKOFF_STOP_OK');
})().catch(error => { console.error(error); process.exit(1); });
