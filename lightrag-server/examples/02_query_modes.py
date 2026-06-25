#!/usr/bin/env python3
"""
LightRAG Example 02: Query Modes
This script demonstrates querying the LightRAG server using its five search modes:
1. naive: Standard vector-based RAG query.
2. local: Focuses on local entity-relationship neighborhoods.
3. global: Focuses on global community-based themes.
4. hybrid: Combines local and global search for balanced retrieval.
5. mix: Integrates local and global graph data dynamically.
"""

import os
import sys
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

HEADERS = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

def query_lightrag(query_text: str, mode: str):
    """Send a query to the LightRAG server with a specific mode."""
    url = f"{BASE_URL}/query"
    payload = {
        "query": query_text,
        "mode": mode,
        "include_references": True  # Request reference sources/entities
    }
    
    print(f"\n{'='*60}")
    print(f"QUERY MODE: {mode.upper()}")
    print(f"Query: '{query_text}'")
    print(f"{'='*60}")
    
    try:
        response = requests.post(url, headers=HEADERS, json=payload, timeout=60)
        
        if response.status_code == 401:
            print("Auth Error: Unauthorized. Please check your API key.", file=sys.stderr)
            return
        elif response.status_code != 200:
            print(f"Error: Server returned status code {response.status_code}", file=sys.stderr)
            print(response.text, file=sys.stderr)
            return
            
        data = response.json()
        
        # Get query answer
        answer = data.get("response", "No response field in output.")
        print(answer)
        
    except Exception as e:
        print(f"Connection error occurred: {e}", file=sys.stderr)

def main():
    # Prompt the user for custom queries if they run it interactively, otherwise use defaults
    default_query = "What are the core capabilities and search modes of LightRAG?"
    
    query = default_query
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        
    print(f"Starting LightRAG query runner...")
    print(f"Target Server: {BASE_URL}")
    
    # Run the query in each of the 5 modes
    modes = ["naive", "local", "global", "hybrid", "mix"]
    
    for mode in modes:
        query_lightrag(query, mode)
        print("\nPress Enter to run the next mode (or Ctrl+C to exit)...")
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            sys.exit(0)

if __name__ == "__main__":
    main()
