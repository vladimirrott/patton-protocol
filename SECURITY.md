# Security Policy

Patton Protocol is a documentation-and-validator package: it defines a
protocol for coordinating coding-agent missions and ships a small,
dependency-free Python validator for that protocol's own structure. It does
not itself execute missions, spawn agents, or run arbitrary code; that
responsibility belongs to the host implementing the protocol.

## Reporting a vulnerability

If you find a security issue in `scripts/validate_protocol.py` (for example,
a path-traversal or resource-exhaustion issue when validating an untrusted
package tree), please report it privately rather than opening a public issue:
use GitHub's "Report a vulnerability" button under this repository's Security
tab, or open a draft security advisory. Include the input that triggers the
problem and, if possible, a minimal reproduction.

## Scope

The protocol's own safety model (bounded missions, immutable ownership
boundaries, mandatory human approval for irreversible actions, serial
fallback) is a design specification for host implementers, not a runtime
guarantee this repository enforces by itself. A security review of an actual
Patton Protocol implementation should evaluate the host's enforcement of that
specification, not just this repository's validator.
