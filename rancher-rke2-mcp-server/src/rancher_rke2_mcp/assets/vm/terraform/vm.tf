resource "vsphere_virtual_machine" "vm" {
  for_each = var.nodes
  name = each.key
  folder = var.vm_folder != "" ? var.vm_folder : null
  resource_pool_id = data.vsphere_resource_pool.pool.id
  datastore_id = data.vsphere_datastore.datastore.id
  num_cpus = each.value.cpu
  memory = each.value.memory
  guest_id = data.vsphere_virtual_machine.template.guest_id
  scsi_type = data.vsphere_virtual_machine.template.scsi_type
  firmware = data.vsphere_virtual_machine.template.firmware
  enable_disk_uuid = true
  wait_for_guest_net_timeout = 10
  wait_for_guest_ip_timeout = 10
  network_interface {
    network_id = data.vsphere_network.network.id
    adapter_type = data.vsphere_virtual_machine.template.network_interface_types[0]
  }
  disk {
    label = data.vsphere_virtual_machine.template.disks[0].label
    size = max(each.value.disk_gb, data.vsphere_virtual_machine.template.disks[0].size)
    unit_number = 0
    thin_provisioned = try(data.vsphere_virtual_machine.template.disks[0].thin_provisioned, true)
    eagerly_scrub = try(data.vsphere_virtual_machine.template.disks[0].eagerly_scrub, false)
  }
  clone {
    template_uuid = data.vsphere_virtual_machine.template.id
    customize {
      linux_options {
        host_name = each.value.host_name
        domain = var.vm_domain
      }
      network_interface {
        ipv4_address = each.value.ip
        ipv4_netmask = each.value.netmask
      }
      ipv4_gateway = each.value.gateway
      dns_server_list = var.dns_servers
    }
  }
}
