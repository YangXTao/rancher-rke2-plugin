cni: cilium
disable-kube-proxy: true
cluster-cidr: ${cluster_cidr}
service-cidr: ${service_cidr}
cluster-dns: ${cluster_dns}
%{ if system_default_registry != "" ~}
system-default-registry: ${system_default_registry}
%{ endif ~}
etcd-expose-metrics: true
etcd-arg:
  - --auto-compaction-mode=periodic
  - --auto-compaction-retention=1h0m0s
  - --quota-backend-bytes=6442450944
kube-apiserver-arg:
  - --event-ttl=2h0m0s
  - --max-requests-inflight=800
  - --max-mutating-requests-inflight=400
kube-controller-manager-arg:
  - --node-monitor-grace-period=20s
  - --node-startup-grace-period=30s
