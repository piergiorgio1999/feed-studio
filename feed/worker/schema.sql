CREATE TABLE IF NOT EXISTS likes (video_id INTEGER PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, video_id INTEGER NOT NULL, text TEXT NOT NULL,
  flagged INTEGER NOT NULL DEFAULT 0,      -- 1 = "per Claude"
  status TEXT NOT NULL DEFAULT 'open',     -- open | read | done
  reply TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS c_video ON comments(video_id);
ALTER TABLE comments ADD COLUMN target TEXT;  -- 'claude' | 'codex' | NULL (applicato)
