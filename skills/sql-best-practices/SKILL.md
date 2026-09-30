---
name: sql-best-practices
description: SQL and database best practices. Use when reading, editing, writing, or reviewing SQL, migrations, queries, or schema code.
---

# SQL best practices

Write database code that preserves data integrity, performs predictably, and can be changed safely.

## Rules

- Model invariants in the database when they are truly data rules: primary keys, foreign keys, unique constraints, checks, and not-null columns.
- Use migrations that are repeatable in order and safe for existing data. Include backfills when adding non-null columns.
- Avoid destructive migrations unless the user explicitly asks and the data impact is clear.
- Parameterize queries. Never concatenate untrusted strings into SQL.
- Select only columns needed by the caller. Avoid `SELECT *` in shipped paths.
- Check query plans for expensive joins, scans, ordering, and pagination on large tables.
- Add indexes to support real access patterns, not hypothetical ones.
- Keep transactions tight. Use them for consistency, not as a blanket wrapper around slow external work.
- Treat time zones and money carefully. Store canonical representations and convert at the edge.
- Prefer keyset pagination over offset pagination for large changing datasets.
- Keep schema-derived app types in sync with migrations or generated database types.
- Test important queries against a real local or test database when practical.
