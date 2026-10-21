"""Synthetic end-to-end verification with sampled physical RAM and GPU usage."""
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import tomllib
import urllib.request
import urllib.error

root = Path('D:/ITGlasses')


class MemoryStatus(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                ('total', ctypes.c_ulonglong), ('available', ctypes.c_ulonglong),
                ('page_total', ctypes.c_ulonglong), ('page_available', ctypes.c_ulonglong),
                ('virtual_total', ctypes.c_ulonglong), ('virtual_available', ctypes.c_ulonglong),
                ('extended', ctypes.c_ulonglong)]


def sample():
    memory = MemoryStatus()
    memory.length = ctypes.sizeof(memory)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise OSError('Cannot read physical memory')
    result = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,memory.free',
                             '--format=csv,noheader,nounits'], capture_output=True, text=True, check=True)
    used, free = map(int, result.stdout.strip().split(','))
    return {'time': time.time(), 'ram_available_mib': round(memory.available / 2**20, 1),
            'gpu_used_mib': used, 'gpu_free_mib': free}


def api(base, path, payload=None, timeout=150):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(base + path, data=data,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


baseline = sample()
print('Pre-load resources:', json.dumps(baseline), flush=True)
if baseline['ram_available_mib'] < 6144 or baseline['gpu_free_mib'] < 3500:
    raise RuntimeError('Resource gate: require 6 GiB free RAM and 3500 MiB free VRAM before loading')
with socket.socket() as probe:
    probe.bind(('127.0.0.1', 8000))
environment = dict(os.environ, ITGLASSES_PROVIDER='ollama', TEMP=str(root / '.cache/tmp'), TMP=str(root / '.cache/tmp'))
process = subprocess.Popen([str(root / '.venv/Scripts/python.exe'), '-m', 'backend.run'], cwd=root, env=environment)
samples = [baseline]
stop = threading.Event()


def monitor():
    while not stop.wait(1):
        samples.append(sample())


thread = threading.Thread(target=monitor, daemon=True)
thread.start()
try:
    for attempt in range(100):
        if process.poll() is not None:
            raise RuntimeError('Backend exited')
        try:
            api('http://127.0.0.1:8000', '/health', timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError('Backend startup timed out')
    status = api('http://127.0.0.1:8000', '/api/v1/status')
    assert status['status'] == 'ready' and status['provider'] == 'ollama', status
    start = time.monotonic()
    answer = api('http://127.0.0.1:8000', '/api/v1/chat', {
        'message': '¿Cómo se llama el proceso de borrar Windows para realizar una instalación limpia?'})
    elapsed = round(time.monotonic() - start, 2)
    assert answer['provider'] == 'ollama' and answer['answer'].strip()
    assert 'Okay, the user' not in answer['answer'] and len(answer['answer']) < 1500, 'Unexpected reasoning-only response'
    assert answer['sources'] == [] and answer['documentation_sufficient'] is False
    loaded = api('http://127.0.0.1:11434', '/api/ps')
    samples.append(sample())
    netstat = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True, text=True, check=True)
    listeners = [line.strip() for line in netstat.stdout.splitlines() if len(line.split()) >= 5
                 and line.split()[1].endswith((':8000', ':11434'))
                 and line.split()[2] in ('0.0.0.0:0', '[::]:0')]
    assert len(listeners) == 2 and all(line.split()[1].startswith('127.0.0.1:') for line in listeners), listeners
    report = {'baseline': baseline, 'request_seconds': elapsed, 'status': status,
              'synthetic_response': answer, 'loaded_models': loaded, 'listeners': listeners,
              'minimum_sampled_free_ram_mib': min(item['ram_available_mib'] for item in samples),
              'maximum_sampled_gpu_used_mib': max(item['gpu_used_mib'] for item in samples),
              'samples': samples}
    (root / 'docs/phase2-measurements.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'samples'}, indent=2), flush=True)
finally:
    stop.set()
    thread.join(timeout=5)
    process.terminate()
    process.wait(timeout=10)
    # Release model memory explicitly after this synthetic verification.
    try:
        api('http://127.0.0.1:11434', '/api/generate', {'model': tomllib.loads((root / 'config.toml').read_text())['model'], 'keep_alive': 0})
    except OSError:
        pass
    print('Backend stopped; model unload requested.', flush=True)
