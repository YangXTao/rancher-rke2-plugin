data "vsphere_datacenter" "dc" {
  name = var.datacenter
}
data "vsphere_resource_pool" "pool" {
  name = var.resource_pool_name
  datacenter_id = data.vsphere_datacenter.dc.id
}
data "vsphere_datastore" "datastore" {
  name = var.datastore
  datacenter_id = data.vsphere_datacenter.dc.id
}
data "vsphere_network" "network" {
  name = var.network_name
  datacenter_id = data.vsphere_datacenter.dc.id
}
data "vsphere_virtual_machine" "template" {
  name = var.template_name
  datacenter_id = data.vsphere_datacenter.dc.id
}
