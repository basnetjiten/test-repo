# -*- coding: utf-8 -*-
"""Focused JSON contract regression checks for OpenCode responses."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure src directory is importable for direct execution.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ebdev.services.opencode import extract_json_block
from ebdev.services.prompts import build_orchestrator_prompt


def main() -> None:
    raw_payload = {
        "task_id": "T-123",
        "ticket_id": "T-123",
        "status": "success",
        "summary": "ok",
    }

    raw_text = json.dumps(raw_payload)
    assert extract_json_block(raw_text, "T-123") == raw_payload

    fenced_text = f"```json\n{json.dumps(raw_payload)}\n```"
    assert extract_json_block(fenced_text, "T-123") == raw_payload

    prompt = build_orchestrator_prompt(
        platforms=["api", "flutter"],
        ticket_id="T-123",
        ticket_title="Sample",
        ticket_desc="Sample description",
        ticket_ac="Sample AC",
    )
    assert "exact JSON object" in prompt
    assert "markdown fences" in prompt

    roundtrip = json.loads(json.dumps(raw_payload))
    assert roundtrip == raw_payload

    print("JSON contract checks passed.")


if __name__ == "__main__":
    main()
