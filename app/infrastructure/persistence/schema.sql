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

CREATE TABLE IF NOT EXISTS evidence_ingestions (
    acquisition_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    round_number INTEGER NOT NULL CHECK (round_number >= 0),
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    ingested_at TEXT NOT NULL,
    document_ids TEXT NOT NULL,
    chunk_ids TEXT NOT NULL,
    skipped_result_ids TEXT NOT NULL,
    new_document_count INTEGER NOT NULL CHECK (new_document_count >= 0),
    reused_document_count INTEGER NOT NULL CHECK (reused_document_count >= 0),
    new_chunk_count INTEGER NOT NULL CHECK (new_chunk_count >= 0),
    FOREIGN KEY (conversation_id)
        REFERENCES conversations(conversation_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evidence_queries (
    acquisition_id TEXT NOT NULL,
    query_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    kind TEXT NOT NULL CHECK (kind IN ('original', 'diversified')),
    query_text TEXT NOT NULL CHECK (length(trim(query_text)) > 0),
    facet TEXT,
    research_goal TEXT,
    PRIMARY KEY (acquisition_id, query_id),
    UNIQUE (acquisition_id, position),
    FOREIGN KEY (acquisition_id)
        REFERENCES evidence_ingestions(acquisition_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evidence_documents (
    document_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    raw_content TEXT NOT NULL CHECK (length(trim(raw_content)) > 0),
    content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
    acquired_at TEXT NOT NULL,
    UNIQUE (conversation_id, canonical_url, content_hash),
    FOREIGN KEY (conversation_id)
        REFERENCES conversations(conversation_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evidence_document_discoveries (
    acquisition_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    query_id TEXT NOT NULL,
    result_id TEXT NOT NULL,
    provider_rank INTEGER NOT NULL CHECK (provider_rank BETWEEN 1 AND 5),
    original_url TEXT NOT NULL,
    provider_result_id TEXT,
    PRIMARY KEY (acquisition_id, query_id, result_id),
    FOREIGN KEY (acquisition_id, query_id)
        REFERENCES evidence_queries(acquisition_id, query_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id)
        REFERENCES evidence_documents(document_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evidence_chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    text TEXT NOT NULL CHECK (length(trim(text)) > 0),
    heading_path TEXT NOT NULL DEFAULT '[]',
    start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK (end_offset > start_offset),
    chunker_version TEXT NOT NULL,
    UNIQUE (document_id, chunker_version, chunk_index),
    FOREIGN KEY (document_id)
        REFERENCES evidence_documents(document_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evidence_chunk_embeddings (
    chunk_id TEXT NOT NULL,
    embedding_version TEXT NOT NULL,
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    vector BLOB NOT NULL CHECK (length(vector) = dimension * 4),
    created_at TEXT NOT NULL,
    PRIMARY KEY (chunk_id, embedding_version),
    FOREIGN KEY (chunk_id)
        REFERENCES evidence_chunks(chunk_id) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE IF NOT EXISTS evidence_chunks_fts USING fts5(
    text,
    content='evidence_chunks',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS evidence_chunks_fts_insert
AFTER INSERT ON evidence_chunks BEGIN
    INSERT INTO evidence_chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER IF NOT EXISTS evidence_chunks_fts_delete
AFTER DELETE ON evidence_chunks BEGIN
    INSERT INTO evidence_chunks_fts(evidence_chunks_fts, rowid, text)
        VALUES ('delete', old.rowid, old.text);
END;

CREATE TRIGGER IF NOT EXISTS evidence_chunks_fts_update
AFTER UPDATE ON evidence_chunks BEGIN
    INSERT INTO evidence_chunks_fts(evidence_chunks_fts, rowid, text)
        VALUES ('delete', old.rowid, old.text);
    INSERT INTO evidence_chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;
