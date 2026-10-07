'use strict';
// One HomeKit lock per entrance, inside the video doorbell accessory: the user
// adds a single accessory, and the locks sit in the doorbell's room, so the
// ring notification offers them. Opening is a pulse; there is no lock sensor.
const {request} = require('./ipc');

const UNLOCKED_SECONDS = 4;

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
  const {SECURED, UNSECURED} = Characteristic.LockCurrentState;
  const services = [];
  for (const entrance of entrances) {
    const service = accessory.getServiceById?.(Service.LockMechanism, entrance.id)
      || accessory.addService(Service.LockMechanism, entrance.name, entrance.id);
    service.setCharacteristic(Characteristic.Name, entrance.name);
    if (Characteristic.ConfiguredName) {
      if (!service.testCharacteristic(Characteristic.ConfiguredName)) service.addOptionalCharacteristic(Characteristic.ConfiguredName);
      service.setCharacteristic(Characteristic.ConfiguredName, entrance.name);
    }
    let pending = false;
    const show = state => {
      service.updateCharacteristic(Characteristic.LockTargetState, state);
      service.updateCharacteristic(Characteristic.LockCurrentState, state);
    };
    show(SECURED);
    service.getCharacteristic(Characteristic.LockTargetState).onSet(async value => {
      if (value !== UNSECURED) { if (!pending) show(SECURED); return; }
      if (pending) return;
      pending = true;
      service.updateCharacteristic(Characteristic.LockCurrentState, UNSECURED);
      let result;
      try { result = await ipc(socket, 'open_entrance', {entrance: entrance.id}, 5000); }
      catch (error) { result = {ok: false, error: error.message}; }
      if (result?.ok) {
        log.info(`Apertura ${entrance.name}: inviata`);
        setTimer(() => { pending = false; show(SECURED); }, UNLOCKED_SECONDS * 1000);
      } else {
        log.warn(`Apertura ${entrance.name}: non riuscita (${result?.error || 'errore'})`);
        pending = false;
        show(SECURED);
      }
    });
    services.push(service);
  }
  return services;
}

module.exports = {addEntranceLocks, normalizeEntrances, entranceId, UNLOCKED_SECONDS};
