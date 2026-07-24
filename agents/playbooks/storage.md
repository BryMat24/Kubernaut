---
playbook_id: storage
category: storage
trigger_conditions:
  - "a PVC is stuck Pending or a pod cannot mount a volume"
  - "an event mentions a StorageClass could not be found or no volumes available"
---

# Storage / PVC Failure

## Checklist
1. tool: get_resource   params: {kind: PERSISTENTVOLUMECLAIM, name, namespace}   conclusive: false
   note: status Pending is the starting signal.
2. tool: get_events   params: {namespace}   conclusive: true
   conclusion_criteria: an event on the PVC/pod naming a StorageClass that could not be found,
   or 'no persistent volumes available'.
3. tool: list_resources   params: {kind: STORAGECLASS}   conclusive: true
   conclusion_criteria: the referenced StorageClass name is absent from the list.

## Conclusion
root_cause = the PVC references a StorageClass that does not exist, so no PV can be provisioned
or bound and the pod stays Pending. Name the PVC and the missing StorageClass.

## Do not conclude
- Insufficient CPU or memory.
- Image pull failure.
- A node taint or scheduling issue unrelated to storage.
