---
playbook_id: resource_governance
category: resource_governance
trigger_conditions:
    - "a Deployment has fewer ready replicas than desired with no crashing pods"
    - "a FailedCreate / exceeded quota event is present"
    - "a pod was OOMKilled or is being CPU-throttled"
---

# Resource Governance Failure (Quota-Blocked Creation / Runtime OOM & CPU Pressure)

## Branch A: Quota-blocked creation

1. tool: get_events params: {namespace} conclusive: true
   conclusion_criteria: a FailedCreate event on the ReplicaSet mentioning 'exceeded quota'.
2. tool: get_resource params: {kind: RESOURCEQUOTA, name, namespace} conclusive: true
   conclusion_criteria: hard limit (e.g. pods=1) equals used, blocking further creation.
3. tool: get_resource params: {kind: DEPLOYMENT, name, namespace} conclusive: false
   note: desired replicas > ready/available confirms the shortfall is creation-blocked, not crashing.

### Conclusion (Branch A)

root_cause = a ResourceQuota caps a resource (e.g. pods) below what the Deployment requests, so
the ReplicaSet controller cannot create the remaining pods. Name the quota and the shortfall.

## Branch B: Runtime OOMKilled / CPU Throttling

Use this branch when the pod already exists and is Running or restarting — not when it was
never created (that's Branch A).

1. tool: describe_resource params: {kind: POD, name, namespace} conclusive: false
   note: check containerStatuses[].lastState.terminated for exitCode/reason, and restartCount.
2. tool: oom_killed_pods params: {namespace} conclusive: true
   conclusion_criteria: this pod/container is listed → confirmed OOMKilled. Go to Conclusion (B1).
3. tool: cpu_saturation params: {app_label} conclusive: true
   conclusion_criteria: >
   only relevant if the pod is Running (not restarting) but slow/underperforming. A value at or
   near 1 means the container is using all of its CPU limit — confirmed throttling. Go to
   Conclusion (B2).

### Conclusion (Branch B1 — OOMKilled)

root_cause = the container's memory limit is set below what its process actually needs, so the
kernel OOM-kills it (exitCode 137, reason OOMKilled). Name the container and its memory limit.

### Conclusion (Branch B2 — CPU Throttling)

root_cause = the container's CPU limit is set below what its workload actually demands, causing
heavy throttling. Name the container and its CPU limit. This is a performance issue, not a crash.

## Do not conclude

- The missing pods are crashlooping or failing health checks when they were never created (that's
  Branch A's mechanism, not Branch B's).
- Image pull failure.
- Insufficient node CPU or memory capacity (Branch A/B are about the container's own
  requests/limits, not node-level capacity — that's scheduling.md's territory).
- An application bug causing high CPU/memory usage when the limit itself is the constraint.
