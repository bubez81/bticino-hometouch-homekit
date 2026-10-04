'use strict';
const assert=require('node:assert/strict');
let requests=0,result={ok:true};
require('./ipc').request=async (_socket,command,payload)=>{
  requests++;assert.equal(command,'open_incoming');assert.equal(payload.session_id,'home');return result;
};
const {installCallUnlock}=require('./call-unlock');
let setter;
const Current={UNKNOWN:3,SECURED:1,UNSECURED:0},Target={SECURED:1,UNSECURED:0};
const getters=new Map(),updates=[];
let expire;
const originalTimeout=global.setTimeout;
global.setTimeout=(fn,ms)=>{assert.equal(ms,3000);expire=fn;return {unref(){}};};
class LockMechanism {
  getCharacteristic(type){return {onGet(fn){getters.set(type,fn);assert.equal(fn(),1);return this;},onSet(fn){setter=fn;}};}
  updateCharacteristic(type,value){updates.push({type,value});}
}
(async()=>{
  const manager={sessions:new Map()};
  installCallUnlock({hap:{Service:{LockMechanism},Characteristic:{LockCurrentState:Current,LockTargetState:Target}}},manager,{}, {info(){}});
  await setter(1);assert.equal(requests,0);
  await assert.rejects(()=>setter(0));assert.equal(requests,0);
  manager.sessions.set('home',{id:'home',incoming:{},state:'stopping'});
  await assert.rejects(()=>setter(0));assert.equal(requests,0);
  manager.sessions.get('home').state='streaming';
  await setter(0);assert.equal(requests,1);
  assert.equal(getters.get(Current)(),0);
  assert.equal(getters.get(Target)(),0);
  await assert.rejects(()=>setter(0),/già in corso/);assert.equal(requests,1);
  expire();
  assert.equal(getters.get(Current)(),1);
  assert.equal(getters.get(Target)(),1);
  assert.equal(requests,1); // timeout never sends a lock/open command
  result={ok:false,error:'answered_call_required'};
  await assert.rejects(()=>setter(0),/answered_call_required/);
  assert.equal(getters.get(Current)(),1);
  manager.sessions.set('other',{id:'other',incoming:{},state:'streaming'});
  await assert.rejects(()=>setter(0));assert.equal(requests,2);
  console.log('CALL_UNLOCK_UI_OK (mock IPC, no physical commands)');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{global.setTimeout=originalTimeout;});
