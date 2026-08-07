rke2-cilium:
  hubble:
    enabled: ${hubble_enabled}
    relay:
      enabled: ${hubble_relay_enabled}
    ui:
      enabled: ${hubble_ui_enabled}
  kubeProxyReplacement: true
  k8sServiceHost: localhost
  k8sServicePort: "6443"

