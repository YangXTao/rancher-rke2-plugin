locals {
  chart_values = yamlencode(var.rke_config.chartValues)

  machine_global_config = yamlencode(merge(
    var.rke_config.machineGlobalConfig,
    try(var.registries.enabled, false) && try(var.registries.systemDefaultRegistry, "") != "" ? {
      "system-default-registry" = var.registries.systemDefaultRegistry
    } : {}
  ))
}
