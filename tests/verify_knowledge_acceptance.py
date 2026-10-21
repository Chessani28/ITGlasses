"""Real-model acceptance benchmark. Synthetic corpus only; no downloads."""
import asyncio
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path('D:/ITGlasses')
sys.path.insert(0, str(ROOT))
import httpx
from backend.app.knowledge.embeddings import OllamaEmbeddings
from backend.app.knowledge.grounding import EvidenceSelector
from backend.app.knowledge.models import KnowledgeRequest, Metadata
from backend.app.knowledge.service import KnowledgeService

RUN = ROOT / 'storage/acceptance' / datetime.now().strftime('%Y%m%d-%H%M%S')
RUN.mkdir(parents=True)
samples = []
phase = 'baseline'
stop = threading.Event()


class Memory(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(name, ctypes.c_ulonglong) for name in ['total','available','page_total','page_available','virtual_total','virtual_available','extended']]


def measure():
    memory = Memory(); memory.length = ctypes.sizeof(memory)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
    gpu = subprocess.check_output(['nvidia-smi','--query-gpu=memory.used,memory.free','--format=csv,noheader,nounits'], text=True)
    used, free = map(int, gpu.strip().split(','))
    command = "@(Get-Process ollama -ErrorAction SilentlyContinue | Where-Object { $_.Path -like 'D:\\ITGlasses\\storage\\runtime\\*' } | Select-Object Id,CPU,WorkingSet64) | ConvertTo-Json -Compress"
    process = subprocess.run(['powershell','-NoProfile','-Command',command], capture_output=True, text=True)
    procs = json.loads(process.stdout) if process.stdout.strip() else []
    if isinstance(procs, dict): procs = [procs]
    return {'t':time.time(),'phase':phase,'free_ram_mib':round(memory.available/2**20,2),
            'gpu_used_mib':used,'gpu_free_mib':free,'processes':procs}


def monitor():
    while not stop.wait(1):
        try: samples.append(measure())
        except Exception: pass


async def main():
    global phase
    baseline = measure(); samples.append(baseline)
    if baseline['free_ram_mib'] < 6144 or baseline['gpu_free_mib'] < 3500:
        raise RuntimeError('Insufficient resource headroom for benchmark')
    worker = threading.Thread(target=monitor, daemon=True); worker.start()
    embedder = OllamaEmbeddings()
    svc = KnowledgeService(RUN, embedder, EvidenceSelector('qwen3:4b-instruct-2507-q4_K_M'))
    report = {'run_root':str(RUN),'baseline':baseline,'ingestion':[], 'cases':{}}
    try:
        phase = 'embedding_cpu_probe'
        start = time.perf_counter()
        await embedder.embed(['El procedimiento consiste en formatear el disco y reinstalar Windows. ' * 8] * 16)
        async with httpx.AsyncClient(trust_env=False) as client:
            report['embedding_loaded'] = (await client.get('http://127.0.0.1:11434/api/ps')).json()
        report['embedding_probe_seconds'] = round(time.perf_counter()-start,3)
        samples.append(measure())
        assert all(m['size_vram'] == 0 for m in report['embedding_loaded']['models'])
        await embedder.unload()

        async def add(title, text, pid='24', key=None, version='1', authorized=True):
            global phase
            phase = 'ingestion'
            start = time.perf_counter()
            result = await svc.ingest('synthetic.md', text.encode(), Metadata(title=title, scope='Laboratorio sintético', procedure_id=pid, procedure_key=key, version=version, authorized=authorized))
            report['ingestion'].append({'title':title, 'seconds':round(time.perf_counter()-start,3),'bytes':len(text.encode()),'chunks':result['chunk_count']})
            return result

        async def query(label, message, expected, **kwargs):
            global phase
            phase = label
            start = time.perf_counter()
            answer = await svc.query(KnowledgeRequest(message=message, **kwargs))
            record = answer.model_dump()
            record['seconds'] = round(time.perf_counter()-start,3)
            record['expected'] = expected
            record['passed'] = answer.status == expected
            report['cases'][label] = record
            print(json.dumps({'case':label,'status':answer.status,'expected':expected,'seconds':record['seconds'],'retrieval':answer.retrieval},ensure_ascii=False), flush=True)
            return answer

        computer = await add('Formateo e instalación limpia de Windows', 'El procedimiento consiste en formatear el disco y reinstalar Windows.')
        await query('A', 'Tengo una computadora con Windows y necesito quitarle todo para instalarlo desde cero con una USB, pero no recuerdo cómo se llama ese proceso.', 'answered')
        await query('B', '¿Cuál es el procedimiento 24?', 'answered')
        printer = await add('Sustitución de tóner de impresoras', 'El procedimiento consiste en cambiar el cartucho de tóner de una impresora láser.')
        payroll = await add('Revisión de nómina', 'El procedimiento consiste en revisar los importes de nómina y confirmar los salarios del personal.')
        ambiguous = await query('C', '¿Cuál es el procedimiento 24?', 'clarification_required')
        await query('D', 'El de las computadoras, donde se borra Windows.', 'answered', conversation_id=ambiguous.conversation_id)
        await query('E', '¿Quién debe aprobar los viajes internacionales de la empresa y cuál es el límite de reembolso?', 'insufficient_evidence')
        await query('E_related', '¿Cuál es la clave BitLocker oficial que usa la empresa para reinstalar Windows?', 'insufficient_evidence')
        await add('Rotación de credenciales de VPN', 'La versión 1 indica renovar las credenciales VPN cada 30 días.', pid='80', key='vpn-rotation', version='1')
        await add('Rotación de credenciales de VPN', 'La versión 2 indica renovar las credenciales VPN cada 90 días.', pid='80', key='vpn-rotation', version='2')
        await query('F', '¿Cuál es el procedimiento 80?', 'conflict')
        await query('long', 'Estoy ayudando a una persona que tiene un equipo de trabajo. ' * 12 + 'La computadora tiene Windows, quiero dejarla vacía e instalar el sistema desde cero con una USB. No recuerdo cómo se llama.', 'answered')
        pending = await query('revocation_pending', 'procedimiento 24', 'clarification_required')
        await svc.access(computer['id'], withdrawn=True)
        await query('revoked_followup', 'El de las computadoras donde se borra Windows', 'insufficient_evidence', conversation_id=pending.conversation_id)
        await add('Clave privada no autorizada Windows', 'El identificador secreto es SYNTHETIC-SECRET-123.', pid='99', authorized=False)
        await query('unauthorized', 'procedimiento 99', 'insufficient_evidence')
        for label in ['A','B','D','long']:
            report['cases'][label]['passed'] &= bool(report['cases'][label]['sources']) and all(s['document_id'] == computer['id'] for s in report['cases'][label]['sources'])
        report['cases']['C']['passed'] &= len(report['cases']['C']['options']) == 3
        report['cases']['F']['passed'] &= {o['version'] for o in report['cases']['F']['options']} == {'1','2'}
        report['all_passed'] = all(item['passed'] for item in report['cases'].values())
    finally:
        stop.set(); worker.join(timeout=5)
        await embedder.unload()
        async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
            await client.post('http://127.0.0.1:11434/api/generate', json={'model':'qwen3:4b-instruct-2507-q4_K_M','keep_alive':0})
        report['samples'] = samples
        report['peak_gpu_used_mib'] = max(s['gpu_used_mib'] for s in samples)
        report['minimum_free_ram_mib'] = min(s['free_ram_mib'] for s in samples)
        (RUN / 'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        print('Report: '+str(RUN / 'report.json'),flush=True)

asyncio.run(main())
