#!/usr/bin/env python3
"""
LightRAG Example 04: OpenAI SDK Usage (Standalone SDK)
This script demonstrates:
- Instantiating the LightRAG Python SDK directly in code (not using the REST API)
- Initializing LightRAG with OpenAI's gpt-4o-mini and text-embedding-3-small
- Processing a local text file and querying it
"""

import os
import sys
import shutil
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# Ensure we have the OpenAI API key set in environment
if not os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") == "replace_with_your_openai_api_key":
    # Fallback to checking LIGHTRAG_API_KEY if it looks like an OpenAI key, 
    # but normally users should set OPENAI_API_KEY for SDK usage.
    print("Error: OPENAI_API_KEY environment variable is not set correctly in your environment.", file=sys.stderr)
    print("Please set it in examples/.env or export it in your shell.", file=sys.stderr)
    sys.exit(1)

try:
    from lightrag import LightRAG, QueryParam
    from lightrag.llm.openai import gpt_4o_mini_complete, openai_embed
except ImportError:
    print("Error: The 'lightrag-hku' package is not installed.", file=sys.stderr)
    print("Please install dependencies first: pip install 'lightrag-hku[api]'", file=sys.stderr)
    sys.exit(1)

# Working directory for SDK file-based storage
WORKING_DIR = os.path.join(os.path.dirname(__file__), "dickens_sdk_data")

def main():
    print(f"Initializing LightRAG direct SDK...")
    print(f"SDK Storage Directory: {WORKING_DIR}")
    
    # Clean up previous runs if requested
    if os.path.exists(WORKING_DIR):
        print("Existing SDK directory found. Clearing for a clean run...")
        shutil.rmtree(WORKING_DIR)
        
    os.makedirs(WORKING_DIR, exist_ok=True)
    
    # Instantiate the LightRAG engine
    rag = LightRAG(
        working_dir=WORKING_DIR,
        llm_model_func=gpt_4o_mini_complete,
        embedding_func=openai_embed
    )
    
    # Read our sample book excerpt
    sample_file = os.path.join(os.path.dirname(__file__), "sample_data", "book.txt")
    if not os.path.exists(sample_file):
        print(f"Error: Sample data not found at {sample_file}.", file=sys.stderr)
        print("Please run this script from the examples folder or make sure sample_data/book.txt is created.", file=sys.stderr)
        sys.exit(1)
        
    print(f"Reading text from {sample_file}...")
    with open(sample_file, "r", encoding="utf-8") as f:
        text_content = f.read()
        
    print("Ingesting text into LightRAG (this extracts entities & relationships)...")
    rag.insert(text_content)
    print("Ingestion completed successfully!")
    
    # Define query
    query = "Who was Marley and what was his relationship to Scrooge?"
    
    # Perform a query in hybrid mode
    print(f"\nRunning Query (Hybrid Mode): '{query}'")
    print("-" * 50)
    response = rag.query(query, param=QueryParam(mode="hybrid"))
    print(response)
    
    # Perform a query in naive mode
    print(f"\nRunning Query (Naive Mode): '{query}'")
    print("-" * 50)
    response = rag.query(query, param=QueryParam(mode="naive"))
    print(response)

if __name__ == "__main__":
    main()
