output "vm_ids" { value = { for name, vm in vsphere_virtual_machine.vm : name => vm.id } }
output "vm_ips" { value = { for name, vm in vsphere_virtual_machine.vm : name => vm.default_ip_address } }
