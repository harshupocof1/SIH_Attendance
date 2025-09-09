#!/usr/bin/env bash
# exit on error
set -o errexit

# Install system packages without sudo
apt-get update
apt-get install -y portaudio19-dev

# Install Python dependencies
pip install -r requirements.txt

