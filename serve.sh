#!/usr/bin/env bash
# Start a local server to view the map
# Then open http://localhost:8080 in your browser
cd "$(dirname "$0")"
python3 -m http.server 8080
