#!/usr/bin/env python3
"""
LightRAG Example 03: Upload File via REST API
This script demonstrates:
- Sending a file from local disk to LightRAG via multipart/form-data upload
- Extracting track IDs from the server response
- Polling status until the file is parsed, chunks embedded, and graph entities extracted
"""

import os
import sys
import time
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# Configuration
BASE_URL = os.getenv("LIGHTRAG_BASE_URL", "http://localhost:9621").rstrip('/')
API_KEY = os.getenv("LIGHTRAG_API_KEY")

if not API_KEY or API_KEY == "replace_with_your_api_key":
    print("Error: LIGHTRAG_API_KEY environment variable is not set correctly in your .env file.", file=sys.stderr)
    sys.exit(1)

HEADERS_AUTH = {
    "X-API-Key": API_KEY
}

def upload_file(file_path: str):
    """Upload a file using multipart form-data."""
    if not os.path.exists(file_path):
        print(f"Error: File '{file_path}' does not exist.", file=sys.stderr)
        sys.exit(1)
        
    url = f"{BASE_URL}/documents/upload"
    print(f"Uploading file: {file_path} ...")
    
    filename = os.path.basename(file_path)
    
    try:
        with open(file_path, 'rb') as f:
            files = {
                'file': (filename, f, 'application/octet-stream')
            }
            # Note: We do NOT set Content-Type header manually for multipart, 
            # requests library does it automatically with the boundary token.
            response = requests.post(url, headers=HEADERS_AUTH, files=files, timeout=60)
            
        if response.status_code != 200:
            print(f"Upload failed. HTTP {response.status_code}: {response.text}", file=sys.stderr)
            sys.exit(1)
            
        data = response.json()
        print("Upload successful!")
        print(f"Server response: {data}")
        
        # Parse track IDs from response
        track_ids = data.get("track_ids", [])
        if not track_ids and "data" in data and isinstance(data["data"], dict):
            track_ids = data["data"].get("track_ids", [])
        if not track_ids and "track_id" in data:
            track_ids = [data["track_id"]]
            
        if not track_ids:
            print("Warning: No track IDs returned in response. The document might be indexed synchronously.")
            return None
            
        return track_ids[0]
        
    except Exception as e:
        print(f"Network error during upload: {e}", file=sys.stderr)
        sys.exit(1)

def track_progress(track_id: str):
    """Track the status of the uploaded file until indexing completes."""
    url = f"{BASE_URL}/documents/track_status/{track_id}"
    print(f"\nMonitoring processing status for track: {track_id}")
    
    start_time = time.time()
    max_wait = 600  # 10 minutes
    
    while time.time() - start_time < max_wait:
        try:
            response = requests.get(url, headers=HEADERS_AUTH, timeout=10)
            if response.status_code == 404:
                print("Track ID not found yet. Retrying...")
                time.sleep(5)
                continue
                
            response.raise_for_status()
            res_data = response.json()
            
            status_data = res_data.get("data", res_data)
            status = "unknown"
            if isinstance(status_data, dict):
                status = status_data.get("status", "unknown")
            elif isinstance(status_data, str):
                status = status_data
                
            print(f"Pipeline status: {status.upper()}")
            
            if status in ("completed", "success"):
                print("🎉 File successfully processed, embedded, and merged into the graph database!")
                return True
            elif status in ("failed", "error"):
                print(f"❌ Processing failed. Detail: {res_data}", file=sys.stderr)
                return False
                
        except Exception as e:
            print(f"Warning: Error fetching status: {e}", file=sys.stderr)
            
        time.sleep(5)
        
    print(f"Timeout of {max_wait} seconds reached.", file=sys.stderr)
    return False

def main():
    # Allow passing custom file as argument, otherwise use sample_data/book.txt
    target_file = os.path.join(os.path.dirname(__file__), "sample_data", "book.txt")
    
    if len(sys.argv) > 1:
        target_file = sys.argv[1]
        
    track_id = upload_file(target_file)
    if track_id:
        track_progress(track_id)
    else:
        print("\nSkipping status tracking as no track_id was received.")

if __name__ == "__main__":
    main()
