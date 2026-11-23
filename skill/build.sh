#!/bin/sh
# Copy the router's source of truth into the skill folder and zip it for sharing.
# Edit route.py, questions.py and departments.json in the project root, never the copies.
set -e
cd "$(dirname "$0")"
ROOT=..
SKILL=ticket-router

cp "$ROOT/route.py" "$ROOT/questions.py" "$ROOT/departments.json" "$SKILL/scripts/"
mkdir -p "$SKILL/examples"
cp "$ROOT/probe_tickets.json" "$SKILL/examples/tickets.json"

rm -f ticket-router.zip
zip -qr ticket-router.zip "$SKILL" -x "*/__pycache__/*" "*/runs/*" "*/.env" "*.DS_Store"
echo "Built skill/ticket-router.zip"
