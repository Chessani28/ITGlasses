import json
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

root = Path('D:/ITGlasses')
with socket.socket() as probe:
    probe.bind(('127.0.0.1', 8000))
process = subprocess.Popen([str(root / '.venv/Scripts/python.exe'), '-m', 'backend.run'], cwd=root)
try:
    for attempt in range(50):
        if process.poll() is not None:
            raise RuntimeError('Server exited before startup')
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=1) as response:
                assert json.load(response) == {'status': 'ok'}
            break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError('Server startup timeout')
    with urllib.request.urlopen('http://127.0.0.1:8000/api/v1/status') as response:
        assert json.load(response)['provider'] == 'mock'
    request = urllib.request.Request('http://127.0.0.1:8000/api/v1/chat',
        data=json.dumps({'message': 'Synthetic connectivity test'}).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request) as response:
        assert json.load(response)['sources'] == []
    result = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True, text=True, check=True)
    listeners = [line for line in result.stdout.splitlines() if len(line.split()) >= 5 and line.split()[1].endswith(':8000') and line.split()[2] in ('0.0.0.0:0', '[::]:0')]
    assert listeners and all(line.split()[1] == '127.0.0.1:8000' for line in listeners), listeners
    print('PASS: real HTTP health, status, chat; process listener exclusively 127.0.0.1:8000')
finally:
    process.terminate()
    process.wait(timeout=10)
    print('Verification server stopped')
