'use strict';

/** Build the proxied video section expected by hap-nodejs 2.x. */
function prepareResponse(proxy, address = '127.0.0.1') {
  if (!proxy || !Number.isInteger(proxy.rtpPort) || !Number.isInteger(proxy.rtcpPort)) {
    throw new Error('RTP proxy ports are required');
  }
  return {
    addressOverride: address,
    video: {
      proxy_pt: 99,
      proxy_server_address: address,
      proxy_server_rtp: proxy.rtpPort,
      proxy_server_rtcp: proxy.rtcpPort,
    },
  };
}

module.exports = { prepareResponse };
