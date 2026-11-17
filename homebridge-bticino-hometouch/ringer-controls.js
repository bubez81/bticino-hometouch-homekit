'use strict';
// Ringer controls inside the video doorbell accessory: a "Suoneria muta" switch
// and a "Segreteria" switch (and, optionally, a "Volume suoneria" dimmer). Each
// sends an OpenWebNet command to the listener over IPC. There is no read-back
// from the gateway, so these are optimistic: the shown state is the last value we
// set (it starts from a safe default and can drift if changed on the HOMETOUCH
// panel itself).
const {request} = require('./ipc');

const IPC_TIMEOUT_MS = 8000;
const NAMES = {mute: 'Suoneria muta', voicemail: 'Segreteria', volume: 'Volume suoneria'};

async function send(ipc, socket, control, value, log, name) {
  let result;
  try { result = await ipc(socket, 'ringer_command', {control, value}, IPC_TIMEOUT_MS); }
  catch (error) { result = {ok: false, error: error.message}; }
  if (result?.ok) {
    log.info(`Suoneria ${name}: ${value} inviato`);
    return true;
  }
  log.warn(`Suoneria ${name}: non riuscito (${result?.error || 'errore'})`);
  return false;
}

function addSwitch(api, accessory, socket, log, ipc, control, name) {
  const {Service, Characteristic} = api.hap;
  const service = accessory.getServiceById?.(Service.Switch, `ringer-${control}`)
    || accessory.addService(Service.Switch, name, `ringer-${control}`);
  service.setCharacteristic(Characteristic.Name, name);
  if (Characteristic.ConfiguredName) {
    if (!service.testCharacteristic(Characteristic.ConfiguredName)) service.addOptionalCharacteristic(Characteristic.ConfiguredName);
    service.setCharacteristic(Characteristic.ConfiguredName, name);
  }
  let state = false;  // safe default: not muted / answering machine off
  service.updateCharacteristic(Characteristic.On, state);
  service.getCharacteristic(Characteristic.On)
    .onGet(() => state)
    .onSet(async value => {
      const wanted = value === true;
      if (await send(ipc, socket, control, wanted, log, name)) {
        state = wanted;
      } else {
        // Revert the toggle so the UI keeps matching our last known state.
        setTimeout(() => service.updateCharacteristic(Characteristic.On, state), 0);
      }
    });
  return service;
}

function addVolume(api, accessory, socket, log, ipc) {
  const {Service, Characteristic} = api.hap;
  const name = NAMES.volume;
  const service = accessory.getServiceById?.(Service.Lightbulb, 'ringer-volume')
    || accessory.addService(Service.Lightbulb, name, 'ringer-volume');
  service.setCharacteristic(Characteristic.Name, name);
  if (Characteristic.ConfiguredName) {
    if (!service.testCharacteristic(Characteristic.ConfiguredName)) service.addOptionalCharacteristic(Characteristic.ConfiguredName);
    service.setCharacteristic(Characteristic.ConfiguredName, name);
  }
  if (!service.testCharacteristic(Characteristic.Brightness)) service.addCharacteristic(Characteristic.Brightness);
  let level = 100;   // optimistic; no read-back
  let on = true;
  service.updateCharacteristic(Characteristic.On, on);
  service.updateCharacteristic(Characteristic.Brightness, level);
  const apply = async value => {
    if (await send(ipc, socket, 'volume', value, log, name)) { level = value; on = value > 0; return true; }
    return false;
  };
  service.getCharacteristic(Characteristic.On)
    .onGet(() => on)
    .onSet(async value => {
      const target = value === true ? (level > 0 ? level : 100) : 0;
      if (!await apply(target)) setTimeout(() => service.updateCharacteristic(Characteristic.On, on), 0);
      else service.updateCharacteristic(Characteristic.Brightness, level);
    });
  service.getCharacteristic(Characteristic.Brightness)
    .onGet(() => level)
    .onSet(async value => {
      const target = Math.max(0, Math.min(100, Number(value) || 0));
      if (!await apply(target)) setTimeout(() => service.updateCharacteristic(Characteristic.Brightness, level), 0);
      else service.updateCharacteristic(Characteristic.On, on);
    });
  return service;
}

// options: {mute, voicemail, volume} booleans. Returns the services added.
function addRingerControls(api, accessory, options, socket, log, {ipc = request} = {}) {
  const services = [];
  const opts = options || {};
  if (opts.mute !== false) services.push(addSwitch(api, accessory, socket, log, ipc, 'mute', NAMES.mute));
  if (opts.voicemail !== false) services.push(addSwitch(api, accessory, socket, log, ipc, 'voicemail', NAMES.voicemail));
  if (opts.volume === true) services.push(addVolume(api, accessory, socket, log, ipc));
  return services;
}

module.exports = {addRingerControls, NAMES};
