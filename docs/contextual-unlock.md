# Contextual opening — experimental

Static evidence: official Android VctActivityLinphone.onCreate constructs the
SlideToTrigView handler linphone.c without MhpDevice. Its a() passes an empty
address and CID 10060 to VctLinphoneService.P. That method sends the pair
`*8*19*4##`, `*8*20*4##` as chat messages to sip:MHT@the-gateway-domain.
Explicit-device activations instead concatenate device type/address.
This establishes the app's generic command path, not the gateway's physical
selection behavior for our endpoint.

Implementation is disabled by default on both sides: plugin enableCallUnlock
and listener incoming_unlock must both be true.
UI now uses a LockMechanism (2026-09-22), with UNKNOWN physical state because
there is no contact sensor. The target resets to SECURED to rearm the pulse;
this neither locks the door nor confirms its physical state. Opening requires a
single incoming dialog established by ACK, answered by the same HomeKit session
which still owns its media attachment. Ringing, unacknowledged answers, outgoing
camera previews, ended calls and ambiguous ownership are rejected.
One attempt per dialog; partial/uncertain sends are never retried automatically.
IPC reports sent_unconfirmed, not physical success. SIP authentication challenges
and delivery confirmations are not handled for these messages yet.

Tests use mock sending/IPC only. Physical validation of correct entrance remains
necessary before production enablement. No real opening was sent during coding.

## Call handling update — 2026-09-22

Opening the incoming live stream explicitly accepts the SIP call after media
preparation, with the microphone gate still closed. The HomeKit talk control
enables outgoing audio separately. Merely preparing a stream or requesting a
snapshot does not answer. Closing the answered stream releases the call.
This changes the earlier preview-only behavior: opening live can take the call
away from other clients. Gateway call duration remains outside our control.

Installed backup: /opt/bticino-sniffer/backups/homekit-call-fix-083_gtjv.
Lock, 7 listener, 8 dialog, 5 opening guard tests and loopback adapter tests pass.
Real Home UI placement, sustained call audio and physical opening are not yet
validated. No real unlock or synthetic ring was sent during deployment.

## Cubetto deployment — 2026-09-19 21:07 CEST

Installed and enabled in the dedicated /opt/bticino-homebridge instance and
/opt/bticino-sniffer listener. Backup: /opt/bticino-sniffer/backups/call-unlock-y8gbvfe4.
The bridge now runs under launchd label io.github.bubez81.bticino-homebridge,
with RunAtLoad/KeepAlive and logs in /opt/bticino-homebridge/stdout.log and stderr.log.
Startup, port 51991, IPC connection and successful SIP registration verified.
No opening request was sent. Physical entrance selection remains unverified.

## Separate accessory — 2026-09-22 21:44 CEST

Installed with backup /opt/bticino-sniffer/backups/separate-lock-ryrx54of.
The existing camera identity is unchanged. With separateCallUnlock:true and
enableCallUnlock:true, the camera exposes no embedded lock. A second static
BTicinoCallLock accessory named "Apri ingresso" resolves the camera manager by
cameraName and ipcSocket, sharing its session ownership checks. No new listener
or second event consumer is created. Tests reject missing cameras, missing calls
and stopping sessions. The installed identifier cache confirms distinct camera
and lock identities. Bridge restart and IPC connection verified.

In Apple Home assign the new lock to the same room as the camera. This room
assignment cannot be set through this Homebridge accessory configuration.
The real notification UI and physical opening are still pending verification.
The physical lock state remains UNKNOWN, not a claimed closed/open position.

## User-requested assumed pulse display

Supersedes the UNKNOWN display above: at the user's explicit request, display
SECURED at rest, UNSECURED only after successful command submission, and SECURED
again after 3 seconds. These are assumed UI states, never physical sensor feedback
or confirmation of delivery. The timeout sends no command. Failed requests do not
show UNSECURED; repeated requests during the pulse are rejected. Existing session
ownership and once-per-dialog backend guards remain unchanged.
Installed backup: /opt/bticino-sniffer/backups/lock-pulse-ejny25_d.
Mock tests verify getters, timeout reset, rejected requests, and no timeout IPC.
