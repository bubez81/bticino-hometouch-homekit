'use strict';
// "HOMETOUCH connection": a contact sensor inside the video doorbell accessory,
// closed while the listener is registered with the HOMETOUCH, open (with a
// fault) when the HOMETOUCH keeps dropping the connection or cannot be reached.
// Apple Home can notify its changes; until the HOMETOUCH is restarted, rings
// and entrance opening may not work.
const {request} = require('./ipc');

const POLL_SECONDS = 5;
// The listener needs a few seconds to start; it reports short reconnections itself.
const IPC_DOWN_POLLS = 12;

function addConnectionSensor(api, accessory, socket, log, {ipc = request, setTimer = setInterval} = {}) {
  const {Service, Characteristic} = api.hap;
  const {CONTACT_DETECTED, CONTACT_NOT_DETECTED} = Characteristic.ContactSensorState;
  const name = 'Collegamento HOMETOUCH';
  const service = accessory.getServiceById?.(Service.ContactSensor, 'connection')
    || accessory.addService(Service.ContactSensor, name, 'connection');
  service.setCharacteristic(Characteristic.Name, name);
  if (Characteristic.ConfiguredName) {
    if (!service.testCharacteristic(Characteristic.ConfiguredName)) service.addOptionalCharacteristic(Characteristic.ConfiguredName);
    service.setCharacteristic(Characteristic.ConfiguredName, name);
  }
  let status = 'connected', failures = 0;
  const show = next => {
    if (next !== status) log[next === 'connected' ? 'info' : 'warn'](`Collegamento HOMETOUCH: ${next}`);
    status = next;
    const ok = next === 'connected';
    service.updateCharacteristic(Characteristic.ContactSensorState, ok ? CONTACT_DETECTED : CONTACT_NOT_DETECTED);
    service.updateCharacteristic(Characteristic.StatusFault,
      ok ? Characteristic.StatusFault.NO_FAULT : Characteristic.StatusFault.GENERAL_FAULT);
  };
  show(status);
  const poll = async () => {
    try {
      const result = await ipc(socket, 'connection_status', {}, 3000);
      failures = 0;
      // An older listener without the command: keep the sensor closed.
      if (result?.ok && ['connected', 'unstable', 'disconnected'].includes(result.connection)) show(result.connection);
    } catch (_) {
      if (++failures >= IPC_DOWN_POLLS) show('disconnected');
    }
  };
  const timer = setTimer(poll, POLL_SECONDS * 1000);
  timer?.unref?.();
  api.on('shutdown', () => clearInterval(timer));
  return {service, poll, get status() { return status; }};
}

module.exports = {addConnectionSensor, POLL_SECONDS, IPC_DOWN_POLLS};
