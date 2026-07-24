---
playbook_id: storage
category: storage
trigger_conditions:
    - "a PVC is stuck Pending or a pod cannot mount a volume"
    - "an event mentions a StorageClass could not be found or no volumes available"
    - "an event mentions a Multi-Attach error for a volume"
---

# Storage / PVC Failure

## Branch A: PVC Pending — missing StorageClass

1. tool: get_resource params: {kind: PERSISTENTVOLUMECLAIM, name, namespace} conclusive: false
   note: status Pending is the starting signal.
2. tool: get_events params: {namespace} conclusive: true
   conclusion_criteria: an event on the PVC/pod naming a StorageClass that could not be found,
   or 'no persistent volumes available'.
3. tool: list_resources params: {kind: STORAGECLASS} conclusive: true
   conclusion_criteria: the referenced StorageClass name is absent from the list.

### Conclusion (Branch A)

root_cause = the PVC references a StorageClass that does not exist, so no PV can be provisioned
or bound and the pod stays Pending. Name the PVC and the missing StorageClass.

## Branch B: Multi-Attach Error

1. tool: get_events params: {namespace} conclusive: true
   conclusion_criteria: >
   an event mentioning "Multi-Attach error for volume ... Volume is already exclusively
   attached to one node and can't be attached to another" — this occurs when a
   ReadWriteOnce PVC's pod is rescheduled to a different node before the volume detaches from
   the old one (e.g. after a node failure or a fast reschedule).
2. tool: describe_resource params: {kind: POD, name, namespace} conclusive: false
   note: confirm the pod is stuck in ContainerCreating and which node it's scheduled to now.

### Conclusion (Branch B)

root_cause = the PVC is ReadWriteOnce and still attached to its previous node; the new pod can't
mount it until the old attachment detaches (or the old node is confirmed gone). Name the PVC and
both the old and new node if visible in the event.

## Do not conclude

- Insufficient CPU or memory.
- Image pull failure.
- A node taint or scheduling issue unrelated to storage.
