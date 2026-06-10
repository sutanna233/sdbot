#!/bin/bash
cd "$(dirname "$0")"
echo "Installing dependencies..."
pip install -r requirements.txt -q
echo ""
echo "Starting SD Artist Combo Tester..."
echo ""
python generate_artists.py