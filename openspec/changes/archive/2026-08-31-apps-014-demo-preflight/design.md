# Design

The new profile is deliberately an orchestration policy, not a second runner.
It composes the existing default gate and `_agent_check` lifecycle. Each agent
check creates an installed app session, waits for readiness, executes bounded
argv, requires an exact non-secret marker, pauses, resumes, and deletes in a
`finally` block.

For preflight, reviewed argv checks provider environment presence with shell
`test` and then prints the pinned CLI version. Shell source contains only fixed
environment variable names and the non-secret EU endpoint. No expansion occurs
in the caller: values exist only inside the provider-created workload. The
model profile keeps its inference argv and remains the only paid evidence.

One configuration variable remains sufficient:
`BARISTA_MANAGED_SMOKE_AGENT_CHECKS`. This avoids parallel configuration
formats and keeps the existing field, count, argv-size, and marker bounds. The
report's profile field distinguishes warm-up evidence from model execution.
