"""Student terminal demonstration: explicit JSON actions, no LLM integration."""
import argparse
import json
import logging
from bistro.database import initialize_database
from bistro.config import load_settings
from fsm_agent import ConversationAgent, ACTION_ARGUMENTS

HELP = '''Perpetual Bistro — deterministic ordering (Phase 3)
Use one JSON object per line: {"action":"name","arguments":{...}}
help shows the action list; state shows session context; quit preserves the session.
Examples:
{"action":"establish_session","arguments":{"table_no":3}}
{"action":"browse_menu","arguments":{"category":"Mains"}}
{"action":"add_item","arguments":{"item_name":"Chicken Adobo","qty":2,"instructions":"less salt"}}
{"action":"request_review","arguments":{}}
{"action":"confirm_order","arguments":{"confirmed":true}}
Resume: establish_session with the same table_no (or table_no + session_id).
New customer: cancel or finish the current order, then new_session with table_no.
Line IDs come from view_order/add_item results. No natural-language interpretation.
'''


def show_help(output):
    output(HELP)
    for name, (required, optional) in ACTION_ARGUMENTS.items():
        output(f'{name}: required={sorted(required)}, optional={sorted(optional)}')


def main(argv=None, *, get_input=input, send_output=print):
    parser = argparse.ArgumentParser(description='Deterministic SQLite restaurant ordering CLI')
    parser.add_argument('--db', help='Optional separate SQLite database path')
    parser.add_argument('--debug', action='store_true', help='Show diagnostic logging on stderr')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING)
    try:
        initialize_database(args.db)
        settings = load_settings()
        agent = ConversationAgent(db_path=args.db, tax_rate=settings.tax_rate, service_rate=settings.service_rate)
        show_help(send_output)
        while True:
            try:
                raw = get_input(f'{agent.state}> ')
            except (EOFError, KeyboardInterrupt):
                send_output('\nSession preserved. Goodbye.')
                return 0
            if raw.strip().lower() in ('quit', 'exit'):
                send_output('Session preserved. Goodbye.')
                return 0
            if raw.strip().lower() == 'help':
                show_help(send_output)
                continue
            if raw.strip().lower() == 'state':
                send_output(json.dumps(agent.context(), ensure_ascii=False, indent=2))
                continue
            try:
                action = json.loads(raw)
            except (ValueError, TypeError):
                send_output('Please enter valid JSON or help. No action was executed.')
                continue
            send_output(json.dumps(agent.execute(action, message=raw), ensure_ascii=False, indent=2))
    except (Exception, KeyboardInterrupt) as exc:
        logging.debug('CLI failure', exc_info=True)
        send_output(f'Unable to run the ordering CLI: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
