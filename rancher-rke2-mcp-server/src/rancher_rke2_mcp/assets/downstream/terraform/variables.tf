variable "rancher_api_url" {
  type = string
}
variable "rancher_token_key" {
  type      = string
  sensitive = true
}
variable "rancher_insecure" {
  type    = bool
  default = false
}
variable "cluster_name" {
  type = string
}
variable "kubernetes_version" {
  type = string
}
variable "rke_config" {
  description = "Selected user-overridable Rancher rkeConfig settings"
  type        = any
}
variable "registries" {
  description = "User-overridable Rancher2 registries configuration"
  type        = any
  default = {
    enabled                 = false
    systemDefaultRegistry   = ""
    configs                 = []
    mirrors                 = []
  }
}
