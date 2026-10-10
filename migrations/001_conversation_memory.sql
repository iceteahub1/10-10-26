-- Independently versioned extension to the preserved Phase 2 user_version=1 schema.
CREATE TABLE conversation_sessions (
 session_id TEXT PRIMARY KEY REFERENCES table_sessions(session_id),
 state TEXT NOT NULL CHECK(state IN ('STATE_GREETING','STATE_TAKING_ORDER','STATE_REVIEW','STATE_CONFIRMED','STATE_CANCELLED')),
 context_json TEXT NOT NULL DEFAULT '{}',
 review_json TEXT,
 updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE conversation_messages (
 id INTEGER PRIMARY KEY,
 session_id TEXT NOT NULL REFERENCES conversation_sessions(session_id),
 role TEXT NOT NULL CHECK(role IN ('customer','assistant','system')),
 content_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE conversation_transitions (
 id INTEGER PRIMARY KEY,
 session_id TEXT NOT NULL REFERENCES conversation_sessions(session_id),
 from_state TEXT NOT NULL,
 to_state TEXT NOT NULL,
 reason TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX conversation_messages_session_idx ON conversation_messages(session_id,id);
CREATE INDEX conversation_transitions_session_idx ON conversation_transitions(session_id,id);
CREATE TRIGGER conversation_transition_history AFTER UPDATE OF state ON conversation_sessions
WHEN OLD.state != NEW.state BEGIN
 INSERT INTO conversation_transitions(session_id,from_state,to_state,reason)
 VALUES(NEW.session_id,OLD.state,NEW.state,'validated action or cart modification');
END;
CREATE TRIGGER conversation_block_cancelled_receipt BEFORE INSERT ON receipts
WHEN EXISTS(SELECT 1 FROM conversation_sessions WHERE session_id=NEW.session_id AND state='STATE_CANCELLED')
BEGIN SELECT RAISE(ABORT,'This order was cancelled; start a new session.'); END;
CREATE TRIGGER conversation_block_closed_cart_insert BEFORE INSERT ON order_line_items
WHEN EXISTS(SELECT 1 FROM conversation_sessions WHERE session_id=NEW.session_id AND state IN ('STATE_CANCELLED','STATE_CONFIRMED'))
BEGIN SELECT RAISE(ABORT,'This conversation order is closed.'); END;
CREATE TRIGGER conversation_block_closed_cart_update BEFORE UPDATE ON order_line_items
WHEN EXISTS(SELECT 1 FROM conversation_sessions WHERE session_id IN (OLD.session_id,NEW.session_id) AND state IN ('STATE_CANCELLED','STATE_CONFIRMED'))
BEGIN SELECT RAISE(ABORT,'This conversation order is closed.'); END;
CREATE TRIGGER conversation_block_closed_cart_delete BEFORE DELETE ON order_line_items
WHEN EXISTS(SELECT 1 FROM conversation_sessions WHERE session_id=OLD.session_id AND state IN ('STATE_CANCELLED','STATE_CONFIRMED'))
BEGIN SELECT RAISE(ABORT,'This conversation order is closed.'); END;
CREATE TRIGGER conversation_invalidate_insert AFTER INSERT ON order_line_items BEGIN
 UPDATE conversation_sessions SET state='STATE_TAKING_ORDER',review_json=NULL
 WHERE session_id=NEW.session_id AND state='STATE_REVIEW';
END;
CREATE TRIGGER conversation_invalidate_update AFTER UPDATE ON order_line_items BEGIN
 UPDATE conversation_sessions SET state='STATE_TAKING_ORDER',review_json=NULL
 WHERE session_id IN (OLD.session_id,NEW.session_id) AND state='STATE_REVIEW';
END;
CREATE TRIGGER conversation_invalidate_delete AFTER DELETE ON order_line_items BEGIN
 UPDATE conversation_sessions SET state='STATE_TAKING_ORDER',review_json=NULL
 WHERE session_id=OLD.session_id AND state='STATE_REVIEW';
END;
INSERT INTO schema_metadata(key,value) VALUES('conversation_schema_version','1');
