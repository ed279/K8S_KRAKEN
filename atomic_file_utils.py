"""
Atomic File Utilities — Race Condition Prevention
All JSON writes use temp + rename pattern for safety.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict
import logging

logger = logging.getLogger(__name__)


class AtomicFileWriter:
    """Safe JSON writes with atomic rename."""
    
    @staticmethod
    def write_json(file_path: str, data: Dict[str, Any], backup: bool = True) -> bool:
        """
        Write JSON atomically: temp file → validate → rename.
        
        Args:
            file_path: Target path
            data: Dict to serialize
            backup: Keep .bak of previous version
        
        Returns:
            True if successful, False if failed
        """
        try:
            # Create parent directory if needed
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Write to temp file in same directory (ensures same filesystem)
            temp_fd, temp_path = tempfile.mkstemp(
                dir=str(Path(file_path).parent),
                prefix='.tmp_',
                suffix='.json'
            )
            
            try:
                with os.fdopen(temp_fd, 'w') as f:
                    json.dump(data, f, indent=2)
                
                # Validate written file
                with open(temp_path, 'r') as f:
                    json.load(f)
                
                # Backup existing file if present
                if backup and os.path.exists(file_path):
                    backup_path = f"{file_path}.bak"
                    if os.path.exists(backup_path):
                        os.remove(backup_path)
                    os.rename(file_path, backup_path)
                
                # Atomic rename
                os.rename(temp_path, file_path)
                logger.debug(f"Wrote {file_path} atomically")
                return True
            
            except Exception as e:
                # Clean up temp file on error
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                logger.error(f"Failed to write {file_path}: {e}")
                return False
        
        except Exception as e:
            logger.error(f"Atomic write failed for {file_path}: {e}")
            return False
    
    @staticmethod
    def read_json_safe(file_path: str, default: Dict = None) -> Dict[str, Any]:
        """
        Read JSON with fallback to .bak if corrupted.
        
        Args:
            file_path: Target path
            default: Return if both primary and backup fail
        
        Returns:
            Parsed JSON or default
        """
        if default is None:
            default = {}
        
        # Try primary file
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Corrupted {file_path}: {e}, trying backup...")
        
        # Try backup
        backup_path = f"{file_path}.bak"
        if os.path.exists(backup_path):
            try:
                with open(backup_path, 'r') as f:
                    data = json.load(f)
                    logger.warning(f"Recovered from {backup_path}")
                    return data
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Backup also corrupted {backup_path}: {e}")
        
        return default


class FileLocker:
    """Simple file-based locking for JSON coordination."""
    
    def __init__(self, file_path: str, timeout: float = 10.0):
        self.lock_path = f"{file_path}.lock"
        self.timeout = timeout
    
    def acquire(self) -> bool:
        """Try to acquire lock (non-blocking)."""
        try:
            # O_CREAT | O_EXCL fails if file exists
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.close(fd)
            return True
        except FileExistsError:
            return False
    
    def release(self) -> None:
        """Release lock."""
        try:
            if os.path.exists(self.lock_path):
                os.remove(self.lock_path)
        except Exception as e:
            logger.error(f"Failed to release lock {self.lock_path}: {e}")
    
    def __enter__(self):
        """Context manager entry."""
        import time
        start = time.time()
        while not self.acquire():
            if time.time() - start > self.timeout:
                raise TimeoutError(f"Could not acquire lock {self.lock_path} within {self.timeout}s")
            time.sleep(0.01)
        return self
    
    def __exit__(self, *args):
        """Context manager exit."""
        self.release()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Test atomic write
    test_path = "/tmp/test_atomic.json"
    test_data = {"test": "data", "nested": {"key": "value"}}
    
    writer = AtomicFileWriter()
    assert writer.write_json(test_path, test_data)
    assert writer.read_json_safe(test_path) == test_data
    
    print("✓ Atomic file utilities working")
