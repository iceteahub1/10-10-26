"""Opt-in REAL Qwen smoke test; uses disposable SQLite, never the restaurant DB."""
import json
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bistro.ai_engine import AIWaiter
from bistro.config import load_settings
from bistro.database import initialize_database
from bistro.ollama_client import OllamaClient
from fsm_agent import ConversationAgent, STATE_REVIEW, STATE_CONFIRMED


def main():
    settings = load_settings()
    client = OllamaClient(settings.ollama_url, settings.model, os.getenv('BISTRO_OLLAMA_TIMEOUT', '120'))
    with tempfile.TemporaryDirectory(prefix='bistro-real-qwen-') as folder:
        db = Path(folder) / 'validation.db'
        initialize_database(db)
        agent = ConversationAgent(db_path=db)
        agent.execute({'action': 'establish_session', 'arguments': {'table_no': 3}})
        engine = AIWaiter(agent=agent, client=client)
        for text, expected in [('Dalawang Chicken Adobo po.', 'add'),
                               ('Magkano na total?', 'total'),
                               ('Can I review my order?', 'review'),
                               ('Yes, confirm my order.', 'confirm')]:
            result = engine.respond(text)
            print(json.dumps({'customer': text, 'result': result}, ensure_ascii=False, indent=2))
            if not result['ok']:
                return 1
            context = agent.context()
            cart = context['order']
            if expected == 'add' and not (len(cart['items']) == 1 and cart['items'][0]['name'] == 'Chicken Adobo' and cart['items'][0]['quantity'] == 2):
                print('FAIL: Real model did not produce the expected cart.')
                return 1
            if expected == 'review' and agent.state != STATE_REVIEW:
                print('FAIL: Real model did not request review.')
                return 1
            if expected == 'confirm' and (agent.state != STATE_CONFIRMED or not cart.get('receipt_id')):
                print('FAIL: Real model did not finalize a receipt.')
                return 1
        print('PASS: Real local Qwen conversation/tool smoke test. Disposable data removed.')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
