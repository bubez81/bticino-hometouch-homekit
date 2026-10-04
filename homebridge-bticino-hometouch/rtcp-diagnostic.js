'use strict';
const crypto = require('node:crypto');
function derive(key, salt, label, length) {
  const iv = Buffer.alloc(16); salt.copy(iv); iv[7] ^= label;
  return crypto.createCipheriv('aes-128-ctr', key, iv).update(Buffer.alloc(length));
}
// Authenticate before parsing. Never log keys, raw packets or user media.
function inspect(packet, key, salt) {
  if (packet.length < 22) return {error:'short'};
  const signed = packet.subarray(0,-10);
  const tag = crypto.createHmac('sha1',derive(key,salt,4,20)).update(signed).digest().subarray(0,10);
  if (!crypto.timingSafeEqual(tag,packet.subarray(-10))) return {error:'authentication'};
  const word = packet.readUInt32BE(packet.length-14), index=word & 0x7fffffff;
  let body = packet.subarray(0,-14);
  if (word >>> 31) {
    const iv=Buffer.alloc(16); derive(key,salt,5,14).copy(iv);
    const mask=Buffer.alloc(16); packet.copy(mask,4,4,8); mask.writeUInt32BE(index,10);
    for(let i=0;i<16;i++)iv[i]^=mask[i];
    body=Buffer.concat([body.subarray(0,8),crypto.createDecipheriv('aes-128-ctr',derive(key,salt,3,16),iv).update(body.subarray(8))]);
  }
  const reports=[];
  for(let offset=0;offset+4<=body.length;) {
    const type=body[offset+1], count=body[offset]&31, size=(body.readUInt16BE(offset+2)+1)*4;
    if(size<4 || offset+size>body.length)return {error:'layout'};
    const report={type,count};
    if(type===201 && count && size>=32)Object.assign(report,{source:body.readUInt32BE(offset+8),fractionLost:body[offset+12],highestSequence:body.readUInt32BE(offset+16),jitter:body.readUInt32BE(offset+20)});
    reports.push(report);offset+=size;
  }
  return {index,reports};
}
module.exports={inspect};
