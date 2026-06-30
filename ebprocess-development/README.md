# ebprocess-development: Multi-platform Agentic Workspace

This repository houses the autonomous multi-agent developer system for API (backend), React/Next.js (Web), and Flutter (Mobile) engineering tasks.

## Quick Start

1. **Configure Environment Variables**:
   Copy `.env.example` to `.env` and fill out keys:
   ```bash
   cp .env.example .env
   ```

2. **Install Workspace Dependencies**:
   ```bash
   pip install -e .
   ```

3. **Verify LangGraph Orchestrator**:
   Validate that the compilation graph works cleanly:
   ```bash
   python -c "from ebdev.core.graph import graph; print('Graph compiled successfully')"
   ```
