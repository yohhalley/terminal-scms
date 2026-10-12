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

Phase 1b has been answered: Timebase must be reachable from another computer on
the local network. The intended topology is a Linux Docker host accessed from
the development Mac. Do not generate host-specific `.env` or Compose files until
the Linux host address, DNS name, storage path, CPU architecture, and Docker
versions have been checked.

Use a static DHCP reservation and a local DNS name for the Linux host. Keep the
database and administrative ports restricted to the LAN; remote access should
use a private overlay such as Tailscale or WireGuard instead of public port
forwarding.

After that answer, the runbook separately asks whether the Timebase AI assistant should use Anthropic, another provider, or remain unconfigured. Credentials and component client secrets must be entered by the user in the relevant Timebase interfaces and must never be placed in this repository.
