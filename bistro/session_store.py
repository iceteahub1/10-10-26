"""Persistent table sessions; conversation/FSM state is reserved for Phase 3."""
from uuid import uuid4
from bistro.database import connection


def validate_session_id(session_id):
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 128:
        raise ValueError('session_id must be a nonempty string of at most 128 characters.')
    return session_id


def create_session(table_no, session_id=None, *, db_path=None):
    if type(table_no) is not int or table_no <= 0:
        raise ValueError('table_no must be a positive integer.')
    session_id = validate_session_id(session_id if session_id is not None else uuid4().hex)
    with connection(db_path, write=True) as con:
        existing = con.execute('SELECT * FROM table_sessions WHERE session_id=?', (session_id,)).fetchone()
        if existing:
            if existing['table_no'] != table_no:
                raise ValueError('Session already belongs to another table.')
            return dict(existing)
        con.execute('INSERT INTO table_sessions(session_id, table_no) VALUES (?, ?)', (session_id, table_no))
        return dict(con.execute('SELECT * FROM table_sessions WHERE session_id=?', (session_id,)).fetchone())


def get_session(session_id, *, db_path=None):
    validate_session_id(session_id)
    with connection(db_path) as con:
        row = con.execute('SELECT * FROM table_sessions WHERE session_id=?', (session_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown session.')
        return dict(row)
