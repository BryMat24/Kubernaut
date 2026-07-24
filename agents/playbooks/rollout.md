---
playbook_id: rollout
category: rollout
trigger_conditions:
  - "a Deployment rollout completed but the application still misbehaves"
  - "5xx/errors despite Running, ready pods with no restarts"
---

# Rollout-Complete-But-Broken Failure

## Checklist
1. tool: rollout_status   params: {deployment, namespace}   conclusive: false
   note: a COMPLETE rollout does NOT prove the app is healthy — keep going.
2. tool: describe_resource   params: {kind: POD, name, namespace}   conclusive: false
   note: Running with no restarts rules out crashloop/scheduling/image-pull.
3. tool: get_pod_logs (or recent_logs / error_logs by app)   conclusive: true
   conclusion_criteria: repeated FATAL / "unable to connect to upstream dependency" style
   application errors on an otherwise-Running pod.

## Conclusion
root_cause = the rollout succeeded but the application is functionally broken (e.g. cannot reach
a dependency). This is an application-level fault, not a rollout/scheduling/image failure — so it
is typically NOT remediable via a Kubernetes manifest change.

## Do not conclude
- The rollout itself failed, is stuck, or is still in progress.
- The pod is crashlooping or failing to start.
- An image pull or scheduling failure occurred.
