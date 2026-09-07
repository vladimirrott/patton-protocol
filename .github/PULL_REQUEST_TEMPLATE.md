## What this changes and why

<!-- One or two sentences. If this touches the approval boundary or candidate-identity rules, name the specific gap it closes. -->

## Checklist

- [ ] `python3 -m unittest discover -s tests -v` passes locally
- [ ] `python3 scripts/validate_protocol.py .` reports `valid`
- [ ] A new test or fixture covers this change (see CONTRIBUTING.md's TDD workflow)
- [ ] If a fixture is meant to fail a specific check, I confirmed it fails for that reason, not by accident
- [ ] Any changed adapter still has exactly one row per required capability, with an allowed status
