# Entrance signaling investigation — 2026-09-16

## Observed in the locally retained official Android DEX

- `Ly0/n;->r(String, Call, State)` (`VctHomepageFragment`, updateCallStatus)
  reads `Call.getRemoteParams().getCustomSdpAttribute("DEVADDR")` into `W`.
- `Ly0/n;->z0()` (`updatePEInfoAndArrows`) uses W when a managed call ID L
  exists. It splits the first character from the rest, compares them with
  `MhpDevice.g()` and `.f()`, and uses `.m()` as the displayed name.
- `Ly0/n;->s0()` explicitly constructs outbound DEVADDR from those two fields.
- `Ly0/n;->k()` first checks the current managed call; its fallback `.b0()`
  is explicitly logged as `getCurrentOutgoingCall()`.

This demonstrates a remote-parameter-to-device-name mapping, **not** that all
incoming rings carry DEVADDR or that the app itself selects their video source.
The latter could be selected upstream. Do not claim the two physical entrances
are identified until known calls actually provide a matching stable value.

## Existing records and gap

Cubetto's preserved SIP files contain 26 INVITEs and 25 CANCELs. A redacted scan
found zero DEVADDR-bearing messages. No later responses/updates are present in
that archive. The archive alone cannot establish what was exchanged later in
the calls or what the official app received on its own endpoint.

## Installed observation

`entrance_signaling_diagnostics=true` on Cubetto. `SIPStream.read_message` now
observes received messages before any register/dialog/request dispatch. Logs
`ENTRANCE SIGNAL v2` in listener.log/stdout: keyed Call-ID token, method, status,
CSeq, body/SDP presence, keyed DEVADDR token and session/media scope. Matches
against the existing candidate configuration expose only candidate indices;
indices are not entrance names or opening-control mappings. No raw credentials,
SDES keys, raw SDP, or device addresses are logged.

Backup: `/opt/bticino-sniffer/backups/signaling-observation-2p_24wfn`.
Installed while idle; IPC ping verified after restart. Four observer tests pass.
No call or opening was initiated for this investigation.

Next evidence is a naturally occurring incoming call, correlated across all
received messages. Absence on this endpoint would still not prove absence on
the official app endpoint. Comparing those paths is a separate subsequent step.
