variable "vsphere_server" {
  type = string
}
variable "vsphere_user" {
  type = string
  sensitive = true
}
variable "vsphere_password" {
  type = string
  sensitive = true
}
variable "vsphere_insecure" {
  type = bool
  default = true
}
variable "datacenter" {
  type = string
}
variable "resource_pool_name" {
  type = string
}
variable "datastore" {
  type = string
}
variable "network_name" {
  type = string
}
variable "template_name" {
  type = string
}
variable "vm_folder" {
  type = string
  default = ""
}
variable "vm_domain" {
  type = string
}
variable "dns_servers" {
  type = list(string)
  default = []
}
variable "nodes" {
  type = map(object({
    host_name = string
    ip = string
    netmask = number
    gateway = string
    cpu = number
    memory = number
    disk_gb = number
  }))
}
