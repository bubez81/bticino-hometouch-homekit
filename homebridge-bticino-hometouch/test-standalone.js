'use strict';
const assert=require('node:assert/strict');
require('./ipc').request=async()=>{throw Error('offline');};
class Service {
  constructor(){this.chars=new Map();}
  getCharacteristic(key){if(!this.chars.has(key))this.chars.set(key,{subscriptions:0,onGet(){return this;},onSet(){return this;},sendEventNotification(){}});return this.chars.get(key);}
  setCharacteristic(){return this;} updateCharacteristic(){return this;} setPrimaryService(){this.primary=true;}
}
class Doorbell extends Service{} class AccessoryInformation extends Service{}
class DoorbellController {constructor(options){this.options=options;} on(){}}
class PlatformAccessory {
  constructor(name,uuid,category){this.displayName=name;this.UUID=uuid;this.category=category;this.services=[new AccessoryInformation()];this.controllers=[];}
  getService(type){return this.services.find(s=>s instanceof type);}
  addService(service){this.services.push(service);return service;}
  configureController(controller){this.controllers.push(controller);}
}
const types={},handlers={},published=[];
const api={
  registerAccessory(_p,name,ctor){types[name]=ctor;},registerPlatform(){},
  on(event,fn){(handlers[event]||=[]).push(fn);},
  platformAccessory:PlatformAccessory,
  publishExternalAccessories(plugin,accessories){published.push({plugin,accessories});},
  hap:{Service:{Doorbell,AccessoryInformation},Characteristic:{Manufacturer:'m',Model:'mo',SerialNumber:'s',ProgrammableSwitchEvent:{SINGLE_PRESS:0}},
    DoorbellController,Categories:{VIDEO_DOORBELL:18},uuid:{generate:value=>`uuid:${value}`}},
};
const log={info(){},warn(){},error(){},debug(){}};
require('./index')(api);

// Default: the doorbell stays inside the bridge, nothing is published externally.
const bridged=new types.BTicinoHOMETOUCH(log,{name:'bridged',enableCamera:true},api);
assert.equal(bridged.getServices().length,1);
assert.equal(bridged.getControllers().length,1);
assert.equal(bridged.externalAccessory,undefined);

// standalone: own accessory with the Video Doorbell category, doorbell service and controller.
const doorbell=new types.BTicinoHOMETOUCH(log,{name:'Videocitofono',enableCamera:true,standalone:true},api);
assert.deepEqual(doorbell.getServices(),[]);
assert.deepEqual(doorbell.getControllers(),[]);
const accessory=doorbell.externalAccessory;
assert.equal(accessory.category,18);
assert.equal(accessory.UUID,'uuid:bticino-hometouch-standalone:Videocitofono');
assert(accessory.services.includes(doorbell.doorbellService));
assert.equal(accessory.controllers.length,1);
assert.equal(accessory.controllers[0].options.externalDoorbellService,doorbell.doorbellService);
assert.equal(published.length,0);
handlers.didFinishLaunching.forEach(fn=>fn());
assert.equal(published.length,1);
assert.equal(published[0].plugin,'homebridge-bticino-hometouch');
assert.deepEqual(published[0].accessories,[accessory]);
console.log('STANDALONE_DOORBELL_OK (mock HAP only)');
