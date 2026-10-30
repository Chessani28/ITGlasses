import os
import subprocess

os.environ['ITGLASSES_PROVIDER'] = 'ollama'

backend_path = r'D:\ITGlasses\backend'
subprocess.run(['python', 'run.py'], cwd=backend_path)