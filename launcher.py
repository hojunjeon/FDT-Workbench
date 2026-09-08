"""Single foreground server, automatic dependencies, browser launch, complete cleanup."""
from __future__ import annotations
import argparse
from importlib import metadata
import multiprocessing
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent


def ensure_dependencies(no_install: bool = False) -> None:
    missing = []
    for line in (ROOT / 'requirements-web.lock').read_text(encoding='utf-8').splitlines():
        item = line.split('#', 1)[0].strip()
        if not item:
            continue
        name, expected = item.split('==', 1)
        try:
            if metadata.version(name) != expected:
                missing.append(item)
        except metadata.PackageNotFoundError:
            missing.append(item)
    if not missing:
        return
    if no_install:
        raise RuntimeError('필요한 의존성을 설치하세요: ' + ', '.join(missing))
    if sys.prefix == sys.base_prefix:
        raise RuntimeError('START.bat 또는 start.sh를 사용하거나 가상환경에서 실행하세요. 시스템 Python에 자동 설치하지 않습니다.')
    print('[SETUP] Installing tested runtime dependencies. Internet is needed on first launch.', flush=True)
    process = subprocess.Popen([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--only-binary=:all:', '-r', str(ROOT / 'requirements-web.lock')], cwd=ROOT)
    try:
        code = process.wait()
        if code:
            raise RuntimeError('의존성 설치 실패. Python 3.11 이상 64-bit, 인터넷 연결, pip 로그를 확인하세요.')
    except KeyboardInterrupt:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise


def listen_socket(explicit_port: int | None):
    ports = [explicit_port] if explicit_port is not None else range(8765, 8785)
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # On Windows SO_REUSEADDR can steal a port. Use exclusive ownership.
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('127.0.0.1', port))
            sock.listen(128)
            return sock, sock.getsockname()[1]
        except OSError:
            sock.close()
    raise RuntimeError('포트를 사용할 수 없습니다. --port 8899처럼 다른 포트를 지정하세요. 다른 프로그램은 종료하지 않습니다.')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='KeyFin local FDT Workbench')
    parser.add_argument('--port', type=int, default=None, help='기본 8765, 점유 시 8784까지 탐색')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--no-install', action='store_true', help='QA: 의존성 자동 설치 없이 버전 검증')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'workbench_data')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        print('[ERROR] Python 3.11 or newer (64-bit) is required.', flush=True)
        return 1
    if args.port is not None and not 1 <= args.port <= 65535:
        print('[ERROR] Port must be 1..65535.', flush=True)
        return 1
    stop_browser = threading.Event()
    browser_thread = None
    try:
        ensure_dependencies(args.no_install)
        # No imports of optional packages until setup has completed.
        import uvicorn
        from workbench.app import create_app
        from workbench.locking import data_lock
        with data_lock(args.data_dir.resolve()):
            sock, port = listen_socket(args.port)
            url = f'http://127.0.0.1:{port}'
            app = create_app(args.data_dir, port)
            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                                     loop='asyncio', http='h11', workers=1, access_log=False,
                                     timeout_graceful_shutdown=3, log_level='info'))
            print('\n' + '=' * 60, flush=True)
            print('  KEYFIN / FDT WORKBENCH 0.2', flush=True)
            print(f'  Open: {url}', flush=True)
            print(f'  Data: {args.data_dir.resolve()}', flush=True)
            print('  Press Ctrl+C in THIS window to stop server + numeric workers.', flush=True)
            print('  Closing the browser tab does NOT stop the engine.', flush=True)
            print('=' * 60 + '\n', flush=True)
            if not args.no_browser:
                def open_when_ready():
                    for _ in range(100):
                        if stop_browser.wait(0.2):
                            return
                        if server.started:
                            try:
                                webbrowser.open_new_tab(url)
                            except Exception:
                                print(f'[BROWSER] Open this address manually: {url}', flush=True)
                            return
                browser_thread = threading.Thread(target=open_when_ready, name='browser-launch', daemon=True)
                browser_thread.start()
            try:
                server.run(sockets=[sock])
            finally:
                stop_browser.set()
                if hasattr(app.state, 'manager'):
                    app.state.manager.close()
                sock.close()
    except KeyboardInterrupt:
        print('\n[STOP] Ctrl+C received.', flush=True)
    except Exception as exc:
        print(f'\n[ERROR] {exc}', flush=True)
        return 1
    finally:
        stop_browser.set()
        if browser_thread:
            browser_thread.join(timeout=1)
    print('[STOPPED] Server and numeric workers are stopped. Saved Twins are retained.', flush=True)
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
