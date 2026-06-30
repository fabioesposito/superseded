from __future__ import annotations

# Shared SQL CASE expression for classifying files into pattern buckets for
# review_stats aggregation. Used by both MemoryStore (SQLite) and PostgresStore.
# NOTE: SQLite LIKE and Postgres LIKE both treat `%` as the wildcard, so a
# single source of truth works for both backends.
STATS_FILE_PATTERN_CASE = """\
CASE
    WHEN f.file LIKE 'test/%' OR f.file LIKE 'tests/%'
         OR f.file LIKE '%_test.%' OR f.file LIKE 'test_%'
         OR f.file LIKE '%__test__/%' THEN 'test'
    WHEN f.file LIKE '%migrations/%' THEN 'migration'
    WHEN f.file LIKE '%.yaml' OR f.file LIKE '%.yml'
         OR f.file LIKE '%.toml' OR f.file LIKE '%.json'
         OR f.file LIKE 'Dockerfile%' THEN 'config'
    ELSE '*'
END"""
