kubelet-arg:
  - kube-reserved=${kube_reserved}
  - system-reserved=${system_reserved}
  - container-log-max-files=5
  - container-log-max-size=100Mi
  - eviction-hard=memory.available<300Mi,nodefs.available<10%,imagefs.available<15%,nodefs.inodesFree<5%
  - eviction-soft=memory.available<500Mi,nodefs.available<15%,imagefs.available<20%,nodefs.inodesFree<10%
  - eviction-soft-grace-period=memory.available=1m30s,nodefs.available=1m30s,imagefs.available=1m30s,nodefs.inodesFree=1m30s
  - max-open-files=2000000
  - serialize-image-pulls=false
  - max-pods=${max_pods}

