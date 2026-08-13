## What

<!-- Summarize the change. Keep the PR focused on one problem. -->

## Why

<!-- Explain the user or maintainer problem this solves. -->

## Related issue

<!-- Use "Closes #123" for an issue resolved by this PR. Non-trivial changes should normally have an issue or prior design discussion. -->

Closes #

## Scope and non-goals

<!-- State what is intentionally included and excluded. Call out unrelated refactors. -->

## Design and behavior changes

<!-- Describe important data flow, lifecycle, failure handling, concurrency, or operational changes. Include diagrams for substantial changes. -->

## Compatibility and operations

<!-- Explain applicable compatibility and deployment impact. Write "N/A" with a reason where appropriate. -->

- Public API or generated protocol:
- Configuration or defaults:
- Snapshot manifest, artifact layout, or storage format:
- Upgrade and rollback:
- Host requirements, permissions, ports, or dependencies:

## Validation

<!-- Check only commands you actually ran. Add focused tests and exact commands below. -->

- [ ] `make fmt`
- [ ] `make clippy`
- [ ] `make test-unit`
- [ ] Relevant Rust integration tests
- [ ] `make -C services test` (required when `services/` changes)
- [ ] Generated clients/server regenerated with the documented `make` target
- [ ] Documentation updated
- [ ] Benchmarks or performance comparison completed

Commands and results:

```text

```

Skipped checks and reasons:

## Risks and reviewer notes

<!-- Identify correctness, security, compatibility, resource, and operational risks. Point reviewers to the most important files or commits. -->

## Chip-sim change review (required)

<!-- Answer all three. A PR that cannot is rejected. See docs/src/vertical/chip-sim/design.md §13. -->

1. Which concrete **agent-loop** problem does this change solve?
2. Can it live in this repo’s vertical layer? Must AgentENV kernel change? (P2 evidence bars required for kernel edits.)
3. Is this P0 loop-blocking, or a P1/P2/P3 enhancement?

- [ ] Discussed the three questions before writing code (no drive-by implementation PRs)
