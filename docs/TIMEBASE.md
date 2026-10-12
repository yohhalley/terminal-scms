# Timebase 2.0 deployment status

Source: the official **Timebase 2.0 Docker Agent Runbook**, updated 7 October 2026.

Timebase 2.0 is currently described by its documentation as a beta release. The runbook warns not to use the stack in production and not to assume migration compatibility with a future production release.

## Phase 1 result

- Docker: 29.7.2
- Docker Compose: v5.4.0
- Python: 3.14.6
- Host: 10 CPU cores and 32 GB RAM
- Documented minimum: 4 cores and 8 GB RAM
- Required ports 4511-4512 through 4571-4572: free

Phase 1b is paused at the required human decision: whether browsers will access Timebase only on this computer or from other computers on the network. The runbook requires that answer before writing `timebase/.env` or its Compose file.

After that answer, the runbook separately asks whether the Timebase AI assistant should use Anthropic, another provider, or remain unconfigured. Credentials and component client secrets must be entered by the user in the relevant Timebase interfaces and must never be placed in this repository.

