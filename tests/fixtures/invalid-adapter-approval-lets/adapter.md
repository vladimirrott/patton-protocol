# Invalid Adapter

## Capability matrix

| Capability | Status | Mapping |
| --- | --- | --- |
| Loading | `native` | discovery |
| Worker definition | `native` | worker |
| Parallel dispatch | `native` | dispatch |
| Serial fallback | `prompt-mediated` | main loop |
| Approval boundary | `prompt-mediated` | gate |
| Nested worker spawn | `native` | configured nested spawn |

Host-only approval does not satisfy the gate but lets Prime perform the
irreversible action. Nested worker spawn is native when the tool and policy
permit it.
