# Guided onboarding design

## Goal

A new user should be able to install the bridge without extracting a family
member's application database, copying private keys from a phone, intercepting
TLS traffic or modifying HOMETOUCH firmware.

The recommended identity model is one dedicated Door Entry account per bridge.
The plant owner invites that account using the supported Door Entry sharing
flow. A dedicated email address or alias makes the bridge independently
revocable and keeps it separate from personal users.

## Intended flow

1. Create a dedicated Door Entry/Legrand user and accept the plant invitation.
2. Run `./scripts/bticino-onboard --email dedicated-account@example.com` on
   the bridge host. Without `--apply` it performs read-only discovery only.
3. Sign in through the normal authorization flow. Prefer browser-based OAuth;
   never persist the account password. If the legacy service requires a
   password grant, hold the password in memory only and never log it.
4. List the plants and gateways visible to that account and ask the installer
   to choose one.
5. Create a new SIP account/device for this bridge instead of reusing an
   existing phone's SIP identity.
6. Generate an EC P-256 private key locally and create a certificate signing
   request. The private key never leaves the bridge.
7. Submit the CSR through the supported certificate service and save the
   returned client certificate and CA chain.
8. Write the SIP username/password, certificate paths and gateway selection to
   the user's private configuration directory using mode `600`, together with
   a ready-to-use listener `config.json`. Print only redacted identifiers.
9. Verify TLS registration and provide a revoke/remove instruction for the
   dedicated client.

## Commands

Run discovery first; it does not change cloud data:

```sh
./scripts/bticino-onboard --email dedicated-account@example.com
```

Only after confirming the plant and that an endpoint slot is available:

```sh
./scripts/bticino-onboard --email dedicated-account@example.com --apply
```

The default private output is `~/.config/bticino-hometouch/`. Override it with
`--output` or `XDG_CONFIG_HOME`. Start the listener directly with:

```sh
BTICINO_SNIFFER_CONFIG="$HOME/.config/bticino-hometouch/config.json" \
  python3 src/bticino_hometouch_listener.py
```

Provisioning creates a cloud endpoint before requesting its certificate. If
certificate enrollment fails, do not blindly repeat `--apply`: first run the
read-only endpoint listing and seek help, otherwise a retry may consume another
slot.

## Troubleshooting invited-account discovery

An accepted invitation may work in the official Door Entry app while an older
version of this tool reports that no plant is visible. Update the repository
and repeat the non-mutating command first:

```sh
git pull
./scripts/bticino-onboard --email dedicated-account@example.com
```

The current parser searches known direct and nested plant response layouts and
also checks the invitations collection. If the result is still empty, collect
the privacy-safe structural diagnostic:

```sh
./scripts/bticino-onboard --email dedicated-account@example.com \
  --diagnose-discovery
```

Only the line beginning with `Diagnosi discovery` is intended for an issue or
support message. It contains response container types and counts only. It does
not contain JSON keys or values, plant/gateway identifiers, email addresses,
credentials, session tokens or invitation contents. The command performs only
authenticated reads and never provisions an endpoint.

Before reporting a problem, confirm that the invitation was accepted inside
the dedicated account rather than merely received by email. Never use
`--apply` as a discovery workaround.

## Evidence and current limits

Static analysis of the HOMETOUCH Door Entry Android application exposes
operations named `askSipAccounts`, `askTokensForPlant`, `createMySipAccount`,
`generateCertificate` and `getTLSCertificate`, together with local SIP-user
fields and TLS certificate filenames. This is strong evidence that normal app
provisioning can supply the required material without a firmware hack.

The legacy HOMETOUCH client uses authenticated Eliot endpoints for sign-in,
plant discovery, SIP endpoint creation and CSR signing. The first client
implementation follows those request formats. Compatibility of every
HOMETOUCH firmware/cloud generation and the server's current behaviour still
need verification with an account created specifically for testing.

Documentation for a newer related BTicino integration describes the same
general model: local key generation, a CSR sent to the certificate service,
and separate cloud-created SIP accounts. It is useful corroboration, not a
drop-in HOMETOUCH API specification.

## Privacy requirements

- Never request or export a family member's existing SIP account.
- Never upload a private key, runtime configuration, SIP/SDP capture or image.
- Redact access tokens, refresh tokens, SIP passwords, account identifiers and
  certificate subjects from diagnostics.
- Store secrets outside the source tree with owner-only permissions.
- Make account/device revocation part of the uninstall documentation.
- Refuse insecure TLS, certificate-validation bypasses and instructions that
  depend on old vulnerable application versions.
- The legacy cloud can accumulate stale SIP endpoints but exposes no verified
  per-endpoint removal operation. The tool must never guess a destructive API;
  at capacity, ask the vendor to purge stale devices or remove only a plant
  user that the owner has explicitly decided no longer needs access.
- The tested HOMETOUCH cloud limit is 20 provisioned SIP endpoints. Onboarding
  detects this before creation and refuses to make a request when full.

## Implementation milestones

- Verify read-only login, plant and gateway discovery against the live service. (done)
- Verify explicit `--apply` SIP-account creation and local CSR enrollment.
- Expand redacted diagnostics and rollback/revocation guidance. (in progress)
- Test with a dedicated invited account on a clean installation.
