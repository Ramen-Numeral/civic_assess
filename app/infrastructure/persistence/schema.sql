CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    next_sequence INTEGER NOT NULL DEFAULT 1 CHECK (next_sequence >= 1),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    sequence_number INTEGER NOT NULL CHECK (sequence_number >= 1),
    client_message_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL CHECK (length(trim(content)) > 0),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    FOREIGN KEY (conversation_id)
        REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    UNIQUE (conversation_id, sequence_number),
    CHECK (
        (role = 'user' AND client_message_id IS NOT NULL)
        OR (role = 'assistant' AND client_message_id IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS turns_client_message
    ON turns(conversation_id, client_message_id)
    WHERE client_message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS conversation_state (
    state_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL UNIQUE,
    summary_through_sequence INTEGER NOT NULL
        CHECK (summary_through_sequence >= 0),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    summarizer_version TEXT NOT NULL,
    current_goal TEXT,
    confirmed_decisions TEXT NOT NULL DEFAULT '[]',
    rejected_proposals TEXT NOT NULL DEFAULT '[]',
    superseded_decisions TEXT NOT NULL DEFAULT '[]',
    active_constraints TEXT NOT NULL DEFAULT '[]',
    open_questions TEXT NOT NULL DEFAULT '[]',
    important_corrections TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    FOREIGN KEY (conversation_id)
        REFERENCES conversations(conversation_id) ON DELETE CASCADE
);
