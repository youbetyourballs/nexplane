packer {
  required_plugins {
    amazon = {
      version = ">= 1.3.0"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

variable "version" {
  type        = string
  description = "Release version tag, e.g. v1.2.3"
}

variable "ghcr_token" {
  type      = string
  sensitive = true
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

locals {
  ami_name = "nexplane-${var.version}"
}

source "amazon-ebs" "nexplane" {
  region        = var.aws_region
  instance_type = "t3.medium"

  # Latest Ubuntu 22.04 LTS x86_64
  source_ami_filter {
    filters = {
      name                = "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"
      root-device-type    = "ebs"
      virtualization-type = "hvm"
    }
    most_recent = true
    owners      = ["099720109477"] # Canonical
  }

  ssh_username = "ubuntu"

  ami_name        = local.ami_name
  ami_description = "Nexplane platform ${var.version} — zero-config, runs on port 80"

  # Start private; made public by publish-manifest job after smoke passes
  ami_groups = []

  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = 30
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Name      = local.ami_name
    ManagedBy = "nexplane-ci"
    Version   = var.version
  }
}

build {
  sources = ["source.amazon-ebs.nexplane"]

  # Create destination directories before file provisioners run
  provisioner "shell" {
    inline = ["mkdir -p /tmp/nexplane-src/frontend /tmp/nexplane-src/packer"]
  }

  # Copy frontend source so setup.sh can build the production image
  provisioner "file" {
    source      = "frontend/"
    destination = "/tmp/nexplane-src/frontend"
  }

  # Copy packer assets (compose file, systemd unit)
  provisioner "file" {
    source      = "packer/"
    destination = "/tmp/nexplane-src/packer"
  }

  provisioner "shell" {
    environment_vars = [
      "NEXPLANE_VERSION=${var.version}",
      "NEXPLANE_GHCR_TOKEN=${var.ghcr_token}",
      "DEBIAN_FRONTEND=noninteractive",
    ]
    execute_command = "sudo -E bash '{{ .Path }}'"
    script          = "packer/scripts/setup.sh"
  }

  post-processor "manifest" {
    output     = "packer/manifest.json"
    strip_path = true
  }
}
