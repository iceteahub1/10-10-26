"""Bounded, privacy-safe acceptance events, independent of application.log."""
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from uuid import uuid4
from bistro.paths import LOGS

STATES = {'STATE_GREETING', 'STATE_TAKING_ORDER', 'STATE_REVIEW', 'STATE_CONFIRMED', 'STATE_CANCELLED'}
CODES = {'ready', 'ai_disabled', 'model_missing', 'ollama_unavailable', 'ai_unavailable',
         'out_of_stock', 'stale_review', 'application_failure', 'invalid_input', 'session_required',
         'session_active', 'duplicate_request', 'request_pending', 'request_interrupted', 'forbidden'}
ACTIONS = {'add_item', 'update_quantity', 'remove_item', 'update_instructions'}
SOURCES = {'controls', 'ai'}
FIELDS = {
    'application_startup': {'phase': int},
    'ai_availability': {'available': bool, 'code': CODES},
    'menu_availability': {'available': bool, 'item_count': int, 'available_count': int},
    'cart_action': {'action': ACTIONS, 'ok': bool, 'source': SOURCES, 'replayed': bool},
    'review_transition': {'from_state': STATES, 'to_state': STATES, 'ok': bool, 'source': SOURCES},
    'checkout_result': {'ok': bool, 'receipt_saved': bool, 'source': SOURCES, 'replayed': bool},
    'receipt_created': {'source': SOURCES},
    'workflow_error': {'code': CODES, 'stage': {'startup', 'session', 'menu', 'chat', 'action', 'confirm', 'request'}},
}


class AcceptanceLog:
    """One server run, at most two 1 MiB files and the latest 1000 report events.

    Allowlisted metadata only: no messages, instructions, table/session/receipt
    identifiers, request IDs, cookies, credentials or exception text. Fail-open
    file errors must never prevent ordering or change its outcome.
    """
    def __init__(self, path=None, *, max_bytes=1048576):
        self.path = Path(path) if path is not None else LOGS / 'acceptance.jsonl'
        self.max_bytes = max_bytes
        self.lock = threading.Lock()
        self.run_id = uuid4().hex  # QA run identifier; never an authorization token.
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.available = True

    def start_run(self):
        with self.lock:
            self.run_id = uuid4().hex
            self.started_at = datetime.now(timezone.utc).isoformat()
        self.record('application_startup', phase=5)

    def record(self, event, **details):
        schema = FIELDS.get(event)
        if schema is None:
            return
        safe = {}
        for key, value in details.items():
            rule = schema.get(key)
            if isinstance(rule, set):
                if type(value) is str and value in rule:
                    safe[key] = value
            elif rule is not None and type(value) is rule:
                safe[key] = value
        entry = {'timestamp': datetime.now(timezone.utc).isoformat(), 'run_id': self.run_id, 'event': event, **safe}
        try:
            with self.lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                    self.path.replace(self.path.with_suffix(self.path.suffix + '.1'))
                with self.path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(entry, ensure_ascii=True) + '\n')
                self.available = True
        except OSError:
            self.available = False

    def report(self):
        events = []
        try:
            with self.lock:
                for path in (self.path.with_suffix(self.path.suffix + '.1'), self.path):
                    if not path.exists():
                        continue
                    # Bounded read, including a possible externally enlarged file.
                    with path.open('rb') as stream:
                        size = path.stat().st_size
                        stream.seek(max(0, size - self.max_bytes - 4096))
                        if stream.tell():
                            stream.readline()
                        raw = stream.read(self.max_bytes + 4096)
                    for line in raw.splitlines():
                        try:
                            entry = json.loads(line)
                            if entry.get('run_id') == self.run_id:
                                events.append(entry)
                        except (ValueError, AttributeError, UnicodeError):
                            continue
        except OSError:
            self.available = False
        return {'run_id': self.run_id, 'started_at': self.started_at, 'logging_available': self.available,
                'scope': 'Latest server run; bounded recent backend observations, not an automatic QA pass.',
                'events': events[-1000:]}
