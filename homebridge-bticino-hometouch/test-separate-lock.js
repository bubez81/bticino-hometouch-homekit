'use strict';
const assert=require('node:assert/strict');
let calls=[];
require('./ipc').request=async(_socket,command,payload)=>{
  if(command==='ping')throw Error('offline');
  calls.push({command,payload});return {ok:true};
};
class Service {
  constructor(){this.chars=new Map();}
  getCharacteristic(key){if(!this.chars.has(key))this.chars.set(key,{onGet(fn){this.get=fn;return this;},onSet(fn){this.set=fn;return this;}});return this.chars.get(key);}
  updateCharacteristic(){} setPrimaryService(){this.primary=true;}
}
class Lock extends Service{} class Doorbell extends Service{}
const Current={UNKNOWN:3,SECURED:1,UNSECURED:0},Target={SECURED:1,UNSECURED:0};
const types={};
const api={registerAccessory(_p,name,ctor){types[name]=ctor;},registerPlatform(){},on(){},hap:{Service:{LockMechanism:Lock,Doorbell},Characteristic:{LockCurrentState:Current,LockTargetState:Target}}};
const log={info(){},warn(){},error(){}};
require('./index')(api);
(async()=>{
  const lock=new types.BTicinoCallLock(log,{cameraName:'camera'},api);
  const set=lock.service.getCharacteristic(Target).set;
  await assert.rejects(()=>set(0),/non disponibile/);
  const camera=new types.BTicinoHOMETOUCH(log,{name:'camera',enableCallUnlock:true,separateCallUnlock:true},api);
  assert.equal(camera.getServices().length,1);
  assert(camera.getServices()[0] instanceof Doorbell);
  assert(lock.getServices()[0] instanceof Lock);
  assert(lock.service.primary);
  await assert.rejects(()=>set(0),/Rispondi/);
  camera.streamManager.sessions.set('session',{id:'session',incoming:{},state:'streaming'});
  await set(0);
  assert.deepEqual(calls,[{command:'open_incoming',payload:{session_id:'session'}}]);
  camera.streamManager.sessions.get('session').state='stopping';
  await assert.rejects(()=>set(0));
  assert.equal(calls.length,1);
  console.log('SEPARATE_LOCK_BINDING_OK (mock IPC only)');
})().catch(err=>{console.error(err);process.exitCode=1;});
