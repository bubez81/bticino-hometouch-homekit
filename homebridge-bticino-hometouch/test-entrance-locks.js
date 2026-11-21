'use strict';
const assert=require('node:assert/strict');
const {addEntranceLocks}=require('./entrance-locks');
const LockCurrentState={UNSECURED:0,SECURED:1,JAMMED:2},LockTargetState='target';
class LockMechanism{
  constructor(name,subtype){this.subtype=subtype;this.values={};this.handlers={};this.getters={};}
  setCharacteristic(key,value){this.values[key]=value;return this;}
  updateCharacteristic(key,value){this.values[key]=value;return this;}
  getCharacteristic(key){const char={onGet:fn=>{this.getters[key]=fn;return char;},onSet:fn=>{this.handlers[key]=fn;return char;}};return char;}
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
  // onGet reflects the real state, so Apple Home re-reading after a missed event self-heals.
  const readCurrent=()=>lock.getters[LockCurrentState]();
  const readTarget=()=>lock.getters[LockTargetState]();
  const set=lock.handlers[LockTargetState];
  assert.equal(readCurrent(),LockCurrentState.SECURED,'onGet current starts locked');
  assert.equal(readTarget(),LockCurrentState.SECURED,'onGet target starts locked');
  // Waiting for the HOMETOUCH: still locked ("Opening…"), a second tap is ignored.
  set(LockCurrentState.UNSECURED);set(LockCurrentState.UNSECURED);
  assert.equal(current(),LockCurrentState.SECURED);
  assert.equal(calls,1);
  answer={ok:true,state:'confirmed'};release();await flush();
  assert.equal(current(),LockCurrentState.UNSECURED);
  assert.equal(readCurrent(),LockCurrentState.UNSECURED,'onGet shows unlocked while open');
  timers.pop().fn();
  assert.equal(current(),LockCurrentState.SECURED);
  // Even if Home missed the relock event, a fresh read now returns locked.
  assert.equal(readCurrent(),LockCurrentState.SECURED,'onGet re-reads locked after relock');
  assert.equal(readTarget(),LockCurrentState.SECURED,'onGet target locked after relock');
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
