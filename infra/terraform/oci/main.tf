resource "oci_core_route_table" "default" {
  compartment_id = var.compartment_id
  display_name   = "default route table for bank-health-vcn"
  freeform_tags = {
    VCN = "2026-10-27T06:14:26.986Z"
  }
  vcn_id = oci_core_vcn.bank_health.id
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.igw.id
    route_type        = "STATIC"
  }
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [defined_tags]
  }
}

resource "oci_core_service_gateway" "sgw" {
  compartment_id = var.compartment_id
  display_name   = "Service gateway-bank-health-vcn"
  freeform_tags = {
    VCN = "2026-10-27T06:14:26.986Z"
  }
  vcn_id = oci_core_vcn.bank_health.id
  services {
    service_id = "ocid1.service.oc1.us-chicago-1.aaaaaaaakalxenk3rcszv74eamyc6dkjq6ngikfsdpctsll4nxw2lnfqbpbq"
  }
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [defined_tags]
  }
}

resource "oci_budget_budget" "monthly_5usd" {
  amount                                = 5
  budget_processing_period_start_offset = 1
  compartment_id                        = var.compartment_id
  description                           = "Alert if anything beyond Always Free costs money"
  display_name                          = "bank-health-5usd"
  freeform_tags                         = {}
  processing_period_type                = "MONTH"
  reset_period                          = "MONTHLY"
  system_tags                           = {}
  target_type                           = "COMPARTMENT"
  targets                               = [var.compartment_id]
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [defined_tags]
  }
}

resource "oci_core_nat_gateway" "nat" {
  block_traffic  = false
  compartment_id = var.compartment_id
  display_name   = "NAT gateway-bank-health-vcn"
  freeform_tags = {
    VCN = "2026-10-27T06:14:26.986Z"
  }
  public_ip_id = var.nat_public_ip_id
  vcn_id       = oci_core_vcn.bank_health.id
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [defined_tags]
  }
}

resource "oci_core_route_table" "private" {
  compartment_id = var.compartment_id
  display_name   = "route table for private private subnet-bank-health-vcn"
  freeform_tags = {
    VCN = "2026-10-27T06:14:26.986Z"
  }
  vcn_id = oci_core_vcn.bank_health.id
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_nat_gateway.nat.id
    route_type        = "STATIC"
  }
  route_rules {
    destination       = "all-ord-services-in-oracle-services-network"
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.sgw.id
    route_type        = "STATIC"
  }
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [defined_tags]
  }
}

resource "oci_core_subnet" "private" {
  cidr_block      = "10.0.1.0/24"
  compartment_id  = var.compartment_id
  dhcp_options_id = oci_core_dhcp_options.default.id
  display_name    = "private subnet-bank-health-vcn"
  dns_label       = "sub10270614301"
  freeform_tags = {
    VCN = "2026-10-27T06:14:26.986Z"
  }
  ipv4cidr_blocks            = ["10.0.1.0/24"]
  ipv6cidr_blocks            = []
  prohibit_internet_ingress  = true
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.private.id]
  vcn_id                     = oci_core_vcn.bank_health.id
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [defined_tags]
  }
}

resource "oci_budget_alert_rule" "monthly_5usd_actual" {
  budget_id      = oci_budget_budget.monthly_5usd.id
  display_name   = "alertrule20261027183205"
  freeform_tags  = {}
  message        = "Bank Health OCI spend alert: check Cost Analysis."
  recipients     = var.budget_alert_recipients
  threshold      = 20
  threshold_type = "PERCENTAGE"
  type           = "ACTUAL"
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [defined_tags]
  }
}

resource "oci_core_dhcp_options" "default" {
  compartment_id   = var.compartment_id
  display_name     = "Default DHCP Options for bank-health-vcn"
  domain_name_type = "CUSTOM_DOMAIN"
  freeform_tags = {
    VCN = "2026-10-27T06:14:26.986Z"
  }
  vcn_id = oci_core_vcn.bank_health.id
  options {
    custom_dns_servers  = []
    search_domain_names = ["bankhealthvcn.oraclevcn.com"]
    type                = "SearchDomain"
  }
  options {
    custom_dns_servers  = []
    search_domain_names = []
    server_type         = "VcnLocalPlusInternet"
    type                = "DomainNameServer"
  }
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [defined_tags]
  }
}

resource "oci_core_vcn" "bank_health" {
  cidr_block     = "10.0.0.0/16"
  cidr_blocks    = ["10.0.0.0/16"]
  compartment_id = var.compartment_id
  display_name   = "bank-health-vcn"
  dns_label      = "bankhealthvcn"
  freeform_tags = {
    VCN = "2026-10-27T06:14:26.986Z"
  }
  ipv6private_cidr_blocks = []
  is_ipv6enabled          = false
  security_attributes     = {}
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [defined_tags]
  }
}

resource "oci_core_instance" "bank_health_prod" {
  async                      = null
  availability_domain        = "iuJs:US-CHICAGO-1-AD-1"
  cluster_placement_group_id = null
  compartment_id             = var.compartment_id
  display_name               = "bank-health-prod"
  extended_metadata          = {}
  fault_domain               = "FAULT-DOMAIN-3"
  freeform_tags              = {}
  metadata = {
    ssh_authorized_keys = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGzw8fB8HzKNduy2BYJhB7aikwFHSW0E5PLE2NPbi9U8 bank-health-vm"
  }
  preserve_boot_volume                    = null
  preserve_data_volumes_created_at_launch = null
  security_attributes                     = {}
  shape                                   = "VM.Standard.A1.Flex"
  state                                   = "RUNNING"
  update_operation_constraint             = null
  agent_config {
    are_all_plugins_disabled = false
    is_management_disabled   = false
    is_monitoring_disabled   = false
    plugins_config {
      desired_state = "DISABLED"
      name          = "Vulnerability Scanning"
    }
    plugins_config {
      desired_state = "DISABLED"
      name          = "OS Management Hub Agent"
    }
    plugins_config {
      desired_state = "DISABLED"
      name          = "Management Agent"
    }
    plugins_config {
      desired_state = "ENABLED"
      name          = "Custom Logs Monitoring"
    }
    plugins_config {
      desired_state = "DISABLED"
      name          = "Compute RDMA GPU Monitoring"
    }
    plugins_config {
      desired_state = "ENABLED"
      name          = "Compute Instance Run Command"
    }
    plugins_config {
      desired_state = "ENABLED"
      name          = "Compute Instance Monitoring"
    }
    plugins_config {
      desired_state = "DISABLED"
      name          = "Compute HPC RDMA Auto-Configuration"
    }
    plugins_config {
      desired_state = "DISABLED"
      name          = "Compute HPC RDMA Authentication"
    }
    plugins_config {
      desired_state = "ENABLED"
      name          = "Cloud Guard Workload Protection"
    }
    plugins_config {
      desired_state = "DISABLED"
      name          = "Block Volume Management"
    }
    plugins_config {
      desired_state = "DISABLED"
      name          = "Bastion"
    }
  }
  availability_config {
    is_live_migration_preferred = false
    recovery_action             = "RESTORE_INSTANCE"
  }
  create_vnic_details {
    assign_ipv6ip             = false
    assign_private_dns_record = false
    assign_public_ip          = "true"
    display_name              = "bank-health-prod-vnic"
    freeform_tags             = {}
    hostname_label            = "bank-health-prod-vnic"
    nsg_ids                   = []
    private_ip                = "10.0.0.156"
    security_attributes       = {}
    skip_source_dest_check    = false
    subnet_id                 = oci_core_subnet.public.id
  }
  instance_options {
    are_legacy_imds_endpoints_disabled = true
  }
  launch_options {
    boot_volume_type                    = "PARAVIRTUALIZED"
    firmware                            = "UEFI_64"
    is_consistent_volume_naming_enabled = true
    is_encryption_in_transit_enabled    = false
    is_pv_encryption_in_transit_enabled = true
    network_type                        = "PARAVIRTUALIZED"
    remote_data_volume_type             = "PARAVIRTUALIZED"
  }
  shape_config {
    local_volume_size_in_gbs = 0
    memory_in_gbs            = 24
    nvmes                    = 0
    ocpus                    = 4
    vcpus                    = 4
  }
  source_details {
    boot_volume_size_in_gbs         = "100"
    boot_volume_vpus_per_gb         = "10"
    is_preserve_boot_volume_enabled = false
    kms_key_id                      = null
    source_id                       = var.vm_image_id
    source_type                     = "image"
  }
  lifecycle {
    prevent_destroy = true
    # Never let Terraform touch the production VM (guard blocks it too).
    ignore_changes = all
  }
}

resource "oci_core_internet_gateway" "igw" {
  compartment_id = var.compartment_id
  display_name   = "Internet gateway-bank-health-vcn"
  enabled        = true
  freeform_tags = {
    VCN = "2026-10-27T06:14:26.986Z"
  }
  vcn_id = oci_core_vcn.bank_health.id
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [defined_tags]
  }
}

resource "oci_core_subnet" "public" {
  cidr_block      = "10.0.0.0/24"
  compartment_id  = var.compartment_id
  dhcp_options_id = oci_core_dhcp_options.default.id
  display_name    = "public subnet-bank-health-vcn"
  dns_label       = "sub10270614300"
  freeform_tags = {
    VCN = "2026-10-27T06:14:26.986Z"
  }
  ipv4cidr_blocks            = ["10.0.0.0/24"]
  ipv6cidr_blocks            = []
  prohibit_internet_ingress  = false
  prohibit_public_ip_on_vnic = false
  route_table_id             = oci_core_route_table.default.id
  security_list_ids          = [oci_core_security_list.default.id]
  vcn_id                     = oci_core_vcn.bank_health.id
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [defined_tags]
  }
}

resource "oci_core_security_list" "default" {
  compartment_id = var.compartment_id
  display_name   = "Default Security List for bank-health-vcn"
  freeform_tags = {
    VCN = "2026-10-27T06:14:26.986Z"
  }
  vcn_id = oci_core_vcn.bank_health.id
  egress_security_rules {
    destination      = "0.0.0.0/0"
    destination_type = "CIDR_BLOCK"
    protocol         = "all"
    stateless        = false
  }
  ingress_security_rules {
    protocol    = "1"
    source      = "10.0.0.0/16"
    source_type = "CIDR_BLOCK"
    stateless   = false
    icmp_options {
      code = -1
      type = 3
    }
  }
  ingress_security_rules {
    protocol    = "1"
    source      = "0.0.0.0/0"
    source_type = "CIDR_BLOCK"
    stateless   = false
    icmp_options {
      code = 4
      type = 3
    }
  }
  ingress_security_rules {
    protocol    = "6"
    source      = "47.186.196.205/32"
    source_type = "CIDR_BLOCK"
    stateless   = false
    tcp_options {
      max = 22
      min = 22
    }
  }
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [defined_tags]
  }
}

resource "oci_core_security_list" "private" {
  compartment_id = var.compartment_id
  display_name   = "security list for private subnet-bank-health-vcn"
  freeform_tags = {
    VCN = "2026-10-27T06:14:26.986Z"
  }
  vcn_id = oci_core_vcn.bank_health.id
  egress_security_rules {
    destination      = "0.0.0.0/0"
    destination_type = "CIDR_BLOCK"
    protocol         = "all"
    stateless        = false
  }
  ingress_security_rules {
    protocol    = "1"
    source      = "10.0.0.0/16"
    source_type = "CIDR_BLOCK"
    stateless   = false
    icmp_options {
      code = -1
      type = 3
    }
  }
  ingress_security_rules {
    protocol    = "1"
    source      = "0.0.0.0/0"
    source_type = "CIDR_BLOCK"
    stateless   = false
    icmp_options {
      code = 4
      type = 3
    }
  }
  ingress_security_rules {
    protocol    = "6"
    source      = "10.0.0.0/16"
    source_type = "CIDR_BLOCK"
    stateless   = false
    tcp_options {
      max = 22
      min = 22
    }
  }
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [defined_tags]
  }
}
