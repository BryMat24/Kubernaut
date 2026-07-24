---
playbook_id: resource_governance
category: resource_governance
trigger_conditions:
  - "a Deployment has fewer ready replicas than desired with no crashing pods"
  - "a FailedCreate / exceeded quota event is present"
---

# ResourceQuota / Governance Failure

## Checklist
1. tool: get_events   params: {namespace}   conclusive: true
   conclusion_criteria: a FailedCreate event on the ReplicaSet mentioning 'exceeded quota'.
2. tool: get_resource   params: {kind: RESOURCEQUOTA, name, namespace}   conclusive: true
   conclusion_criteria: hard limit (e.g. pods=1) equals used, blocking further creation.
3. tool: get_resource   params: {kind: DEPLOYMENT, name, namespace}   conclusive: false
   note: desired replicas > ready/available confirms the shortfall is creation-blocked, not crashing.

## Conclusion
root_cause = a ResourceQuota caps a resource (e.g. pods) below what the Deployment requests, so
the ReplicaSet controller cannot create the remaining pods. Name the quota and the shortfall.

## Do not conclude
- The missing pods are crashlooping or failing health checks (they were never created).
- Image pull failure.
- Insufficient node CPU or memory capacity.
