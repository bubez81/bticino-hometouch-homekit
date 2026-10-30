'use strict';
// One HomeKit lock per entrance, inside the video doorbell accessory: the user
// adds a single accessory, and the locks sit in the doorbell's room, so the
// ring notification offers them. Opening is a pulse; there is no lock sensor.
// The lock shows "unlocked" only once the listener has sent the opening (it
// waits for a reconnection of the HOMETOUCH and for its answer); an opening
// that fails shows the lock as jammed for a minute instead of silently locked.
const {request} = require('./ipc');

const UNLOCKED_SECONDS = 4;
const JAMMED_SECONDS = 60;
// The listener waits up to 20 s for a reconnection and 3 s for the HOMETOUCH's answer.
const OPEN_TIMEOUT_SECONDS = 30;

// Listener entrance ids must match ^[a-z0-9_-]{1,32}$.
function entranceId(name, used) {
  const base = String(name).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 28) || 'entrance';
  let id = base, n = 2;
  while (used.has(id)) id = `${base}_${n++}`;
  used.add(id);
  return id;
}

function normalizeEntrances(list) {
  const used = new Set(), result = [];
  for (const item of Array.isArray(list) ? list : []) {
    const name = String(item?.name || '').trim();
    const address = String(item?.address ?? '').trim();
    if (!name || !/^[0-9]{1,4}$/.test(address)) continue;
    result.push({name: name.slice(0, 64), address, id: entranceId(name, used)});
  }
  return result;
}

function addEntranceLocks(api, accessory, entrances, socket, log, {ipc = request, setTimer = setTimeout} = {}) {
  const {Service, Characteristic} = api.hap;
  const {SECURED, UNSECURED, JAMMED} = Characteristic.LockCurrentState;
  const services = [];
  for (const entrance of entrances) {
    const service = accessory.getServiceById?.(Service.LockMechanism, entrance.id)
      || accessory.addService(Service.LockMechanism, entrance.name, entrance.id);
    service.setCharacteristic(Characteristic.Name, entrance.name);
    if (Characteristic.ConfiguredName) {
      if (!service.testCharacteristic(Characteristic.ConfiguredName)) service.addOptionalCharacteristic(Characteristic.ConfiguredName);
      service.setCharacteristic(Characteristic.ConfiguredName, entrance.name);
    }
    let pending = false, jammed = null;
    const show = (current, target = current === UNSECURED ? UNSECURED : SECURED) => {
      service.updateCharacteristic(Characteristic.LockTargetState, target);
      service.updateCharacteristic(Characteristic.LockCurrentState, current);
    };
    show(SECURED);
    const open = async () => {
      let result;
      try { result = await ipc(socket, 'open_entrance', {entrance: entrance.id}, OPEN_TIMEOUT_SECONDS * 1000); }
      catch (error) { result = {ok: false, error: error.message}; }
      if (result?.ok) {
        log.info(`Apertura ${entrance.name}: ${result.state === 'confirmed' ? 'confermata dal HOMETOUCH' : 'inviata'}`);
        show(UNSECURED);
        setTimer(() => { pending = false; show(SECURED); }, UNLOCKED_SECONDS * 1000);
      } else {
        log.warn(`Apertura ${entrance.name}: non riuscita (${result?.error || 'errore'})`);
        pending = false;
        show(JAMMED, SECURED);
        jammed = setTimer(() => { jammed = null; if (!pending) show(SECURED); }, JAMMED_SECONDS * 1000);
      }
    };
    service.getCharacteristic(Characteristic.LockTargetState).onSet(value => {
      if (value !== UNSECURED) { if (!pending && !jammed) show(SECURED); return; }
      if (pending) return;
      pending = true;
      if (jammed) { clearTimeout(jammed); jammed = null; }
      // Apple Home shows "Opening…" meanwhile; the answer may take a reconnection.
      service.updateCharacteristic(Characteristic.LockCurrentState, SECURED);
      open();
    });
    services.push(service);
  }
  return services;
}

module.exports = {addEntranceLocks, normalizeEntrances, entranceId, UNLOCKED_SECONDS, JAMMED_SECONDS};
