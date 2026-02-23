#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

# Initialize database tables and sample data
python -c "from app import init_db; init_db()"
