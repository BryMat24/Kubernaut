# Kubernetes Failure Categories & Diagnosis Playbooks

This document defines the supported Kubernetes failure categories for the diagnosis agent. Each category maps to a playbook containing deterministic investigation steps before the LLM performs reasoning and remediation.

---

## Pod Lifecycle

| Error                      | Agent Actions                                                                                                             |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| CrashLoopBackOff           | Analyze current & previous logs, inspect exit code, container status, events, identify root cause, recommend or apply fix |
| ImagePullBackOff           | Verify image exists, validate image tag, inspect registry authentication, check `imagePullSecrets`                        |
| ErrImagePull               | Check registry connectivity, credentials, repository existence, network reachability                                      |
| CreateContainerConfigError | Detect missing ConfigMaps, Secrets, environment variables, invalid configuration references                               |
| CreateContainerError       | Inspect entrypoint, command, filesystem mounts, permissions, runtime failures                                             |

---

## Scheduling

| Error               | Agent Actions                                                                                            |
| ------------------- | -------------------------------------------------------------------------------------------------------- |
| Pending             | Explain exactly why scheduler cannot place the Pod                                                       |
| FailedScheduling    | Diagnose CPU, memory, taints, tolerations, node selectors, affinity, anti-affinity, topology constraints |
| Unschedulable Nodes | Recommend scaling cluster, modifying scheduling rules, or correcting node configuration                  |

---

## Storage

| Error              | Agent Actions                                                                             |
| ------------------ | ----------------------------------------------------------------------------------------- |
| PVC Pending        | Detect missing StorageClass, unavailable PersistentVolumes, insufficient storage capacity |
| MountVolume Errors | Verify PVC/PV binding, CSI driver health, mount permissions, node attachment              |
| Multi-Attach Error | Detect stale volume attachments and recommend detaching or rescheduling workloads         |

---

## Networking

| Error                 | Agent Actions                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------------- |
| DNS Failure           | Verify CoreDNS, DNS policy, Service DNS records, upstream DNS resolution                       |
| Service Unreachable   | Validate Service selector, Endpoints, EndpointSlices, targetPort, Pod readiness                |
| NetworkPolicy Blocked | Detect denied ingress/egress traffic between workloads                                         |
| Ingress Failure       | Validate Ingress resource, ingressClass, controller health, backend Service, TLS configuration |
| LoadBalancer Pending  | Verify cloud provider integration, load balancer controller, subnet configuration, quotas      |

---

## Node Health

| Error           | Agent Actions                                                           |
| --------------- | ----------------------------------------------------------------------- |
| Node NotReady   | Diagnose kubelet, container runtime, networking, node conditions        |
| DiskPressure    | Identify disk usage, clean images/logs, recommend storage expansion     |
| MemoryPressure  | Identify memory-consuming Pods and recommend scaling or tuning          |
| PIDPressure     | Detect process leaks causing PID exhaustion                             |
| Kubelet Stopped | Verify kubelet status, inspect logs, recommend restart or node recovery |

---

## Configuration

| Error                    | Agent Actions                                                                      |
| ------------------------ | ---------------------------------------------------------------------------------- |
| Invalid Manifest         | Explain validation errors and identify invalid fields                              |
| Failed Admission Webhook | Identify failing webhook, timeout, or rejected policy                              |
| RBAC Forbidden           | Recommend missing Roles, ClusterRoles, RoleBindings, or ServiceAccount permissions |

---

## Security

| Error                 | Agent Actions                                                  |
| --------------------- | -------------------------------------------------------------- |
| Secret Missing        | Locate missing or incorrectly referenced Secrets               |
| ServiceAccount Issues | Verify ServiceAccount existence, token mounting, RBAC bindings |

---

## Resource Management

| Error          | Agent Actions                                                                     |
| -------------- | --------------------------------------------------------------------------------- |
| OOMKilled      | Analyze memory usage, recommend appropriate requests/limits or application tuning |
| CPU Throttling | Detect excessive throttling and recommend CPU request/limit adjustments           |

---

## Autoscaling

| Error                          | Agent Actions                                                                                                  |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| HPA Not Scaling                | Verify Metrics Server or Prometheus metrics, HPA configuration, scaling targets                                |
| Cluster Autoscaler Not Scaling | Explain why nodes were not provisioned (resource constraints, cloud provider limits, autoscaler configuration) |
