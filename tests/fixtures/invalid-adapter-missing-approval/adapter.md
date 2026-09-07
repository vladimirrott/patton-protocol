# Invalid Adapter

## Capability matrix

| Capability | Status | Mapping |
| --- | --- | --- |
| Loading | `native` | discovery |
| Worker definition | `native` | worker |
| Parallel dispatch | `native` | dispatch |
| Serial fallback | `prompt-mediated` | main loop |
| Approval boundary | `prompt-mediated` | gate |
| Nested worker spawn | `unavailable` | no recursive spawn |

Nested worker spawn remains unavailable without host proof. The adapter leaves
irreversible actions to the host.
