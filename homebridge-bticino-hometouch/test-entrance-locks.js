'use strict';
const assert=require('node:assert/strict');
const {addEntranceLocks}=require('./entrance-locks');
const LockCurrentState={UNSECURED:0,SECURED:1,JAMMED:2},LockTargetState='target';
class LockMechanism{
  constructor(name,subtype){this.subtype=subtype;this.values={};this.handlers={};}
  setCharacteristic(key,value){this.values[key]=value;return this;}
  updateCharacteristic(key,value){this.values[key]=value;return this;}
  getCharacteristic(key){return {onSet:fn=>{this.handlers[key]=fn;}};}
}
const accessory={addService:(Type,name,subtype)=>new Type(name,subtype)};
const api={hap:{Service:{LockMechanism},Characteristic:{LockCurrentState,LockTargetState,Name:'name'}}};
const log={info(){},warn(){},error(){}};
const timers=[];const setTimer=(fn,ms)=>{const t={fn,ms};timers.push(t);return t;};
const flush=()=>new Promise(r=>setImmediate(r));
let answer,calls=0,release;
const ipc=(_s,command,payload,timeout)=>{calls++;assert.equal(command,'open_entrance');assert.equal(payload.entrance,'esterno');assert.equal(timeout,30000);
  return new Promise(r=>{release=()=>r(answer);});};
(async()=>{
  const [lock]=addEntranceLocks(api,accessory,[{name:'Esterno',id:'esterno',address:'21'}],'/tmp/sock',log,{ipc,setTimer});
  const current=()=>lock.values[LockCurrentState];
  const set=lock.handlers[LockTargetState];
  // Waiting for the HOMETOUCH: still locked ("Opening…"), a second tap is ignored.
  set(LockCurrentState.UNSECURED);set(LockCurrentState.UNSECURED);
  assert.equal(current(),LockCurrentState.SECURED);
  assert.equal(calls,1);
  answer={ok:true,state:'confirmed'};release();await flush();
  assert.equal(current(),LockCurrentState.UNSECURED);
  timers.pop().fn();
  assert.equal(current(),LockCurrentState.SECURED);
  // Not opened: jammed for a minute, not silently locked.
  set(LockCurrentState.UNSECURED);
  answer={ok:false,error:'sip_unavailable'};release();await flush();
  assert.equal(current(),LockCurrentState.JAMMED);
  assert.equal(lock.values[LockTargetState],LockCurrentState.SECURED);
  const jam=timers.pop();assert.equal(jam.ms,60000);
  jam.fn();
  assert.equal(current(),LockCurrentState.SECURED);
  console.log('ENTRANCE_LOCK_CONFIRM_JAMMED_OK (mock IPC only)');
})().catch(err=>{console.error(err);process.exitCode=1;});
