---
playbook_id: rbac
category: rbac
trigger_conditions:
  - "a workload's logs or an API call return a Forbidden / cannot <verb> resource error"
  - "the pod runs under a non-default ServiceAccount"
---

# RBAC / Permission Failure

## Checklist
1. tool: get_pod_logs (or get_previous_logs if restarted)   conclusive: true
   conclusion_criteria: >
     logs contain a Forbidden error naming the ServiceAccount and the denied verb/resource,
     e.g. 'pods is forbidden: User "system:serviceaccount:<ns>:<sa>" cannot list resource'.
     The Forbidden text is itself the root cause.
2. tool: get_events   params: {namespace}   conclusive: false
   note: only if logs are unavailable.

## Conclusion
root_cause = the ServiceAccount lacks the RBAC permission named in the Forbidden error. Do NOT
enumerate ClusterRoles/RoleBindings one kind at a time — the error already names what's missing.
There is no ServiceAccount kind in get_resource; do not retry that call.

## Do not conclude
- Image pull failure.
- An application code bug unrelated to permissions.
- Insufficient CPU or memory.
