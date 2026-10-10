"""Opt-in REAL selected-model acceptance test; uses disposable SQLite only."""
import argparse
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
from bistro import order_service as orders
from fsm_agent import ConversationAgent, STATE_REVIEW, STATE_CONFIRMED


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', help='Installed exact Qwen2.5 tag; defaults to saved selection. Never downloads or changes preferences.')
    parser.add_argument('--transport', choices=('auto','native','structured'), default='auto', help='Initial transport; native automatically falls back if explicitly unsupported.')
    args = parser.parse_args(argv)
    settings = load_settings()
    model = args.model or settings.model
    client = OllamaClient(settings.ollama_url, model, os.getenv('BISTRO_OLLAMA_TIMEOUT', '120'))
    print(f'REAL OLLAMA ACCEPTANCE: {model}. No model downloads, preference changes, or real restaurant data access.')
    with tempfile.TemporaryDirectory(prefix='bistro-real-qwen-') as folder:
        db = Path(folder) / 'validation.db'
        initialize_database(db)
        agent = ConversationAgent(db_path=db)
        engine = AIWaiter(agent=agent, client=client)
        engine.structured = args.transport == 'structured' or (args.transport == 'auto' and settings.ai_transport == 'structured')
        first = engine.respond('Dalawang Chicken Adobo po.')
        if not first['ok'] or agent.session_id is not None or first['inference_verified']:
            print('FAIL: Python did not ask for a table safely before inference.')
            return 1
        print('PASS: No guessed table/session; pending customer request retained.')
        stock_before = orders.check_stock(1, db_path=db)['stock_qty']
        steps = [('Table 3 po.', 'add'),
                 ('Magkano na total?', 'total'),
                 ('Can I review my order?', 'review'),
                 ('Do not confirm yet. What is my total?', 'no-consent'),
                 ('Yes, confirm my order.', 'confirm')]
        for text, expected in steps:
            result = engine.respond(text)
            print(json.dumps({'customer': text, 'result': result}, ensure_ascii=True, indent=2))
            # An incomplete turn is a failure even when another mutation was
            # saved. A read-only result must never pass as partial order success.
            if not result['ok']:
                print('FAIL: Customer request incomplete. Inspect authoritative feedback; do not replay saved mutations.')
                return 1
            if result.get('partial_success') or result['source'] != 'application_results':
                print('FAIL: Expected authoritative completion, without a required model acknowledgement.')
                return 1
            if expected == 'add' and not result.get('order_mutation_succeeded'):
                print('FAIL: A read-only action did not fulfill the pending food addition.')
                return 1
            context = agent.context()
            cart = context['order']
            if context['table_no'] != 3 or not (len(cart['items']) == 1 and cart['items'][0]['name'] == 'Chicken Adobo' and cart['items'][0]['quantity'] == 2):
                print('FAIL: Expected one Python-owned table session and exactly two Chicken Adobo, without duplicates.')
                return 1
            if expected in ('add', 'total', 'review') and ('Total: PHP ' + cart['total'] + '.') not in result['response']:
                print('FAIL: Acknowledgement did not show the authoritative SQLite total.')
                return 1
            if expected in ('review','no-consent') and agent.state != STATE_REVIEW:
                print('FAIL: Review was not preserved, or checkout happened without direct consent.')
                return 1
            if expected == 'confirm' and (agent.state != STATE_CONFIRMED or not cart.get('receipt_id') or 'Saved receipt:' not in result['response']):
                print('FAIL: Explicit confirmation did not produce the saved receipt acknowledgement.')
                return 1
            expected_stock = stock_before - 2 if expected == 'confirm' else stock_before
            if orders.check_stock(1, db_path=db)['stock_qty'] != expected_stock:
                print('FAIL: Inventory changed before consent or was deducted more than once.')
                return 1
        print(f'PASS: Real {model} acceptance; transport={"structured" if engine.structured else "native"}; incomplete turns=0. Disposable data removed.')
        return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'ACCEPTANCE FAILED: {exc}', file=sys.stderr)
        raise SystemExit(1)
