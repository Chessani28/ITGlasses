import asyncio
import sys
sys.path.insert(0, r'd:\ITGlasses')

async def quick_test():
    from backend.app.providers.ollama import OllamaProvider
    from backend.app.core.config import settings
    p = OllamaProvider(settings)
    available = await p.is_available()
    print(f'1. Ollama chat works: {available}')
    
    # Test a simple generation
    result = await p.generate('Di hola en español')
    result_str = result[:50] if result else "empty"
    print(f'2. Ollama generates Spanish: {result_str}...')

asyncio.run(quick_test())