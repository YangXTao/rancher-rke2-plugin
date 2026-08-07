resource "rancher2_cluster_v2" "cluster" {
  name                  = var.cluster_name
  kubernetes_version    = var.kubernetes_version
  enable_network_policy = false

  rke_config {
    chart_values          = local.chart_values
    machine_global_config = local.machine_global_config

    dynamic "machine_selector_config" {
      for_each = try(var.rke_config.machineSelectorConfig, [])
      content {
        config = yamlencode(machine_selector_config.value.config)
      }
    }

    etcd {
      disable_snapshots      = var.rke_config.etcd.disableSnapshots
      snapshot_retention     = var.rke_config.etcd.snapshotRetention
      snapshot_schedule_cron = var.rke_config.etcd.snapshotScheduleCron
    }

    dynamic "registries" {
      for_each = try(var.registries.enabled, false) ? [var.registries] : []
      content {
        dynamic "configs" {
          for_each = try(registries.value.configs, [])
          content {
            hostname                = configs.value.hostname
            auth_config_secret_name = try(configs.value.authConfigSecretName, "") != "" ? configs.value.authConfigSecretName : null
            tls_secret_name         = try(configs.value.tlsSecretName, "") != "" ? configs.value.tlsSecretName : null
            ca_bundle               = try(configs.value.caBundle, "") != "" ? configs.value.caBundle : null
            insecure                = try(configs.value.insecure, false)
          }
        }
        dynamic "mirrors" {
          for_each = try(registries.value.mirrors, [])
          content {
            hostname  = mirrors.value.hostname
            endpoints = try(mirrors.value.endpoints, [])
            rewrites  = try(mirrors.value.rewrites, {})
          }
        }
      }
    }
  }
}
