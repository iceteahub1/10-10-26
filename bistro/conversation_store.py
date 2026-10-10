"""Structured, session-isolated history storage; no model or text interpretation."""
import json
import sqlite3
from bistro.database import connection


def migrate(con, root):
    row = con.execute("SELECT value FROM schema_metadata WHERE key='conversation_schema_version'").fetchone()
    if row:
        if row[0] != '1':
            raise RuntimeError('Unsupported conversation schema version; data preserved.')
        required = {'conversation_sessions': {'session_id','state','context_json','review_json','updated_at'},
                    'conversation_messages': {'id','session_id','role','content_json','created_at'},
                    'conversation_transitions': {'id','session_id','from_state','to_state','reason','created_at'}}
        for table, columns in required.items():
            if not columns <= {r[1] for r in con.execute(f'PRAGMA table_info({table})')}:
                raise RuntimeError(f'Incompatible {table}; data preserved.')
        triggers = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        expected = {'conversation_transition_history', 'conversation_block_cancelled_receipt',
                    'conversation_block_closed_cart_insert', 'conversation_block_closed_cart_update',
                    'conversation_block_closed_cart_delete', 'conversation_invalidate_insert',
                    'conversation_invalidate_update', 'conversation_invalidate_delete'}
        if not expected <= triggers:
            raise RuntimeError('Conversation integrity triggers are missing; data preserved.')
        return
    # Do not overwrite unknown preexisting extension tables.
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables & {'conversation_sessions','conversation_messages','conversation_transitions'}:
        raise RuntimeError('Unversioned conversation tables; data preserved.')
    statement = ''
    for line in (root / 'migrations' / '001_conversation_memory.sql').read_text(encoding='utf-8').splitlines(True):
        statement += line
        if sqlite3.complete_statement(statement):
            con.execute(statement)
            statement = ''
    if statement.strip():
        raise RuntimeError('Incomplete conversation migration.')


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def record(con, session_id, role, content):
    con.execute('INSERT INTO conversation_messages(session_id,role,content_json) VALUES(?,?,?)',
                (session_id, role, encode(content)))


def load(con, session_id):
    row = con.execute('SELECT * FROM conversation_sessions WHERE session_id=?', (session_id,)).fetchone()
    if row is None:
        raise ValueError('No conversation for this session.')
    result = dict(row)
    result['context'] = json.loads(result.pop('context_json'))
    result['review'] = json.loads(result.pop('review_json')) if row['review_json'] else None
    return result


def get_context(session_id, *, db_path=None):
    """Phase 4 adapter: structured state plus only this session's ordered history."""
    with connection(db_path) as con:
        result = load(con, session_id)
        result['table_no'] = con.execute('SELECT table_no FROM table_sessions WHERE session_id=?', (session_id,)).fetchone()[0]
        result['messages'] = [dict(r) for r in con.execute(
            'SELECT id,role,content_json,created_at FROM conversation_messages WHERE session_id=? ORDER BY id', (session_id,))]
        for message in result['messages']:
            message['content'] = json.loads(message.pop('content_json'))
        result['transitions'] = [dict(r) for r in con.execute(
            'SELECT from_state,to_state,reason,created_at FROM conversation_transitions WHERE session_id=? ORDER BY id', (session_id,))]
        return result
