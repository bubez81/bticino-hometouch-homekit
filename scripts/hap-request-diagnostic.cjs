'use strict';
// Temporary, metadata-only tracing for the isolated Cubetto test bridge.
const { Accessory } = require('/usr/local/lib/node_modules/homebridge/node_modules/@homebridge/hap-nodejs');
const trace = message => console.log(`[HAP-DIAG] ${message}`);
for (const method of ['handleAccessories', 'handleResource', 'handleSetCharacteristics', 'handleGetCharacteristics']) {
  const original = Accessory.prototype[method];
  if (typeof original !== 'function') throw new Error(`Missing HAP method: ${method}`);
  Accessory.prototype[method] = function (...args) {
    trace(`${method} received`);
    if (method === 'handleAccessories') {
      const callback = args[1];
      args[1] = (error, response) => {
        // Inspect the serialized response, not just the in-memory controller.
        trace(`accessories response error=${Boolean(error)}`);
        for (const accessory of response?.accessories || []) {
          for (const service of accessory.services || []) {
            trace(`wire aid=${accessory.aid} service=${service.type} primary=${Boolean(service.primary)} characteristics=${service.characteristics.map(c => `${c.iid}:${c.type}:${c.status ?? 'ok'}:${c.format}`).join(',')}`);
          }
        }
        callback(error, response);
      };
      for (const accessory of [this, ...(this.bridgedAccessories || [])]) {
        trace(`accessory aid=${accessory.aid} cameraController=${Boolean(accessory.activeCameraController)} services=${accessory.services.map(s => s.UUID).join(',')}`);
      }
    }
    if (method === 'handleSetCharacteristics' || method === 'handleGetCharacteristics') {
      const entries = args[1].characteristics || args[1].ids || [];
      for (const entry of entries) {
        const characteristic = this.getAccessoryByAID(entry.aid)?.getCharacteristicByIID(entry.iid);
        trace(`characteristic aid=${entry.aid} iid=${entry.iid} type=${characteristic?.UUID || 'missing'} operation=${method === 'handleGetCharacteristics' ? 'read' : Object.hasOwn(entry, 'value') ? 'write' : 'subscription'}`);
      }
      const callback = args[2];
      args[2] = (error, response) => {
        trace(`response error=${Boolean(error)} statuses=${(response?.characteristics || []).map(c => c.status || 0).join(',')}`);
        callback(error, response);
      };
    }
    if (method === 'handleResource') {
      const data = args[0] || {};
      const target = data.aid ? this.getAccessoryByAID(data.aid) : this;
      trace(`resource targetExists=${Boolean(target)} cameraController=${Boolean(target && target.activeCameraController)}`);
    }
    return original.apply(this, args);
  };
}
const configure = Accessory.prototype.configureController;
Accessory.prototype.configureController = function (...args) {
  const result = configure.apply(this, args);
  trace(`controller configured cameraController=${Boolean(this.activeCameraController)}`);
  return result;
};
trace('metadata tracing enabled');
