# Terminal SCMS development rules

## Monorepo boundaries

- `apps/minitos` owns planning, inventory, layout, routing, and master data.
- `apps/terminal-oes` owns dispatch, commands, and execution state.
- `clients/godot-open-terminal` simulates equipment and publishes telemetry and command results.
- `compose.yaml` is the source of truth for local orchestration. Make targets are convenience wrappers.
- Each Django service owns its database. Never query another service's database directly.
- Cross-service changes use versioned MQTT or HTTP contracts and idempotent inbox/outbox handling.

## Contract changes

When changing a model, serializer, view, URL, migration, MQTT event, or command:

1. Review both producer and consumer contracts.
2. Add focused regression tests in the owning application.
3. Run migration drift checks and the complete affected test suite.
4. Update contract and architecture documentation where behavior changes.
5. Preserve backward compatibility or version the event schema explicitly.

The additional MiniTOS rules in `apps/minitos/AGENTS.md` apply within that tree.
