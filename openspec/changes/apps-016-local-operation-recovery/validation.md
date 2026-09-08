# Validation

Mutants run individually; each file restored from a saved fixed copy in a finally block.

| Mutation | Test | Result |
|---|---|---|
| Invalid node ID | `test_create_uses_node_ulid` | Failed as expected |
| Reused pause key | `test_grpc_adapter_new_cycles_use_new_keys` | Failed as expected |
| Delete uncertain creation | `test_start_retry_after_restart_preserves_node_and_original_request` | Failed as expected |
| Forget creation retry key | `test_start_retry_after_restart_preserves_node_and_original_request` | Failed as expected |
| Ignore persisted lifecycle key | `test_lifecycle_retry_after_restart_reuses_operation_key` | Failed as expected |

Final checks: local provider 21 passed (including Cloud-blocked conformance); contracts 74 passed; conformance self-tests 22 passed; SDK 68 passed. Supply-chain check and strict OpenSpec validation passed. No live hypervisor was used; gRPC lifecycle replay behavior was exercised through the real adapter with a deterministic Contract A test double.
