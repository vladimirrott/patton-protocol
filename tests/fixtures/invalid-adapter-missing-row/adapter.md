# Invalid Adapter

## Capability matrix

| Capability | Status | Mapping |
| --- | --- | --- |
| Loading | `native` | discovery |
| Worker definition | `native` | worker |
| Parallel dispatch | `native` | dispatch |
| Serial fallback | `prompt-mediated` | main loop |
| Approval boundary | `prompt-mediated` | human gate |

Explicit human approval is required before an irreversible action. Nested
worker spawn remains unavailable without host proof.
