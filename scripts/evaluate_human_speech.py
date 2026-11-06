"""Human-only es-MX pilot. Calls transcription only; never calls an assistant."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import urlsplit
import wave
import httpx

PHRASES = ['¿Qué es una VPN?', '¿Qué es Active Directory?', '¿Cuál es la versión más reciente de Windows 11?', 'Según este manual, ¿cuál es el mantenimiento de las computadoras?', '¿Y cuál es el segundo paso?']
TERMS = { '1': ['vpn'], '2': ['active', 'directory'], '3': ['windows', '11'], '4': ['mantenimiento', 'computadoras'], '5': ['segundo', 'paso'] }


def tokens(text):
    folded = ''.join(c for c in unicodedata.normalize('NFD', text.casefold()) if unicodedata.category(c) != 'Mn')
    return re.findall(r'\w+', folded)


def align(reference, hypothesis):
    """Case/accent/punctuation-folded word alignment; no acronym/number rewriting."""
    a, b = tokens(reference), tokens(hypothesis)
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1): dp[i][0] = i
    for j in range(len(b) + 1): dp[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            dp[i][j] = min(dp[i-1][j-1] + (a[i-1] != b[j-1]), dp[i-1][j] + 1, dp[i][j-1] + 1)
    i, j = len(a), len(b)
    steps = []
    while i or j:
        if i and j and dp[i][j] == dp[i-1][j-1] + (a[i-1] != b[j-1]):
            steps.append({'operation': 'equal' if a[i-1] == b[j-1] else 'substitute', 'reference': a[i-1], 'hypothesis': b[j-1]}); i -= 1; j -= 1
        elif i and dp[i][j] == dp[i-1][j] + 1:
            steps.append({'operation': 'delete', 'reference': a[i-1], 'hypothesis': None}); i -= 1
        else:
            steps.append({'operation': 'insert', 'reference': None, 'hypothesis': b[j-1]}); j -= 1
    return {'wer': dp[-1][-1] / max(len(a), 1), 'reference_words': len(a),
            'substitutions': sum(s['operation'] == 'substitute' for s in steps),
            'deletions': sum(s['operation'] == 'delete' for s in steps),
            'insertions': sum(s['operation'] == 'insert' for s in steps), 'alignment': steps[::-1]}


def endpoint(base):
    u = urlsplit(base)
    if u.scheme != 'http' or u.hostname not in {'127.0.0.1', 'localhost', '::1'} or u.username or u.password or u.query or u.fragment or u.path not in {'', '/', '/api/v1/voice/transcribe'}:
        raise ValueError('Use a loopback HTTP base URL or exact transcription endpoint')
    return base.rstrip('/') if u.path == '/api/v1/voice/transcribe' else base.rstrip('/') + '/api/v1/voice/transcribe'


def validate_manifest(path):
    path = Path(path).resolve()
    rows = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(rows, list) or not rows: raise ValueError('Manifest must be a nonempty list')
    seen = set()
    for item in rows:
        if not isinstance(item, dict): raise ValueError('Manifest entries must be objects')
        if not all(isinstance(item.get(k), str) and item[k].strip() for k in ['path', 'speaker_id', 'condition', 'locale', 'phrase_id', 'reference']):
            raise ValueError('Manifest metadata fields must be nonempty strings')
        if item.get('provenance') != 'human_recorded' or item.get('kind', 'human') != 'human': raise ValueError('Explicit human_recorded provenance required; synthetic/unknown prohibited')
        pid = item.get('phrase_id')
        if pid not in TERMS or item.get('reference') != PHRASES[int(pid)-1]: raise ValueError('Exact phrase_id/reference required')
        if item.get('locale') != 'es-MX' or not re.fullmatch(r'speaker-\d{2,}', item.get('speaker_id', '')): raise ValueError('Require es-MX and anonymous speaker-NN')
        if item.get('condition') not in {'quiet', 'noise'} or type(item.get('repeat')) is not int or item['repeat'] < 1: raise ValueError('Require quiet/noise condition and positive repeat')
        key = (item['speaker_id'], item['condition'], pid, item['repeat'])
        if key in seen: raise ValueError('Duplicate speaker/condition/phrase/repeat')
        seen.add(key)
        wav = path.parent / item['path']
        try:
            if wav.stat().st_size > 960044: raise ValueError('WAV exceeds byte limit')
            with wave.open(str(wav), 'rb') as f:
                n = f.getnframes()
                if f.getnchannels() != 1 or f.getsampwidth() != 2 or f.getframerate() != 16000 or f.getcomptype() != 'NONE' or not .1 <= n / 16000 <= 30 or len(f.readframes(n)) != n * 2:
                    raise ValueError('Require mono PCM16 16000Hz, 0.1–30 seconds, complete frames')
        except (OSError, EOFError, wave.Error, ValueError) as exc:
            raise ValueError(f'Invalid/missing WAV: {item["path"]}') from exc
    groups = {(x['speaker_id'], x['condition']) for x in rows}
    for group in groups:
        if {x['phrase_id'] for x in rows if (x['speaker_id'], x['condition']) == group} != set(TERMS): raise ValueError('Incomplete five phrase coverage per speaker/condition')
    return rows


def post_audio(url, *, data, headers):
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(100, connect=3)) as client:
        return client.post(url, content=data, headers=headers)


def valid_response(payload):
    if not isinstance(payload, dict): return False
    if not all(isinstance(payload.get(k), str) for k in ['raw_text', 'normalized_text']): return False
    if type(payload.get('needs_clarification')) is not bool: return False
    if not isinstance(payload.get('quality_reasons'), list) or not all(isinstance(x, str) for x in payload['quality_reasons']): return False
    if not isinstance(payload.get('words'), list): return False
    for w in payload['words']:
        if not isinstance(w, dict) or not isinstance(w.get('word'), str): return False
        if 'conf' in w and w['conf'] is not None and (type(w['conf']) not in (float, int) or not math.isfinite(w['conf']) or not 0 <= w['conf'] <= 1): return False
    return True


def evaluate(path, base, token=None):
    url = endpoint(base)
    rows = validate_manifest(path)  # Validate every recording before any request.
    report = {'evaluation': 'human_recorded_es-MX_pilot', 'confidence_warning': 'Engine estimates are not calibrated probabilities.', 'cases': []}
    headers = {'Content-Type': 'audio/wav'}
    if token: headers['Authorization'] = 'Bearer ' + token
    for item in rows:
        row = dict(item)
        start = time.perf_counter()
        try:
            audio = (Path(path).resolve().parent / item['path']).read_bytes()
            row['recording_sha256'] = hashlib.sha256(audio).hexdigest()
            response = post_audio(url, data=audio, headers=headers)
            row['http_status'] = response.status_code
            if response.status_code != 200:
                row['error'] = f'HTTP {response.status_code}'
                try:
                    code = response.json().get('error', {}).get('code')
                    if isinstance(code, str) and re.fullmatch(r'[a-z0-9_]{1,128}', code):
                        row['server_error_code'] = code
                except (ValueError, AttributeError, TypeError):
                    pass
            else:
                payload = response.json()
                if not valid_response(payload): raise ValueError('Invalid transcription response')
                row['response'] = payload
                row['raw_wer'] = align(item['reference'], payload['raw_text'])
                row['normalized_wer'] = align(item['reference'], payload['normalized_text'])
                missing = [t for t in TERMS[item['phrase_id']] if t not in tokens(payload['normalized_text'])]
                scores = [w.get('conf') for w in payload['words'] if type(w.get('conf')) in (int, float) and math.isfinite(w['conf'])]
                row['missing_or_changed_key_terms'] = missing
                row['high_confidence_wrong_key_terms'] = bool(missing and scores and min(scores) >= .85)
                row['unblocked_wrong_key_terms'] = bool(missing and not payload['needs_clarification'])
        except httpx.HTTPError:
            row['error'] = 'HTTP transport failure'  # Do not serialize exception/request headers.
        except (ValueError, TypeError, KeyError):
            row['error'] = 'Invalid transcription response'
        row['elapsed_ms'] = round((time.perf_counter() - start) * 1000, 3)
        report['cases'].append(row)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', required=True); p.add_argument('--base-url', default='http://127.0.0.1:8000')
    p.add_argument('--output'); p.add_argument('--validate-only', '--dry-run', action='store_true')
    p.add_argument('--bearer-env', default='ITGLASSES_BEARER_TOKEN')
    args = p.parse_args()
    try:
        if args.validate_only:
            rows = validate_manifest(args.manifest); print(f'Validated {len(rows)} human WAV entries; no network requests.'); return 0
        if not args.output: p.error('--output required unless --validate-only')
        report = evaluate(args.manifest, args.base_url, os.environ.get(args.bearer_env))
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return int(any('error' in r for r in report['cases']))
    except (OSError, ValueError) as exc:
        p.error(str(exc))


if __name__ == '__main__': raise SystemExit(main())
