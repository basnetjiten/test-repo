# -*- coding: utf-8 -*-
"""
fs.py
=====
Centralized filesystem service for non-blocking and atomic file I/O operations.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ebdev.core.logger import get_logger

logger = get_logger(__name__)


class FileSystemError(Exception):
    """Raised when any filesystem read, write, or folder operation fails."""


class AsyncFileSystemService:
    """
    Centralized filesystem service.
    All disk operations are offloaded to standard threadpools to keep the
    asynchronous event loop non-blocking.
    """

    @staticmethod
    async def ensure_directory(path: Path) -> None:
        """Create directory and parents if not already existing."""
        try:
            await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
        except OSError as exc:
            raise FileSystemError(f"Failed to create directory {path}: {exc}") from exc

    @staticmethod
    async def read_text(path: Path) -> str:
        """Read a text file in a non-blocking thread."""
        try:
            return await asyncio.to_thread(path.read_text, encoding="utf-8")
        except OSError as exc:
            raise FileSystemError(f"Failed to read file {path}: {exc}") from exc

    @staticmethod
    async def write_text(path: Path, content: str) -> None:
        """Write content to a file in a-non-blocking thread."""
        try:
            await AsyncFileSystemService.ensure_directory(path.parent)
            await asyncio.to_thread(path.write_text, content, encoding="utf-8")
        except OSError as exc:
            raise FileSystemError(f"Failed to write file {path}: {exc}") from exc

    @staticmethod
    async def write_text_atomic(path: Path, content: str) -> None:
        """
        Write content to a file atomically via tempfile write-and-rename.
        Guarantees that other processes never observe a partially written file.
        """
        try:
            await AsyncFileSystemService.ensure_directory(path.parent)

            def _write_atomic():
                fd, tmp_path_str = tempfile.mkstemp(dir=path.parent, prefix=".fs_", suffix=".tmp")
                tmp_path = Path(tmp_path_str)
                try:
                    with open(fd, "w", encoding="utf-8") as f:
                        f.write(content)
                    os.replace(tmp_path, path)
                except Exception as exc:
                    if tmp_path.exists():
                        try:
                            tmp_path.unlink()
                        except OSError:
                            pass
                    raise exc

            await asyncio.to_thread(_write_atomic)
        except Exception as exc:
            raise FileSystemError(f"Failed to write file atomically {path}: {exc}") from exc

    @staticmethod
    async def read_json(path: Path) -> Any:
        """Read and parse JSON content from file."""
        content = await AsyncFileSystemService.read_text(path)
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise FileSystemError(f"Failed to parse JSON from {path}: {exc}") from exc

    @staticmethod
    async def write_json_atomic(path: Path, data: Any, indent: int = 2) -> None:
        """Serialize data to JSON and write to file atomically."""
        try:
            content = json.dumps(data, indent=indent, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise FileSystemError(f"Failed to serialize JSON data: {exc}") from exc
        await AsyncFileSystemService.write_text_atomic(path, content)

    @staticmethod
    async def exists(path: Path) -> bool:
        """Check path existence in a non-blocking thread."""
        return await asyncio.to_thread(path.exists)

    @staticmethod
    async def delete_file(path: Path) -> bool:
        """Safely delete a file in a non-blocking thread. Returns True if deleted."""
        try:
            def _delete():
                if path.exists():
                    path.unlink()
                    return True
                return False
            return await asyncio.to_thread(_delete)
        except OSError as exc:
            raise FileSystemError(f"Failed to delete file {path}: {exc}") from exc
