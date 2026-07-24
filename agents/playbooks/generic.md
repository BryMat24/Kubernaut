---
playbook_id: generic
category: fallback
trigger_conditions: []
---

# Generic Investigation (Pod Lifecycle / Fallback)

Use this when no specialized playbook matches the scope signals — this covers Pod Lifecycle
startup/crash failures (CrashLoopBackOff, ImagePullBackOff, ErrImagePull, CreateContainerError)
plus anything else with no dedicated playbook.

## Checklist

### 1. Identify the affected workload

tool: describe_resource params: {kind: POD, name, namespace} conclusive: false
(if no specific pod is named: list_resources params: {kind: POD, namespace} first)

Check: phase, containerStatuses[].state (current) and lastState (most recent previous
termination — exitCode/reason), restartCount.

### 2. Check recent events

tool: get_events params: {namespace} conclusive: false
note: read the event `reason` field verbatim — it names which branch you're in
(CrashLoopBackOff / ImagePullBackOff / ErrImagePull / Failed / BackOff).

### 3a. Branch: CrashLoopBackOff

tool: error_logs (or recent_logs if the failure is outside error_logs's window)
params: {app_label, namespace} conclusive: true

conclusion_criteria:

- lastState.terminated.exitCode == 137, or an OOMKilled event/log line → this is
  resource_governance's OOMKilled mechanism, not a generic crash. Say so and stop — do not
  re-diagnose it here.
- logs show an unhandled exception/stack trace → application-level crash. Name the exception.
- logs are clean but events show a failed liveness/readiness probe → probe misconfiguration,
  not an app crash. Name the probe field likely at fault.
- no useful logs are returned and restartCount is climbing with no app output at all → go to 3b.

### 3b. Branch: CreateContainerError (container never starts)

tool: recent_logs params: {app_label, namespace} conclusive: false
note: if this also returns nothing, the container never ran long enough to log anything —
that absence is itself evidence, not a dead end.

tool: describe_resource params: {kind: POD, name, namespace} conclusive: true
conclusion_criteria: waiting.reason is CreateContainerError, or an event names a bad
command/entrypoint, missing mount, or permission denied.

### 3c. Branch: ImagePullBackOff / ErrImagePull

tool: get_events params: {namespace} conclusive: true
conclusion_criteria (read the message verbatim):

- "manifest unknown" / "not found" → wrong image name or tag.
- "unauthorized" / "authentication required" → missing or incorrect imagePullSecret.
- "connection refused" / "timeout" / "no such host" → registry unreachable.

tool: describe_resource params: {kind: POD, name, namespace} conclusive: false
note: confirm the exact image:tag requested and whether imagePullSecrets is set.

## Conclusion

Name exactly one mechanism: application exception (name it) · failing liveness/readiness probe
(name the config) · CreateContainerError (name the failure) · ImagePullBackOff/ErrImagePull
(bad tag / missing imagePullSecret / unreachable registry — name which). If evidence points to
OOMKilled or CreateContainerConfigError, say so and defer — resource_governance and
secret_configmap own those mechanisms, do not re-derive them here.

## Do not conclude

- OOMKilled without exitCode 137 or an explicit OOMKilled event/log line.
- A missing Secret/ConfigMap key (that's CreateContainerConfigError — secret_configmap territory).
- A ResourceQuota block (the pod would never have been created at all).
- Runtime CPU/memory throttling on an otherwise-Running pod (resource_governance's territory).
