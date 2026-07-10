# -*- coding: utf-8 -*-
import pytest
from pathlib import Path
from ebdev.services.fs import AsyncFileSystemService, FileSystemError

@pytest.mark.asyncio
async def test_ensure_directory(tmp_path: Path):
    target_dir = tmp_path / "subdir" / "nested"
    assert not target_dir.exists()
    await AsyncFileSystemService.ensure_directory(target_dir)
    assert target_dir.exists()
    assert target_dir.is_dir()

@pytest.mark.asyncio
async def test_read_write_text(tmp_path: Path):
    target_file = tmp_path / "test_file.txt"
    content = "Hello, filesystem service!"
    
    # Write
    await AsyncFileSystemService.write_text(target_file, content)
    assert target_file.exists()
    
    # Read
    read_back = await AsyncFileSystemService.read_text(target_file)
    assert read_back == content

@pytest.mark.asyncio
async def test_write_text_atomic(tmp_path: Path):
    target_file = tmp_path / "test_file_atomic.txt"
    content = "Atomic file content"
    
    await AsyncFileSystemService.write_text_atomic(target_file, content)
    assert target_file.exists()
    
    read_back = await AsyncFileSystemService.read_text(target_file)
    assert read_back == content

@pytest.mark.asyncio
async def test_read_write_json_atomic(tmp_path: Path):
    target_file = tmp_path / "test_data.json"
    data = {"key": "value", "number": 42, "nested": {"list": [1, 2, 3]}}
    
    # Write JSON
    await AsyncFileSystemService.write_json_atomic(target_file, data)
    assert target_file.exists()
    
    # Read JSON
    read_back = await AsyncFileSystemService.read_json(target_file)
    assert read_back == data

@pytest.mark.asyncio
async def test_exists_and_delete(tmp_path: Path):
    target_file = tmp_path / "test_delete.txt"
    assert not await AsyncFileSystemService.exists(target_file)
    
    await AsyncFileSystemService.write_text(target_file, "temporary content")
    assert await AsyncFileSystemService.exists(target_file)
    
    deleted = await AsyncFileSystemService.delete_file(target_file)
    assert deleted is True
    assert not await AsyncFileSystemService.exists(target_file)
    
    # Delete non-existent file
    deleted_again = await AsyncFileSystemService.delete_file(target_file)
    assert deleted_again is False

@pytest.mark.asyncio
async def test_read_non_existent_file_raises_error(tmp_path: Path):
    non_existent = tmp_path / "ghost.txt"
    with pytest.raises(FileSystemError):
        await AsyncFileSystemService.read_text(non_existent)
