---
playbook_id: secret_configmap
category: secret_configmap
trigger_conditions:
  - "a pod is stuck at CreateContainerConfigError or cannot start due to a missing env/volume source"
  - "an event mentions couldn't find key ... in Secret/ConfigMap"
---

# Secret / ConfigMap Reference Failure

## Checklist
1. tool: describe_resource   params: {kind: POD, name, namespace}   conclusive: false
   note: identify the referenced Secret/ConfigMap name and key from env/envFrom/volumes.
2. tool: get_events   params: {namespace}   conclusive: true
   conclusion_criteria: an event mentioning "couldn't find key <k> in Secret/ConfigMap <name>",
   or CreateContainerConfigError.
3. tool: get_resource   params: {kind: SECRET or CONFIGMAP, name, namespace}   conclusive: true
   conclusion_criteria: the object exists but the referenced key name is not present (mismatch).

## Conclusion
Report the missing reference as the root cause: "<pod> references key <k> in <kind> <name> which
does not exist" (name mismatch vs. genuinely absent object). Do not speculate about the GitOps
source — you cannot see it here.

## Do not conclude
- The Secret/ConfigMap itself does not exist (when it exists but the key is wrong).
- Image pull failure.
- Insufficient CPU or memory.
