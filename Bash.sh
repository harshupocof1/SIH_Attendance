#!/usr/bin/env bash
# exit on error
set -o errexit

# Use sudo to get permissions to install system packages
sudo apt-get update
sudo apt-get install -y portaudio19-dev

# Install Python dependencies
pip install -r requirements.txt

