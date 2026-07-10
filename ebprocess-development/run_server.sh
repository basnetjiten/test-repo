#!/bin/bash

# Run the FastAPI server
echo "Starting FastAPI Server for ebprocess-development..."
PYTHONPATH=src .venv/bin/uvicorn ebdev.api.main:app --reload --reload-dir src --host 0.0.0.0 --port 8001
