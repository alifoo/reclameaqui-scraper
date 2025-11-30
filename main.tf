# 1. PROVIDER
# This tells Terraform we are using AWS.
provider "aws" {
  region = "us-east-1"
}

# 2. DATA SOURCE (The AMI)
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-*-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# 3. RESOURCE (The Security Group / Firewall)
resource "aws_security_group" "scraper_sg" {
  name        = "scraper_security_group"
  description = "Allow SSH inbound traffic"

  ingress {
    description = "SSH from anywhere"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # todo: put specific ip
  }

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# 4. RESOURCE (The SSH Key)
resource "aws_key_pair" "deployer" {
  key_name   = "scraper-key"
  public_key = file("scraper-key.pub")
}

# 5. RESOURCE (The Server / EC2)
resource "aws_instance" "scraper_bot" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = "t3.micro"
  key_name      = aws_key_pair.deployer.key_name

  vpc_security_group_ids = [aws_security_group.scraper_sg.id]

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  # script that runs once to set up swap memory and install libs
  user_data = <<-EOF
              #!/bin/bash
              # Update system
              apt-get update -y
              apt-get upgrade -y
              
              # create 4gb swap memory
              fallocate -l 4G /swapfile
              chmod 600 /swapfile
              mkswap /swapfile
              swapon /swapfile
              echo '/swapfile none swap sw 0 0' | tee -a /etc/fstab
              
              apt-get install -y python3-pip python3-venv
              mkdir -p /home/ubuntu/scraper
              cd /home/ubuntu/scraper

              cat <<EOT >> requirements.txt
              ${file("requirements.txt")}
              EOT

              pip3 install -r requirements.txt --break-system-packages
              playwright install chromium
              playwright install-deps

              chown ubuntu:ubuntu /home/ubuntu/scraper
              EOF

  tags = {
    Name = "ReclameAqui-Scraper"
  }
}

# 7. OUTPUT
output "server_ip" {
  value = aws_instance.scraper_bot.public_ip
}
