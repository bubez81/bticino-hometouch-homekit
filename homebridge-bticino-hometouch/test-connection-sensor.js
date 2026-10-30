'use strict';
const assert=require('node:assert/strict');
const {addConnectionSensor,IPC_DOWN_POLLS}=require('./connection-sensor');
const ContactSensorState={CONTACT_DETECTED:0,CONTACT_NOT_DETECTED:1};
const StatusFault={NO_FAULT:0,GENERAL_FAULT:1};
const Characteristic={ContactSensorState,StatusFault,Name:'Name'};
class ContactSensor{
  constructor(name,subtype){this.name=name;this.subtype=subtype;this.values=new Map();}
  setCharacteristic(key,value){this.values.set(key,value);return this;}
  updateCharacteristic(key,value){this.values.set(key,value);return this;}
}
const accessory={services:[],addService(Type,name,subtype){const s=new Type(name,subtype);this.services.push(s);return s;}};
const api={hap:{Service:{ContactSensor},Characteristic},on(){}};
const log={info(){},warn(){},error(){}};
let answer={ok:true,connection:'connected'};
const ipc=async(_socket,command)=>{assert.equal(command,'connection_status');if(answer instanceof Error)throw answer;return answer;};
(async()=>{
  const sensor=addConnectionSensor(api,accessory,'/tmp/sock',log,{ipc,setTimer:()=>null});
  const state=()=>[sensor.service.values.get(ContactSensorState),sensor.service.values.get(StatusFault)];
  assert.equal(sensor.service.subtype,'connection');
  assert.deepEqual(state(),[0,0]);
  answer={ok:true,connection:'unstable'};await sensor.poll();
  assert.deepEqual(state(),[1,1]);
  answer={ok:true,connection:'connected'};await sensor.poll();
  assert.deepEqual(state(),[0,0]);
  // An older listener without the command stays closed.
  answer={ok:false,error:'unsupported_command'};await sensor.poll();
  assert.deepEqual(state(),[0,0]);
  // The listener not answering: open only after a minute.
  answer=new Error('IPC timeout');
  for(let i=1;i<IPC_DOWN_POLLS;i++)await sensor.poll();
  assert.deepEqual(state(),[0,0]);
  await sensor.poll();
  assert.equal(sensor.status,'disconnected');
  assert.deepEqual(state(),[1,1]);
  console.log('CONNECTION_SENSOR_OK (mock IPC only)');
})().catch(err=>{console.error(err);process.exitCode=1;});
