#!/bin/bash
cd "$(dirname "$0")"
echo "Installing dependencies..."
pip install -r requirements.txt -q
echo ""
echo "Starting sdbot..."
echo ""
python sdbot.py shell
