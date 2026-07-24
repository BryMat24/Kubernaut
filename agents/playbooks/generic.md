---
playbook_id: generic
category: fallback
trigger_conditions: []
---

# Generic Investigation

Use this when no specialized playbook matches the scope signals.

## Checklist
1. If a specific resource/workload is named, `describe_resource` it first; otherwise
   `list_resources` in the namespace to identify the affected workload.
2. For a failing or not-ready workload, `describe_resource` the Pod, then `get_events` in the
   namespace for the failure reason.
3. If the container is running, `get_pod_logs`; if it has restarted, `get_previous_logs`.
4. Treat an explicit failure string in events or logs — "Forbidden", "OOMKilled",
   "CrashLoopBackOff", "ImagePullBackOff", "Evicted", "FailedScheduling",
   "FailedGetResourceMetric", "CreateContainerConfigError" — as sufficient evidence of the
   mechanism. Stop confirming it further and conclude.

## Conclusion
Name the concrete mechanism and the specific resource that causes it. If evidence is
insufficient, say exactly what is missing rather than guessing.

## Do not conclude
- Do not assert a cause behind a missing/misreferenced resource you cannot see the source of.
- Do not blame infrastructure (metrics-server, node capacity) when a workload-level cause fits.
