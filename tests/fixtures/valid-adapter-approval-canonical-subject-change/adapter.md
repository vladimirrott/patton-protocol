# Valid Adapter

## Capability matrix

| Capability | Status | Mapping |
| --- | --- | --- |
| Loading | `native` | discovery |
| Worker definition | `native` | worker |
| Parallel dispatch | `native` | dispatch |
| Serial fallback | `prompt-mediated` | main loop |
| Approval boundary | `prompt-mediated` | gate |
| Nested worker spawn | `native` | configured nested spawn |

Host-only approval does not satisfy or replace explicit human approval and
cannot authorize an irreversible action. Human approval authorizes the
irreversible action. Nested worker spawn is native when the tool and policy
permit it.
