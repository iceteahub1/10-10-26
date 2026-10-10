"""Disposable HTTP server for the optional Chromium regression checks."""
from dataclasses import replace
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import Mock
import uvicorn
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from bistro.config import load_settings

with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    offline_port = sock.getsockname()[1]
with tempfile.TemporaryDirectory() as folder:
    settings = replace(load_settings(), port=port, ai_enabled=True, ollama_url=f'http://127.0.0.1:{offline_port}')
    client = Mock(model=settings.model)
    client.classify_request.return_value = 'general'
    client.chat.return_value = {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {
        'name': 'add_item_to_order', 'arguments': {'item_name': 'Chicken Adobo', 'qty': 2, 'instructions': ''}}}]}
    kwargs = {'client_factory': lambda: client} if '--ai' in sys.argv else {}
    app = create_app(settings=settings, db_path=Path(folder)/'restaurant.db', acceptance_log_path=Path(folder)/'acceptance.jsonl', **kwargs)
    print(f'http://127.0.0.1:{port}', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='critical')
