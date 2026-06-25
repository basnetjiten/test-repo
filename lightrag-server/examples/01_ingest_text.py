#!/usr/bin/env python3
"""
LightRAG Example 01: Ingest Text via REST API
This script demonstrates:
- Authenticating with the LightRAG server using X-API-Key
- Ingesting a raw text document directly via `/documents/text`
- Polling the task tracking endpoint `/documents/track_status/{track_id}` until indexing is complete
"""

import os
import sys
import time
import requests
from dotenv import load_dotenv

# Load environment variables from examples/.env or parent .env
load_dotenv()
# Also look in the parent directory if run from within the examples folder
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# Configuration
BASE_URL = os.getenv("LIGHTRAG_BASE_URL", "http://localhost:9621").rstrip('/')
API_KEY = os.getenv("LIGHTRAG_API_KEY")

if not API_KEY or API_KEY == "replace_with_your_api_key":
    print("Error: LIGHTRAG_API_KEY environment variable is not set correctly in your .env file.", file=sys.stderr)
    print("Please copy .env.example to .env and fill in your values.", file=sys.stderr)
    sys.exit(1)

HEADERS = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

def check_health():
    """Verify that the LightRAG server is running and reachable."""
    print(f"Connecting to LightRAG server at {BASE_URL}...")
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=10)
        if response.status_code == 200:
            print("Connected successfully! Server is healthy.")
            return True
        else:
            print(f"Server health check returned status code: {response.status_code}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"Error connecting to server: {e}", file=sys.stderr)
        return False

def ingest_text(text: str, description: str = "Demo document"):
    """Ingest a text block into the LightRAG server."""
    url = f"{BASE_URL}/documents/text"
    payload = {
        "text": text,
        "description": description
    }
    
    print(f"\nSubmitting document for ingestion: '{description}'...")
    response = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    
    if response.status_code != 200:
        print(f"Failed to ingest document. HTTP {response.status_code}: {response.text}", file=sys.stderr)
        sys.exit(1)
        
    data = response.json()
    print("Ingestion request accepted!")
    print(f"Server response: {data}")
    
    # Check if a track_id was returned (FastAPI schema might vary, let's search for keys)
    # Usually it's in a key like "track_id" or "data"
    track_id = data.get("track_id")
    if not track_id and "data" in data and isinstance(data["data"], dict):
        track_id = data["data"].get("track_id")
        
    if not track_id:
        # Some LightRAG versions might index synchronously or return list
        track_id = data.get("track_ids", [None])[0]
        
    if not track_id:
        print("Warning: No track_id found in response. Standard response structure is missing. The document may have been indexed synchronously.")
        return None
        
    return track_id

def poll_status(track_id: str):
    """Poll the status of the document until indexing is finished or fails."""
    url = f"{BASE_URL}/documents/track_status/{track_id}"
    print(f"\nTracking indexing progress (Track ID: {track_id})...")
    
    start_time = time.time()
    max_wait = 300  # 5 minutes
    
    while time.time() - start_time < max_wait:
        try:
            response = requests.get(url, headers=HEADERS, timeout=10)
            if response.status_code == 404:
                # Some versions might delete completed tracks or have different formats
                print("Status check returned 404. Let's wait a bit or it might have completed.")
                time.sleep(5)
                continue
                
            response.raise_for_status()
            res_data = response.json()
            
            # The status response structure can be {"data": {"status": "..."}} or {"status": "..."}
            status_data = res_data.get("data", res_data)
            status = "unknown"
            if isinstance(status_data, dict):
                status = status_data.get("status", "unknown")
            elif isinstance(status_data, str):
                status = status_data
                
            print(f"Current status: {status.upper()}")
            
            if status in ("completed", "success"):
                print("🎉 Ingestion successfully completed! The document is now indexed.")
                return True
            elif status in ("failed", "error"):
                print(f"❌ Indexing failed. Response: {res_data}", file=sys.stderr)
                return False
                
        except Exception as e:
            print(f"Warning: Error polling status: {e}. Retrying...", file=sys.stderr)
            
        time.sleep(5)
        
    print(f"Timeout of {max_wait} seconds reached while waiting for document indexing.", file=sys.stderr)
    return False

def main():
    if not check_health():
        sys.exit(1)
        
    demo_text = (
        "LightRAG is an innovative Retrieval-Augmented Generation framework that integrates "
        "graph-based structures into the LLM context. It excels at local, global, and hybrid search queries, "
        "allowing users to query dual-level information (low-level specific entities as well as high-level "
        "themes). LightRAG is designed to be lightweight, support fast insertion, and work seamlessly with "
        "OpenAI, Ollama, and various local LLM providers. It stores knowledge graphs and vector databases "
        "to assist the generator model in producing factual, context-aware responses."
    )
    
    track_id = ingest_text(demo_text, description="LightRAG Overview Intro")
    if track_id:
        poll_status(track_id)
    else:
        print("\nSkipping polling as no track_id was returned. Try running query_modes.py to check if it's searchable.")

if __name__ == "__main__":
    main()
