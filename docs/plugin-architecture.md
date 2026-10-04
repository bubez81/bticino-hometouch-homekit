# Standalone Homebridge plugin

The `homebridge-bticino-hometouch/` package is intentionally separate from
`homebridge-camera-ffmpeg`. It will run as a child bridge and own the BTicino
SIP/RTP session, so installing it cannot replace a user's global FFmpeg
processor or duplicate a dynamic Camera-ffmpeg platform.

Implementation order:

1. Connect the existing Python SIP transport through a small local IPC
   protocol (Unix socket, loopback only).
2. Expose one BTicino doorbell accessory and its camera service from this
   platform.
3. Add entrance-specific lock controls only after the CID mapping is verified.
4. Add an installer that validates the user's Homebridge JSON and writes only
   a child-bridge entry, with an automatic backup.

No credentials, certificates, snapshots or gateway identifiers belong in this
package or in the Git repository.
