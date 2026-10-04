'use strict';
const {request}=require('./ipc');

function installCallUnlock(api,manager,config,log) {
  const {LockCurrentState:Current,LockTargetState:Target}=api.hap.Characteristic;
  const service=new api.hap.Service.LockMechanism('Apri ingresso in chiamata','incoming-unlock');
  // User-selected assumed pulse state, NOT feedback from a physical sensor.
  let state=Current.SECURED,busy=false,timer;
  service.getCharacteristic(Current).onGet(()=>state);
  service.updateCharacteristic(Current,state);
  service.updateCharacteristic(Target,Target.SECURED);
  api.on?.('shutdown',()=>clearTimeout(timer));
  service.getCharacteristic(Target).onGet(()=>state===Current.UNSECURED?Target.UNSECURED:Target.SECURED).onSet(async value=>{
    if(value===Target.SECURED)return; // rearming the pulse does not lock a door
    if(value!==Target.UNSECURED)throw Error('Stato apertura non valido');
    if(busy||timer)throw Error('Apertura già in corso');
    const calls=[...manager.sessions.values()].filter(s=>s.incoming&&s.state==='streaming');
    if(calls.length!==1)throw Error('Rispondi prima alla chiamata da Casa');
    busy=true;
    try {
      const result=await request(config.ipcSocket||'/tmp/bticino-hometouch.sock','open_incoming',{session_id:calls[0].id},3000);
      if(!result.ok)throw Error(result.error||'Apertura rifiutata');
      log.info('Comando apertura inviato; apertura fisica non confermata');
      state=Current.UNSECURED;
      service.updateCharacteristic(Current,state);
      timer=setTimeout(()=>{
        timer=undefined;
        state=Current.SECURED;
        service.updateCharacteristic(Current,state);
        service.updateCharacteristic(Target,Target.SECURED);
      },3000);
      timer.unref?.();
    } finally {
      busy=false;
    }
  });
  return service;
}
module.exports={installCallUnlock};
