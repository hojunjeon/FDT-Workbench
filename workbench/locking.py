"""Prevent two launcher instances from mutating the same local data directory."""
from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def data_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / '.server.lock').open('a+b')
    locked = False
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise RuntimeError('이 데이터 폴더를 사용하는 Workbench가 이미 실행 중입니다. 기존 실행 창을 확인하세요.') from exc
        yield
    finally:
        if locked:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
