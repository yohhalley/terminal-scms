# FlowFuse deployment boundary

The official self-hosted FlowFuse Docker installation requires a real base domain and wildcard DNS. It also operates its own internal platform broker. Replacing that broker with HiveMQ is not a supported shortcut.

The intended integration is:

1. Deploy FlowFuse with its official installer and platform prerequisites.
2. Create Node-RED application instances in FlowFuse.
3. Configure those Node-RED flows with MQTT nodes that connect to the SCMS HiveMQ endpoint.
4. Subscribe only to the terminal topics needed by each process.
5. Publish process results using versioned `scms/v1/...` topics or authenticated REST APIs.

This keeps FlowFuse control-plane traffic separate from terminal operational messages. A concrete deployment file should be generated only after choosing the domain, wildcard DNS record, TLS approach, and whether the installation is local-only or shared.

