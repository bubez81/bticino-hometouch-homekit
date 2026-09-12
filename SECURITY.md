# Security and privacy

This project handles SIP credentials, TLS private keys, SDES key material and
images captured at a private entrance. Treat all runtime output as sensitive.

## Never publish

- SIP credentials, account identifiers or authentication challenges
- TLS certificates paired with private keys
- `a=crypto` lines from real SDP messages
- raw SIP captures, packet captures, logs or snapshots
- real public/private IP addresses, hostnames or serial numbers
- HomeKit setup codes and Homebridge configuration files

The repository ignores common sensitive file types, but `.gitignore` is only a
last line of defence. Review staged changes with `git diff --cached` before
every push.

Raw SIP persistence is disabled by default. Enable `save_raw_sip` only for
short, controlled diagnostics and remove the resulting files afterwards.

Entrance diagnostics use an installation-local HMAC key stored with mode 600.
Logs contain only short keyed fingerprints of selected SIP/SDP metadata, so
calls can be compared without recording the original identifiers. The key and
the fingerprints are still private runtime data and must not be committed.

Visual entrance profiles are installation-specific images and must remain in
the private service directory with mode 600. They are never part of the source
tree. Classification runs locally and temporary reduced frames are deleted at
the end of each media session.

## Reporting vulnerabilities

Do not open a public issue containing secrets, captures or household details.
Use GitHub's private security advisory feature instead.
