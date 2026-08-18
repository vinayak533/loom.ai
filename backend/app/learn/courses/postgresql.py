"""PostgreSQL & SQL."""

from __future__ import annotations

COURSE = {
    "id": "postgresql",
    "title": "PostgreSQL & SQL",
    "short_title": "PostgreSQL",
    "subtitle": "Query it, model it, index it, keep it correct",
    "difficulty": "intermediate",
    "tags": ["Database", "Backend"],
    "description": (
        "SQL is a declarative language over a cost-based engine, and Postgres "
        "is the most capable open-source implementation of it. This course "
        "moves from SELECT and JOIN through aggregation and window functions "
        "into schema design, indexing, EXPLAIN, transactions and the Postgres "
        "features worth reaching for."
    ),
    "objectives": [
        "Write correct SELECT queries with filtering, joins and aggregation",
        "Use window functions for ranking and running totals",
        "Design a normalised schema with the right constraints",
        "Choose and verify indexes using EXPLAIN ANALYZE",
        "Reason about transactions, isolation and locking",
        "Use JSONB, CTEs, upserts and other Postgres-specific tools",
    ],
    "resources": [
        {"kind": "doc", "title": "PostgreSQL documentation", "url": "https://www.postgresql.org/docs/"},
        {"kind": "doc", "title": "PostgreSQL Tutorial", "url": "https://www.postgresqltutorial.com/"},
        {"kind": "doc", "title": "W3Schools — SQL tutorial", "url": "https://www.w3schools.com/sql/"},
    ],
    "chapters": [
        {
            "id": "select",
            "title": "SELECT, filtering and ordering",
            "topic": "Querying",
            "summary": "The clause order the engine actually uses, and the NULL rules that catch everyone.",
            "minutes": 15,
            "body": """
## Logical evaluation order

You write it in one order; the engine evaluates it in another:

```
FROM → WHERE → GROUP BY → HAVING → SELECT → DISTINCT → ORDER BY → LIMIT
```

That order explains two constant beginner questions: why you cannot use a
`SELECT` alias in `WHERE` (the alias does not exist yet), and why you *can* use
it in `ORDER BY` (by then it does).

```sql
select c.id,
       c.title,
       c.minutes / 60.0 as hours
from   courses c
where  c.difficulty = 'beginner'      -- cannot say `hours` here
  and  c.published_at is not null
order  by hours desc                  -- but can here
limit  20;
```

## NULL is not a value

`NULL` means unknown, so any comparison with it is unknown — not false:

```sql
where price = null        -- never true, not even for NULL rows
where price is null       -- correct
where price <> 100        -- excludes NULL rows too!
where price is distinct from 100    -- includes them
coalesce(price, 0)        -- substitute a default
```

The `<> ` case is the one that quietly loses rows in production reports.

## Filtering

```sql
where status in ('draft', 'review')
  and created_at >= now() - interval '30 days'
  and title ilike '%rag%'            -- case-insensitive LIKE (Postgres)
  and tags && array['ai','llm']      -- array overlap
```

## Ordering and NULLs

```sql
order by published_at desc nulls last, title asc;
```

Postgres sorts NULLs first for `DESC` by default. If you care — and in a UI you
do — say so explicitly.

## DISTINCT ON

A Postgres speciality: one row per group, chosen by the ordering.

```sql
select distinct on (course_id) course_id, score, taken_at
from   exam_attempts
order  by course_id, taken_at desc;      -- latest attempt per course
```

The leading `ORDER BY` columns must match the `DISTINCT ON` columns.
""",
            "concepts": [
                ("Logical query order", "FROM → WHERE → GROUP BY → HAVING → SELECT → ORDER BY → LIMIT."),
                ("Three-valued logic", "Comparisons with NULL yield unknown, not false."),
                ("DISTINCT ON", "Postgres syntax returning the first row per group under a given ordering."),
            ],
            "takeaways": [
                "WHERE runs before SELECT, so SELECT aliases are not visible in it",
                "`= NULL` is never true — use `IS NULL` or `IS DISTINCT FROM`",
                "`<> value` silently excludes NULL rows",
                "Specify NULLS FIRST/LAST when ordering matters to the UI",
            ],
            "resources": [
                {"kind": "doc", "title": "PostgreSQL — SELECT", "url": "https://www.postgresql.org/docs/current/sql-select.html"},
                {"kind": "doc", "title": "W3Schools — SQL WHERE", "url": "https://www.w3schools.com/sql/sql_where.asp"},
            ],
            "video": {"query": "postgresql sql select where tutorial beginners", "title": "SQL basics"},
        },
        {
            "id": "joins",
            "title": "Joins and set operations",
            "topic": "Joins",
            "summary": "Combining tables without losing or duplicating rows.",
            "minutes": 16,
            "body": """
## The four you need

```sql
-- INNER: only matching pairs
select u.name, e.score
from   users u
join   exam_attempts e on e.user_id = u.id;

-- LEFT: every user, with NULLs where there is no attempt
select u.name, e.score
from   users u
left join exam_attempts e on e.user_id = u.id;

-- FULL: every row from both sides
-- CROSS: every combination (deliberate, or a missing join condition)
```

`LEFT JOIN` is how you ask "everyone, and their attempts if any". A very common
bug turns it back into an inner join:

```sql
left join exam_attempts e on e.user_id = u.id
where  e.score > 80          -- NULL > 80 is unknown → users without attempts vanish

left join exam_attempts e on e.user_id = u.id and e.score > 80   -- correct
```

Predicates on the *right* table of a LEFT JOIN belong in the `ON` clause.

## Semi and anti joins

```sql
-- rows that HAVE a match, without duplicating
select * from courses c
where exists (select 1 from enrolments e where e.course_id = c.id);

-- rows that have NO match
select * from courses c
where not exists (select 1 from enrolments e where e.course_id = c.id);
```

Prefer `EXISTS` to `IN (subquery)` when the subquery may return NULLs —
`NOT IN` with a NULL in the list returns no rows at all, which is correct in
SQL logic and never what was intended.

## Fan-out

Joining a one-to-many relationship multiplies rows. Aggregate *after* joining,
or aggregate in a subquery first:

```sql
select c.id, c.title, count(e.id) as attempts, avg(e.score)::numeric(5,2) as avg_score
from   courses c
left join exam_attempts e on e.course_id = c.id
group  by c.id, c.title;
```

Joining two one-to-many tables at once multiplies both — that is the classic
"my sums are double" bug. Aggregate each in its own subquery and join the
results.

## Set operations

```sql
select id from a union     select id from b;   -- distinct
select id from a union all select id from b;   -- keeps duplicates, faster
select id from a intersect select id from b;
select id from a except    select id from b;
```
""",
            "concepts": [
                ("Inner vs outer join", "Whether unmatched rows from a side are preserved."),
                ("Anti join", "Selecting rows with no match, typically via NOT EXISTS."),
                ("Fan-out", "Row multiplication caused by joining a one-to-many relationship."),
            ],
            "takeaways": [
                "A WHERE predicate on the right table turns a LEFT JOIN into an inner join",
                "Use EXISTS/NOT EXISTS rather than IN/NOT IN when NULLs are possible",
                "Joining two one-to-many tables double-counts — aggregate separately",
                "UNION ALL is cheaper than UNION when duplicates are impossible or fine",
            ],
            "resources": [
                {"kind": "doc", "title": "PostgreSQL — Joins", "url": "https://www.postgresql.org/docs/current/tutorial-join.html"},
                {"kind": "doc", "title": "PostgreSQL Tutorial — Joins", "url": "https://www.postgresqltutorial.com/postgresql-tutorial/postgresql-joins/"},
            ],
            "video": {"query": "sql joins explained inner left outer tutorial", "title": "SQL joins"},
        },
        {
            "id": "aggregation",
            "title": "Aggregation, grouping and window functions",
            "topic": "Aggregation",
            "summary": "Collapsing rows, and computing across them without collapsing.",
            "minutes": 17,
            "body": """
## GROUP BY

```sql
select difficulty,
       count(*)                     as courses,
       round(avg(minutes), 1)       as avg_minutes,
       count(*) filter (where published) as live
from   courses
group  by difficulty
having count(*) > 3
order  by courses desc;
```

`WHERE` filters rows before grouping; `HAVING` filters groups after. The
`FILTER` clause gives you conditional aggregates without `CASE WHEN` gymnastics.

Every non-aggregated column in `SELECT` must appear in `GROUP BY` — unless it is
functionally dependent on the grouped primary key, which Postgres allows.

## Window functions

An aggregate collapses rows. A **window function** computes across a set of rows
and keeps every row:

```sql
select user_id,
       course_id,
       score,
       row_number() over (partition by user_id order by taken_at desc) as recency,
       avg(score)  over (partition by course_id)                        as course_avg,
       score - lag(score) over (partition by user_id, course_id
                                order by taken_at)                      as delta
from   exam_attempts;
```

- `PARTITION BY` — the group this row is measured within.
- `ORDER BY` inside `OVER` — the ordering for rank/lag/running totals.

## The ranking family

- `row_number()` — 1, 2, 3, 4 — always unique.
- `rank()` — 1, 2, 2, 4 — ties share, then skip.
- `dense_rank()` — 1, 2, 2, 3 — ties share, no gap.

"Latest attempt per user per course" is the canonical use:

```sql
select * from (
  select *, row_number() over (partition by user_id, course_id
                               order by taken_at desc) as rn
  from exam_attempts
) t
where rn = 1;
```

## Frames

```sql
sum(score) over (partition by user_id order by taken_at
                 rows between unbounded preceding and current row) as running_total
```

Without a frame clause but with `ORDER BY`, the default frame is
`RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW` — which is why a "plain"
`sum() over (order by ...)` gives a running total rather than the grand total.
""",
            "concepts": [
                ("HAVING", "A filter applied to groups after aggregation."),
                ("Window function", "A calculation across a set of rows that does not collapse them."),
                ("PARTITION BY", "The subgroup a window function is computed within."),
                ("Frame", "The subset of the partition a window function sees for the current row."),
            ],
            "takeaways": [
                "WHERE filters rows, HAVING filters groups",
                "`FILTER (WHERE ...)` gives clean conditional aggregates",
                "Window functions keep every row — row_number() picks the latest per group",
                "With ORDER BY and no frame, an OVER aggregate is a running total",
            ],
            "resources": [
                {"kind": "doc", "title": "PostgreSQL — Window functions", "url": "https://www.postgresql.org/docs/current/tutorial-window.html"},
                {"kind": "doc", "title": "PostgreSQL Tutorial — Window functions", "url": "https://www.postgresqltutorial.com/postgresql-window-function/"},
            ],
            "video": {"query": "sql window functions explained partition by row_number", "title": "Window functions"},
        },
        {
            "id": "schema",
            "title": "Schema design & constraints",
            "topic": "Schema Design",
            "summary": "Normalisation, keys and letting the database enforce the rules.",
            "minutes": 16,
            "body": """
## Normalise first

- **1NF** — atomic values; no comma-separated lists in a column.
- **2NF** — no partial dependency on part of a composite key.
- **3NF** — no non-key column depending on another non-key column.

In practice: one fact in one place. Denormalise later, deliberately, with a
measured reason — and know that you are taking on the job of keeping the copies
consistent.

## Constraints are correctness

```sql
create table enrolments (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references users (id) on delete cascade,
  course_id    text not null references courses (id) on delete restrict,
  progress     smallint not null default 0
                 check (progress between 0 and 100),
  created_at   timestamptz not null default now(),
  unique (user_id, course_id)
);
```

Every constraint here removes a class of bug that application code would
otherwise have to prevent on every write path — and application code is where
the race conditions live. `UNIQUE (user_id, course_id)` is the only thing that
actually prevents a double enrolment from two concurrent requests.

Choose `ON DELETE` deliberately: `CASCADE` for owned children, `RESTRICT` for
references that should block deletion, `SET NULL` for optional links.

## Types worth knowing

| Use | Type |
|---|---|
| Money | `numeric(12,2)` — never `float` |
| Timestamps | `timestamptz`, always |
| Identifiers | `uuid`, or `bigint generated always as identity` |
| Free text | `text` — `varchar(n)` buys nothing in Postgres |
| Fixed set | an `enum` type, or `text` + a CHECK |
| Semi-structured | `jsonb` |

`timestamp` without a time zone stores a wall clock with no meaning across
regions. Use `timestamptz` and store UTC.

## Naming and migrations

Plural table names, singular columns, `snake_case`, `*_id` for keys. Pick one
convention and never mix.

Migrations are versioned, forward-only, and reviewed like code. On a live table
that means: add nullable, backfill in batches, then add the constraint —
`ALTER TABLE ... ADD COLUMN NOT NULL DEFAULT` on a huge table used to rewrite
it entirely, and locking a hot table for minutes is an outage.
""",
            "concepts": [
                ("Normalisation", "Structuring tables so each fact is stored once."),
                ("Foreign key action", "What happens to children when a parent row is deleted."),
                ("CHECK constraint", "A row-level invariant enforced by the database."),
                ("timestamptz", "A timestamp with time zone — the only safe choice for instants."),
            ],
            "takeaways": [
                "Constraints prevent bugs application code cannot, because of concurrency",
                "Use numeric for money and timestamptz for time",
                "A UNIQUE index is the only real defence against duplicate concurrent inserts",
                "On live tables: add nullable, backfill, then constrain",
            ],
            "resources": [
                {"kind": "doc", "title": "PostgreSQL — Data types", "url": "https://www.postgresql.org/docs/current/datatype.html"},
                {"kind": "doc", "title": "PostgreSQL — Constraints", "url": "https://www.postgresql.org/docs/current/ddl-constraints.html"},
            ],
            "video": {"query": "database schema design normalization constraints tutorial", "title": "Schema design"},
        },
        {
            "id": "indexes",
            "title": "Indexes & EXPLAIN",
            "topic": "Indexing",
            "summary": "What an index can serve, and how to check rather than guess.",
            "minutes": 18,
            "body": """
## The default: B-tree

Serves equality, ranges, sorting and prefix `LIKE 'abc%'`.

```sql
create index on exam_attempts (user_id, course_id, taken_at desc);
```

**Column order matters.** A composite index on `(a, b, c)` can serve queries
filtering on `a`, on `a, b`, or on `a, b, c` — but not on `b` alone. Put
equality columns first and the range/sort column last.

## Other index types

- **GIN** — `jsonb` containment, arrays, full-text search.
- **GiST** — geometric and range types.
- **BRIN** — huge, naturally ordered tables (append-only logs).
- **HNSW / IVFFlat** — vector similarity, via `pgvector`.
- **Partial** — `create index ... where status = 'active'`: smaller and faster
  when most queries only care about a slice.
- **Expression** — `create index on users (lower(email))`, required for
  `where lower(email) = $1` to use an index at all.

## What kills an index

```sql
where date(created_at) = '2026-01-01'          -- function on the column: no index
where created_at >= '2026-01-01'
  and created_at <  '2026-01-02'               -- sargable: index used

where email ilike '%@example.com'              -- leading wildcard: no B-tree
where status::text = '1'                       -- a cast can also prevent it
```

Wrapping the indexed column in a function or a cast prevents the index from
being used — unless you built an index on that exact expression.

## EXPLAIN ANALYZE

```sql
explain (analyze, buffers)
select * from exam_attempts where user_id = $1 order by taken_at desc limit 10;
```

Read it inside-out. What to look for:

- **Seq Scan** on a large table in a selective query → missing index.
- **rows** estimated vs actual differing by orders of magnitude → stale
  statistics; run `ANALYZE`.
- **Nested Loop** over a large outer side → often a bad plan from bad estimates.
- Sorts spilling to disk → raise `work_mem` or add an index that provides the
  order.

A Seq Scan is not automatically wrong: on a small table, or when returning most
rows, it is the correct plan.

## The cost of indexes

Every index slows `INSERT`, `UPDATE` and `DELETE`, and consumes storage.
Unused indexes are pure overhead — `pg_stat_user_indexes` shows which ones have
never been scanned.
""",
            "concepts": [
                ("B-tree index", "The default index, serving equality, ranges and ordering."),
                ("Sargable predicate", "A condition an index can be used to satisfy."),
                ("Partial index", "An index over a filtered subset of rows."),
                ("EXPLAIN ANALYZE", "Runs the query and reports the actual plan, timings and row counts."),
            ],
            "takeaways": [
                "Composite index order matters: equality columns first, range/sort last",
                "A function or cast on the indexed column disables the index",
                "Read EXPLAIN ANALYZE for Seq Scans and estimate-vs-actual row gaps",
                "Indexes cost write throughput — drop the ones nothing scans",
            ],
            "resources": [
                {"kind": "doc", "title": "PostgreSQL — Indexes", "url": "https://www.postgresql.org/docs/current/indexes.html"},
                {"kind": "doc", "title": "PostgreSQL — Using EXPLAIN", "url": "https://www.postgresql.org/docs/current/using-explain.html"},
            ],
            "video": {"query": "postgresql indexes explain analyze query performance tutorial", "title": "Indexes and EXPLAIN"},
        },
        {
            "id": "transactions",
            "title": "Transactions, isolation and locking",
            "topic": "Transactions",
            "summary": "ACID in practice: what your isolation level actually prevents.",
            "minutes": 17,
            "body": """
## ACID

**Atomicity** — all or nothing. **Consistency** — constraints hold at commit.
**Isolation** — concurrent transactions do not corrupt each other.
**Durability** — a committed transaction survives a crash.

```sql
begin;
update accounts set balance = balance - 100 where id = 1;
update accounts set balance = balance + 100 where id = 2;
commit;                -- or rollback;
```

## Isolation levels

| Level | Dirty read | Non-repeatable read | Phantom |
|---|---|---|---|
| Read Committed (Postgres default) | no | possible | possible |
| Repeatable Read | no | no | no* |
| Serializable | no | no | no |

Postgres never allows dirty reads. Its Repeatable Read prevents phantoms too,
via snapshot isolation, but it can abort your transaction with a serialization
failure — so any application using it **must retry**.

## Lost updates

The read-modify-write race:

```sql
-- two sessions do this concurrently; one update is lost
select balance from accounts where id = 1;    -- both read 500
update accounts set balance = 400 where id = 1;
```

Three fixes:

```sql
-- 1. do it in one statement (atomic)
update accounts set balance = balance - 100 where id = 1;

-- 2. pessimistic lock
select balance from accounts where id = 1 for update;

-- 3. optimistic lock
update accounts set balance = $1, version = version + 1
where id = 1 and version = $2;      -- 0 rows updated → someone else won
```

## Deadlocks

Two transactions each hold what the other needs. Postgres detects and kills one.
The fix is convention: **always acquire locks in the same order**, and keep
transactions short.

## MVCC and vacuum

Postgres never updates a row in place — it writes a new version and leaves the
old one until no transaction can see it. Consequences:

- Readers never block writers and writers never block readers.
- Dead tuples accumulate; `autovacuum` reclaims them.
- A long-running transaction prevents vacuuming *everything* newer, causing
  table bloat. The idle-in-transaction connection is a genuine production
  hazard.

## Practical rules

- Keep transactions short; never wait for a user or an HTTP call inside one.
- Set `statement_timeout` and `idle_in_transaction_session_timeout`.
- Retry on serialization and deadlock errors — they are expected, not
  exceptional.
""",
            "concepts": [
                ("Isolation level", "How much concurrent interference a transaction tolerates."),
                ("Lost update", "Two read-modify-write cycles overlapping so one is overwritten."),
                ("Optimistic locking", "Detecting conflict with a version column instead of holding a lock."),
                ("MVCC", "Multi-version concurrency control: readers see a snapshot and never block writers."),
            ],
            "takeaways": [
                "Postgres defaults to Read Committed — non-repeatable reads are possible",
                "Prevent lost updates with a single atomic statement, SELECT FOR UPDATE, or a version column",
                "Acquire locks in a consistent order and keep transactions short",
                "Long idle transactions block vacuum and bloat tables",
            ],
            "resources": [
                {"kind": "doc", "title": "PostgreSQL — Transaction isolation", "url": "https://www.postgresql.org/docs/current/transaction-iso.html"},
                {"kind": "doc", "title": "PostgreSQL — Explicit locking", "url": "https://www.postgresql.org/docs/current/explicit-locking.html"},
            ],
            "video": {"query": "database transactions isolation levels mvcc explained", "title": "Transactions and isolation"},
        },
        {
            "id": "advanced",
            "title": "CTEs, JSONB and Postgres extras",
            "topic": "Advanced SQL",
            "summary": "The features that replace application code you were about to write.",
            "minutes": 16,
            "body": """
## CTEs

```sql
with recent as (
  select * from exam_attempts where taken_at > now() - interval '30 days'
), ranked as (
  select *, row_number() over (partition by user_id order by score desc) as rn
  from recent
)
select * from ranked where rn = 1;
```

CTEs name intermediate results and make long queries readable. Since Postgres
12 they are inlined by default; add `MATERIALIZED` to force evaluation once
when the CTE is expensive and used repeatedly.

**Recursive** CTEs walk trees:

```sql
with recursive tree as (
  select id, parent_id, title, 1 as depth from nodes where parent_id is null
  union all
  select n.id, n.parent_id, n.title, t.depth + 1
  from nodes n join tree t on n.parent_id = t.id
)
select * from tree order by depth;
```

## Upsert

```sql
insert into course_progress (user_id, course_id, chapter_id, completed)
values ($1, $2, $3, true)
on conflict (user_id, course_id, chapter_id)
do update set completed   = excluded.completed,
              updated_at  = now()
returning *;
```

One statement, no race, no round trip to check first. `RETURNING` gives you the
resulting row without a second query — one of Postgres's best features.

## JSONB

```sql
alter table events add column payload jsonb not null default '{}';

select payload->>'type'          as type,      -- ->> text, -> jsonb
       payload#>>'{user,id}'     as user_id
from   events
where  payload @> '{"type": "click"}'          -- containment, uses GIN
  and  payload ? 'session_id';                 -- key exists

create index on events using gin (payload jsonb_path_ops);
```

Use `jsonb` for genuinely variable data — third-party payloads, user-defined
fields. Do not use it as an excuse to avoid columns: anything you filter, join
or constrain on should be a real column with a real type.

## Full-text search

```sql
alter table courses add column search tsvector
  generated always as (to_tsvector('english', title || ' ' || description)) stored;
create index on courses using gin (search);

select * from courses
where  search @@ plainto_tsquery('english', 'retrieval augmented')
order  by ts_rank(search, plainto_tsquery('english', 'retrieval augmented')) desc;
```

For most applications this removes the need for a separate search service.

## Extensions worth knowing

`pgcrypto` (uuid/hashing), `pg_trgm` (fuzzy matching and index support for
`LIKE '%x%'`), `pgvector` (embeddings), `pg_stat_statements` (find the slow
queries you actually run).
""",
            "concepts": [
                ("CTE", "A named subquery defined with WITH, improving readability and enabling recursion."),
                ("Upsert", "INSERT ... ON CONFLICT DO UPDATE — insert or update atomically."),
                ("JSONB", "Binary JSON storage supporting containment queries and GIN indexes."),
                ("tsvector", "A preprocessed document representation for full-text search."),
            ],
            "takeaways": [
                "CTEs make long queries readable; MATERIALIZED forces single evaluation",
                "ON CONFLICT DO UPDATE is the race-free upsert; RETURNING avoids a second query",
                "Use jsonb for variable data, real columns for anything you filter or join on",
                "Postgres full-text search replaces a separate search service for most apps",
            ],
            "resources": [
                {"kind": "doc", "title": "PostgreSQL — WITH queries", "url": "https://www.postgresql.org/docs/current/queries-with.html"},
                {"kind": "doc", "title": "PostgreSQL — JSON types", "url": "https://www.postgresql.org/docs/current/datatype-json.html"},
            ],
            "video": {"query": "postgresql jsonb cte upsert advanced tutorial", "title": "Advanced PostgreSQL"},
        },
        {
            "id": "operations",
            "title": "Performance & operations",
            "topic": "Operations",
            "summary": "Connections, N+1, backups and the numbers to watch.",
            "minutes": 15,
            "body": """
## Connection pooling

A Postgres connection is a process. A few hundred is a problem; a few thousand
is an outage. Pool in the application (SQLAlchemy, HikariCP) and, in
serverless environments, put PgBouncer in front — many short-lived functions
each opening a connection is the classic way to exhaust a database.

Note the constraint: in PgBouncer's transaction mode, prepared statements and
session-level settings do not survive between transactions.

## N+1

```python
courses = await db.fetch("select * from courses")           # 1 query
for course in courses:
    course.chapters = await db.fetch(                        # N queries
        "select * from chapters where course_id = $1", course.id)
```

Fix with a join, or one batched second query:

```sql
select * from chapters where course_id = any($1::text[]);
```

ORMs make this invisible — turn on query logging in development at least once
and count.

## Query hygiene

- `select *` in application code ships columns you do not need and breaks when
  the schema changes. Name them.
- Always parameterise. String-formatted SQL is how SQL injection happens; a
  parameter is never parsed as SQL.
- Keep `LIMIT` on anything user-facing.
- Batch writes: one `INSERT` with 1,000 rows beats 1,000 inserts by orders of
  magnitude.

## What to watch

- `pg_stat_statements` — total time by query, the only ranking that matters.
- Cache hit ratio (aim >99%), index usage, dead tuple counts.
- Long-running and idle-in-transaction sessions in `pg_stat_activity`.
- Replication lag if you read from replicas.

## Backups

A backup you have never restored is a hypothesis. Run point-in-time recovery
against a real restore at least once, and know your RPO and RTO before an
incident rather than during one.

## Safe migrations

- Add columns nullable; backfill in batches; then add the constraint.
- Create indexes with `CREATE INDEX CONCURRENTLY` — the plain form locks writes.
- Deploy in expand/contract phases: add the new shape, migrate code, then remove
  the old one. Never in one release.
""",
            "concepts": [
                ("Connection pool", "A bounded, reused set of database connections."),
                ("N+1 query", "One query per row of a previous result, instead of one batched query."),
                ("CREATE INDEX CONCURRENTLY", "Building an index without blocking writes."),
                ("Expand/contract", "Migrating in additive then subtractive phases so deploys stay safe."),
            ],
            "takeaways": [
                "Pool connections; a Postgres connection is a process, not a socket",
                "N+1 is the most common ORM performance bug — batch or join",
                "Always parameterise; never format SQL strings",
                "CREATE INDEX CONCURRENTLY on live tables, and migrate expand-then-contract",
            ],
            "resources": [
                {"kind": "doc", "title": "PostgreSQL — Performance tips", "url": "https://www.postgresql.org/docs/current/performance-tips.html"},
                {"kind": "doc", "title": "PostgreSQL — Backup and restore", "url": "https://www.postgresql.org/docs/current/backup.html"},
            ],
            "video": {"query": "postgresql performance tuning connection pooling n+1 tutorial", "title": "Postgres operations"},
            "notes": """
First move on any "the database is slow" report: `pg_stat_statements` ordered by
total execution time. It is almost never the query anyone suspected — it is a
fast query being run 40,000 times.
""",
        },
    ],
    "exams": [
        {
            "id": "pg-exam-1",
            "title": "SQL — Querying Assessment",
            "description": "Covers chapters 1–4: SELECT, joins, aggregation, schema design.",
            "chapter_ids": ["select", "joins", "aggregation", "schema"],
            "questions": [
                {
                    "type": "code", "topic": "Querying", "chapter_id": "select", "language": "sql",
                    "prompt": "Why does this return no rows even though NULL prices exist?",
                    "code": "select * from products where price = null;",
                    "options": [
                        "NULL is not a valid literal",
                        "Comparison with NULL yields unknown, never true — use `is null`",
                        "The column must be indexed",
                        "Postgres requires quotes around null",
                    ],
                    "answer": 1,
                    "explanation": "SQL uses three-valued logic. Only `IS NULL` / `IS NOT NULL` test for it.",
                },
                {
                    "type": "mcq", "topic": "Querying", "chapter_id": "select",
                    "prompt": "Why can you use a SELECT alias in ORDER BY but not in WHERE?",
                    "options": [
                        "ORDER BY is optional",
                        "WHERE is evaluated before SELECT, so the alias does not exist yet",
                        "Aliases are only valid in subqueries",
                        "It is a Postgres-specific restriction",
                    ],
                    "answer": 1,
                    "explanation": "Logical order: FROM → WHERE → GROUP BY → HAVING → SELECT → ORDER BY.",
                },
                {
                    "type": "scenario", "topic": "Joins", "chapter_id": "joins",
                    "prompt": "A LEFT JOIN plus `where e.score > 80` returns only users who have attempts. Why?",
                    "options": [
                        "LEFT JOIN is not supported with WHERE",
                        "NULL > 80 is unknown, so unmatched rows are filtered out — the predicate belongs in ON",
                        "The join columns need an index",
                        "score must be cast to integer",
                    ],
                    "answer": 1,
                    "explanation": "A WHERE predicate on the right table turns a LEFT JOIN into an inner join. Move it into the ON clause.",
                },
                {
                    "type": "mcq", "topic": "Joins", "chapter_id": "joins",
                    "prompt": "Why prefer `NOT EXISTS` over `NOT IN (subquery)`?",
                    "options": [
                        "NOT IN is deprecated",
                        "If the subquery yields any NULL, NOT IN returns no rows at all",
                        "NOT EXISTS supports more columns",
                        "NOT IN cannot use indexes",
                    ],
                    "answer": 1,
                    "explanation": "A single NULL makes every NOT IN comparison unknown. NOT EXISTS has no such trap.",
                },
                {
                    "type": "truefalse", "topic": "Joins", "chapter_id": "joins",
                    "prompt": "Joining two separate one-to-many tables in a single query can double-count aggregates.",
                    "options": ["True", "False"],
                    "answer": 0,
                    "explanation": "True. The two fan-outs multiply. Aggregate each in its own subquery and join the results.",
                },
                {
                    "type": "mcq", "topic": "Aggregation", "chapter_id": "aggregation",
                    "prompt": "What is the difference between WHERE and HAVING?",
                    "options": [
                        "None — they are synonyms",
                        "WHERE filters rows before grouping; HAVING filters groups after aggregation",
                        "HAVING is faster",
                        "WHERE only works on indexed columns",
                    ],
                    "answer": 1,
                    "explanation": "That ordering is why an aggregate can appear in HAVING but not in WHERE.",
                },
                {
                    "type": "code", "topic": "Aggregation", "chapter_id": "aggregation", "language": "sql",
                    "prompt": "What does this subquery + filter produce?",
                    "code": "select * from (\n  select *, row_number() over (partition by user_id order by taken_at desc) rn\n  from exam_attempts\n) t where rn = 1;",
                    "options": [
                        "The highest score per user",
                        "The most recent attempt per user",
                        "The first attempt per user",
                        "One random row per user",
                    ],
                    "answer": 1,
                    "explanation": "row_number() over a descending taken_at gives 1 to the latest attempt in each partition.",
                },
                {
                    "type": "mcq", "topic": "Aggregation", "chapter_id": "aggregation",
                    "prompt": "Which ranking function produces 1, 2, 2, 3 for a tie?",
                    "options": ["row_number()", "rank()", "dense_rank()", "ntile(4)"],
                    "answer": 2,
                    "explanation": "dense_rank() shares ranks without leaving a gap; rank() would give 1, 2, 2, 4.",
                },
                {
                    "type": "mcq", "topic": "Schema Design", "chapter_id": "schema",
                    "prompt": "Which column type should store a monetary amount?",
                    "options": ["float8", "numeric(12,2)", "real", "money as text"],
                    "answer": 1,
                    "explanation": "Binary floating point cannot represent 0.10 exactly. numeric is exact decimal arithmetic.",
                },
                {
                    "type": "scenario", "topic": "Schema Design", "chapter_id": "schema",
                    "prompt": "Two concurrent requests both check 'is this user enrolled?', both see no, and both insert. What prevents the duplicate?",
                    "options": [
                        "Wrapping the check and insert in application-level locking",
                        "A UNIQUE constraint on (user_id, course_id)",
                        "Adding an index on user_id",
                        "Retrying the request",
                    ],
                    "answer": 1,
                    "explanation": "Check-then-insert is inherently racy. Only the database constraint makes the duplicate impossible.",
                },
            ],
        },
        {
            "id": "pg-exam-2",
            "title": "PostgreSQL — Performance & Operations",
            "description": "Covers chapters 5–8: indexing, transactions, advanced SQL, operations.",
            "chapter_ids": ["indexes", "transactions", "advanced", "operations"],
            "questions": [
                {
                    "type": "code", "topic": "Indexing", "chapter_id": "indexes", "language": "sql",
                    "prompt": "Why does this query ignore the index on created_at?",
                    "code": "select * from events where date(created_at) = '2026-01-01';",
                    "options": [
                        "The index is on the wrong column",
                        "Wrapping the column in a function makes the predicate non-sargable",
                        "Dates cannot be indexed",
                        "The table is too small",
                    ],
                    "answer": 1,
                    "explanation": "Rewrite as a half-open range, or create an index on the expression `date(created_at)`.",
                },
                {
                    "type": "mcq", "topic": "Indexing", "chapter_id": "indexes",
                    "prompt": "A composite index on (a, b, c) can serve which query?",
                    "options": [
                        "WHERE b = $1",
                        "WHERE a = $1 AND b = $2",
                        "WHERE c = $1",
                        "WHERE b = $1 AND c = $2",
                    ],
                    "answer": 1,
                    "explanation": "A B-tree can use a leading prefix of the columns. Filtering on b alone cannot use it.",
                },
                {
                    "type": "scenario", "topic": "Indexing", "chapter_id": "indexes",
                    "prompt": "EXPLAIN ANALYZE shows estimated rows 12, actual rows 480,000, and a Nested Loop. What is the first thing to check?",
                    "options": [
                        "Whether the disk is full",
                        "Stale planner statistics — run ANALYZE on the table",
                        "The client library version",
                        "Whether autovacuum is disabled globally",
                    ],
                    "answer": 1,
                    "explanation": "A huge estimate/actual gap means the planner is working from bad statistics, which produces bad plan shapes.",
                },
                {
                    "type": "mcq", "topic": "Transactions", "chapter_id": "transactions",
                    "prompt": "What is PostgreSQL's default isolation level?",
                    "options": ["Read Uncommitted", "Read Committed", "Repeatable Read", "Serializable"],
                    "answer": 1,
                    "explanation": "Read Committed: each statement sees a fresh snapshot, so non-repeatable reads are possible within a transaction.",
                },
                {
                    "type": "code", "topic": "Transactions", "chapter_id": "transactions", "language": "sql",
                    "prompt": "Which statement is immune to the lost-update race?",
                    "code": "-- A\nselect balance from accounts where id = 1;\nupdate accounts set balance = 400 where id = 1;\n\n-- B\nupdate accounts set balance = balance - 100 where id = 1;",
                    "options": ["A", "B", "Both", "Neither"],
                    "answer": 1,
                    "explanation": "B reads and writes atomically in one statement. A has a window between the read and the write where another session can win.",
                },
                {
                    "type": "truefalse", "topic": "Transactions", "chapter_id": "transactions",
                    "prompt": "A long-running idle transaction can prevent vacuum from reclaiming dead rows across the database.",
                    "options": ["True", "False"],
                    "answer": 0,
                    "explanation": "True. MVCC must keep any version that transaction could still see, so bloat accumulates.",
                },
                {
                    "type": "code", "topic": "Advanced SQL", "chapter_id": "advanced", "language": "sql",
                    "prompt": "What does this statement do if the row already exists?",
                    "code": "insert into progress (user_id, chapter_id, completed)\nvalues ($1, $2, true)\non conflict (user_id, chapter_id)\ndo update set completed = excluded.completed\nreturning *;",
                    "options": [
                        "Raises a unique violation",
                        "Updates the existing row atomically and returns it",
                        "Silently does nothing",
                        "Deletes and re-inserts",
                    ],
                    "answer": 1,
                    "explanation": "This is the race-free upsert. `excluded` refers to the row proposed for insertion, and RETURNING avoids a second query.",
                },
                {
                    "type": "mcq", "topic": "Advanced SQL", "chapter_id": "advanced",
                    "prompt": "When is `jsonb` the wrong choice?",
                    "options": [
                        "For third-party webhook payloads",
                        "For fields you filter, join or constrain on — those should be real columns",
                        "For user-defined custom fields",
                        "For rarely-read audit detail",
                    ],
                    "answer": 1,
                    "explanation": "jsonb is for genuinely variable data. Anything with a fixed meaning deserves a typed, constrained column.",
                },
                {
                    "type": "scenario", "topic": "Operations", "chapter_id": "operations",
                    "prompt": "Loading a list page issues 1 query for 50 courses and then 50 more for their chapters. What is this and how is it fixed?",
                    "options": [
                        "A deadlock — add retries",
                        "An N+1 query — replace with a join or one batched `where course_id = any($1)`",
                        "A connection leak — increase the pool",
                        "Index bloat — reindex",
                    ],
                    "answer": 1,
                    "explanation": "The classic ORM performance bug. One batched second query replaces N round trips.",
                },
                {
                    "type": "mcq", "topic": "Operations", "chapter_id": "operations",
                    "prompt": "How should an index be added to a large, live table?",
                    "options": [
                        "CREATE INDEX during peak hours to warm the cache",
                        "CREATE INDEX CONCURRENTLY, which does not block writes",
                        "Drop the table and recreate it",
                        "Add it inside a transaction with a lock timeout",
                    ],
                    "answer": 1,
                    "explanation": "A plain CREATE INDEX takes a lock that blocks writes for the duration of the build.",
                },
            ],
        },
    ],
}
