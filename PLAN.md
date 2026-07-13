describe_resource / get_resource / list_resources all hardcode -n namespace — but you added cluster-scoped kinds.

Node, PersistentVolume, StorageClass, ClusterRole, ClusterRoleBinding are not namespaced. Running kubectl get node my-node -n default -o json doesn't error in kubectl (it silently ignores -n), but kubectl describe node my-node -n default may behave inconsistently, and more importantly the agent will be confused about why it has to pass a namespace for something that doesn't have one. This isn't hypothetical — you already added NODE, PERSISTENTVOLUME, and STORAGECLASS to the enum, so this bug is live right now.

Fix: maintain a \_CLUSTER_SCOPED_KINDS set and branch the cmd construction to omit -n namespace for those kinds.
