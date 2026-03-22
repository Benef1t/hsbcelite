#!/bin/bash
# HSBC Points Tracker - Setup Script
# Run this once to set up the environment

set -e

echo "=== HSBC Points Tracker Setup ==="

# 1. Create virtual environment
echo "Creating Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
echo "Installing dependencies..."
pip install -r requirements.txt

# 3. Install Playwright browsers
echo "Installing Playwright Chromium browser..."
playwright install chromium

# 4. Create .env from example if it doesn't exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo ">>> Created .env file. Please edit it with your credentials:"
    echo ">>>   nano .env"
    echo ""
fi

# 5. Create credentials directory
mkdir -p credentials screenshots

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. Edit .env with your HSBC credentials and Google Sheet ID"
echo "2. Place your Google service account JSON in credentials/service_account.json"
echo "   (See README.md for how to create a service account)"
echo "3. Test with: source .venv/bin/activate && python -m src.main --dry-run"
echo "4. For scheduling, see README.md for Mac LaunchAgent setup"
