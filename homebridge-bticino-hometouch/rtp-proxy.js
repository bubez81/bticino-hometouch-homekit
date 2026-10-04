'use strict';

const dgram = require('node:dgram');

class RtpProxy {
  constructor(log, host = '127.0.0.1') { this.log = log; this.host = host; this.sessions = new Map(); }

  async allocate() {
    const rtp = dgram.createSocket('udp4');
    const rtcp = dgram.createSocket('udp4');
    await new Promise((resolve, reject) => { rtp.once('error', reject); rtp.bind(0, this.host, resolve); });
    await new Promise((resolve, reject) => { rtcp.once('error', reject); rtcp.bind(0, this.host, resolve); });
    const id = `${rtp.address().port}:${rtcp.address().port}`;
    this.sessions.set(id, { rtp, rtcp });
    return { id, rtpPort: rtp.address().port, rtcpPort: rtcp.address().port };
  }

  connect(id, homekit, bticino) {
    const session = this.sessions.get(id);
    if (!session) throw new Error('Unknown RTP session');
    const bindForwarder = (socket, home, remote) => socket.on('message', (packet, info) => {
      const target = info.port === remote.port ? home : remote;
      socket.send(packet, target.port, target.host);
    });
    bindForwarder(session.rtp, homekit.rtp, bticino.rtp);
    bindForwarder(session.rtcp, homekit.rtcp, bticino.rtcp);
    session.homekit = homekit;
    session.bticino = bticino;
  }

  close(id) {
    const session = this.sessions.get(id);
    if (!session) return;
    session.rtp.close(); session.rtcp.close(); this.sessions.delete(id);
  }

  closeAll() { for (const id of this.sessions.keys()) this.close(id); }
}

module.exports = { RtpProxy };
