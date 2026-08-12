output "cluster_id" {
  value = rancher2_cluster_v2.cluster.id
}
output "registration_command" {
  # The insecure command is required while Rancher uses the approved private CA.
  value     = rancher2_cluster_v2.cluster.cluster_registration_token[0].insecure_node_command
  sensitive = true
}
output "controlplane_registration_command" {
  value     = "${rancher2_cluster_v2.cluster.cluster_registration_token[0].insecure_node_command} --etcd --controlplane"
  sensitive = true
}
output "worker_registration_command" {
  value     = "${rancher2_cluster_v2.cluster.cluster_registration_token[0].insecure_node_command} --worker"
  sensitive = true
}
