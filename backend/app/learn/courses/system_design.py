"""System Design."""

from __future__ import annotations

COURSE = {
    "id": "system-design",
    "title": "System Design",
    "short_title": "System Design",
    "subtitle": "Scaling, trade-offs and the vocabulary of distributed systems",
    "difficulty": "advanced",
    "tags": ["Architecture", "Backend"],
    "description": (
        "System design is the discipline of choosing which problems to have. "
        "This course covers the building blocks — load balancing, caching, "
        "databases and sharding, queues — and the reasoning that connects them: "
        "CAP, consistency models, failure handling, and how to actually work "
        "through a design question."
    ),
    "objectives": [
        "Estimate load, storage and bandwidth before choosing an architecture",
        "Apply caching correctly, including invalidation and stampede control",
        "Choose replication, partitioning and consistency models deliberately",
        "Design asynchronous flows with queues and idempotent consumers",
        "Build for failure with timeouts, retries, backoff and circuit breakers",
        "Structure an answer to an open-ended design question",
    ],
    "resources": [
        {"kind": "doc", "title": "System Design Primer", "url": "https://github.com/donnemartin/system-design-primer"},
        {"kind": "article", "title": "AWS — Architecture best practices", "url": "https://aws.amazon.com/architecture/well-architected/"},
    ],
    "chapters": [
        {
            "id": "fundamentals",
            "title": "Fundamentals & estimation",
            "topic": "Fundamentals",
            "summary": "Requirements, back-of-envelope numbers, and the latency figures worth memorising.",
            "minutes": 16,
            "body": """
## Start with requirements

**Functional** — what it does. **Non-functional** — how well: expected users,
read/write ratio, latency target, availability target, consistency needs,
retention.

Almost every bad design starts by choosing a technology before knowing the
numbers. Ask: how many users, how often, how big, how fast, how correct.

## Back-of-the-envelope

```
10M daily active users
× 20 requests/day        = 200M requests/day
÷ 86,400 s               ≈ 2,300 requests/second average
× 3 (peak factor)        ≈ 7,000 rps peak

Each record 2 KB × 200M/day = 400 GB/day  → ~146 TB/year before compression
```

The goal is an order of magnitude, not a number. 7,000 rps and 700,000 rps are
different architectures; 7,000 and 9,000 are the same one.

## Latency numbers to know

| Operation | Order |
|---|---|
| L1 cache | ~1 ns |
| Main memory | ~100 ns |
| SSD random read | ~100 µs |
| Datacentre round trip | ~0.5 ms |
| Disk seek (HDD) | ~10 ms |
| Cross-continent round trip | ~150 ms |

The consequence that matters: memory is roughly 1,000× faster than SSD, and a
cross-region hop costs more than a thousand local queries. Geography is a design
constraint, not a deployment detail.

## Availability

| Nines | Downtime/year |
|---|---|
| 99% | 3.65 days |
| 99.9% | 8.8 hours |
| 99.99% | 52 minutes |
| 99.999% | 5 minutes |

Each nine costs roughly an order of magnitude more. Services in series multiply:
three 99.9% dependencies give you 99.7%.

## Vertical vs horizontal

Vertical scaling (a bigger machine) is simpler and always has a ceiling.
Horizontal scaling (more machines) is unbounded and requires statelessness, load
balancing, and an answer for shared state. Start vertical, design so horizontal
is possible.

## Measure the right latency

Averages hide everything. Report p50, p95, p99 — at scale, the p99 is the
experience of your most engaged users, because they make the most requests.
""",
            "concepts": [
                ("Non-functional requirements", "Latency, availability, consistency, scale and retention targets."),
                ("Back-of-envelope estimation", "Order-of-magnitude sizing before choosing an architecture."),
                ("Tail latency", "p95/p99 — the slow requests that dominate user experience."),
                ("Horizontal scaling", "Adding machines rather than growing one."),
            ],
            "takeaways": [
                "Get the numbers before choosing technology",
                "Memory is ~1000× faster than SSD; a cross-region hop is ~150ms",
                "Dependencies in series multiply their availability",
                "Design against p99, not the average",
            ],
            "resources": [
                {"kind": "doc", "title": "System Design Primer — Performance vs scalability", "url": "https://github.com/donnemartin/system-design-primer#performance-vs-scalability"},
                {"kind": "article", "title": "Latency numbers every programmer should know", "url": "https://gist.github.com/jboner/2841832"},
            ],
            "video": {"query": "system design fundamentals estimation latency numbers", "title": "System design fundamentals"},
        },
        {
            "id": "load-balancing",
            "title": "Load balancing & statelessness",
            "topic": "Load Balancing",
            "summary": "Spreading traffic, and the design property that makes it possible.",
            "minutes": 14,
            "body": """
## Statelessness first

A load balancer can only spread traffic freely if any server can serve any
request. That means no in-memory sessions, no local file uploads, no per-server
counters. Push state to a shared store (Redis, the database, the client's
token) and servers become interchangeable.

Sticky sessions are the workaround, and they cost you: uneven load, lost state
on restart, and a slow drain during deploys.

## Layers

- **L4** — routes on IP/port. Fast, protocol-agnostic, no visibility into HTTP.
- **L7** — reads the request: route by path or header, terminate TLS, retry,
  compress. Slower, far more useful.

## Algorithms

- **Round robin** — simple; assumes equal requests and equal servers.
- **Least connections** — better when request durations vary widely.
- **Consistent hashing** — the same key lands on the same node, which is what
  makes cache nodes and shards addable without reshuffling everything.

## Health checks

Two kinds, and conflating them causes outages:

- **Liveness** — is the process alive? Failing means restart.
- **Readiness** — can it serve traffic *right now*? Failing means remove from
  the pool, do not restart.

A readiness check that also pings the database will pull every server out of
rotation the moment the database blips. Check what this instance can control.

## Deploy patterns

- **Rolling** — replace instances gradually.
- **Blue/green** — two environments, switch traffic, roll back instantly.
- **Canary** — 1% of traffic to the new version, watch error rates, ramp.

All three depend on graceful shutdown: stop accepting new requests, drain
in-flight ones, then exit.

## CDN and the edge

Static assets, images and cacheable API responses should be served from an edge
location near the user. The cheapest latency win available is not sending the
request to your origin at all.
""",
            "concepts": [
                ("Statelessness", "Any instance can serve any request; state lives in a shared store."),
                ("L4 vs L7", "Transport-level versus application-level routing."),
                ("Consistent hashing", "Mapping keys to nodes so adding a node moves only a fraction of keys."),
                ("Readiness vs liveness", "Whether to remove from rotation or restart."),
            ],
            "takeaways": [
                "Statelessness is what makes horizontal scaling work",
                "L7 balancers can route, retry and terminate TLS; L4 cannot",
                "Consistent hashing avoids reshuffling every key when the pool changes",
                "Never fail readiness on a shared dependency — you will drain the whole fleet",
            ],
            "resources": [
                {"kind": "doc", "title": "System Design Primer — Load balancer", "url": "https://github.com/donnemartin/system-design-primer#load-balancer"},
                {"kind": "doc", "title": "MDN — HTTP caching", "url": "https://developer.mozilla.org/en-US/docs/Web/HTTP/Caching"},
            ],
            "video": {"query": "load balancing algorithms l4 l7 consistent hashing explained", "title": "Load balancing"},
        },
        {
            "id": "caching",
            "title": "Caching",
            "topic": "Caching",
            "summary": "The biggest lever available — and the hardest thing to invalidate.",
            "minutes": 17,
            "body": """
## Where caches live

Browser → CDN → API gateway → application (in-process) → distributed cache
(Redis) → database buffer pool.

Each layer you hit is an order of magnitude slower than the one before, so the
question is always: what is the *earliest* layer that can answer this?

## Patterns

**Cache-aside (lazy loading)** — the default:

```python
value = await cache.get(key)
if value is None:
    value = await db.fetch(key)
    await cache.set(key, value, ttl=300)
return value
```

Only requested data is cached; the first request pays. **Write-through** writes
to cache and database together (consistent, slower writes). **Write-behind**
writes to cache and flushes asynchronously (fast, risks loss).

## Invalidation

Three options, in order of preference:

1. **TTL** — accept staleness for a bounded window. Simplest and usually right.
2. **Explicit invalidation on write** — correct, but every write path must
   remember, and one that forgets is a bug you will find in production.
3. **Versioned keys** — `user:42:v7`. Bump the version and old entries expire
   naturally. No delete needed, no missed path.

## Stampede

When a hot key expires, every concurrent request misses and hits the database at
once. Defences:

- **Jittered TTLs** so keys do not expire together.
- **A lock**: one request recomputes, others wait or serve stale.
- **Early recomputation**: refresh at 80% of TTL in the background.

## Eviction

LRU is the sane default; LFU suits skewed access. **Watch the hit rate.** A
cache below ~80% hit rate is often adding a round trip rather than removing one.

## What not to cache

- Data that must be exactly current (balances, stock, permissions).
- Per-user data with no reuse — you are just paying memory.
- Anything whose staleness would be a security problem: a revoked permission
  cached for five minutes is five minutes of unauthorised access.

## HTTP caching

```
Cache-Control: public, max-age=31536000, immutable   # hashed asset
Cache-Control: private, no-cache                     # revalidate every time
ETag: "a1b2c3"                                       # 304 when unchanged
```

Content-hashed filenames plus a long max-age is the strongest caching strategy
on the web, because the URL changes when the content does.
""",
            "concepts": [
                ("Cache-aside", "Read from cache, fall back to the source, then populate."),
                ("Cache stampede", "Many concurrent misses on one expired hot key."),
                ("Versioned key", "Embedding a version in the key so invalidation needs no deletes."),
                ("ETag", "A content fingerprint enabling conditional requests and 304 responses."),
            ],
            "takeaways": [
                "Answer at the earliest layer that can — each one back is ~10× slower",
                "TTL first, versioned keys next; explicit invalidation is the one that gets forgotten",
                "Jitter TTLs and lock recomputation to prevent stampedes",
                "Never cache permission decisions longer than you can tolerate them being wrong",
            ],
            "resources": [
                {"kind": "doc", "title": "System Design Primer — Cache", "url": "https://github.com/donnemartin/system-design-primer#cache"},
                {"kind": "doc", "title": "MDN — HTTP caching", "url": "https://developer.mozilla.org/en-US/docs/Web/HTTP/Caching"},
            ],
            "video": {"query": "caching strategies cache aside invalidation stampede system design", "title": "Caching strategies"},
        },
        {
            "id": "databases",
            "title": "Databases, replication and sharding",
            "topic": "Data Layer",
            "summary": "Scaling reads, then writes, and what each step costs you.",
            "minutes": 18,
            "body": """
## SQL or NoSQL

Not a religious question — a shape question.

- **Relational** — related entities, transactions, ad-hoc queries, constraints
  that must hold. The correct default.
- **Document** — self-contained documents, flexible schema, access by key.
- **Key-value** — caches, sessions, counters.
- **Wide-column** — enormous write volume with known access patterns.
- **Graph** — relationship traversal is the primary query.

Modern Postgres covers relational, document (JSONB), key-value, full-text and
vector workloads. Adding a second datastore adds a consistency problem; do it
when the first one demonstrably cannot cope.

## Scaling reads: replication

A primary takes writes and streams to replicas that serve reads.

- **Asynchronous** — fast, but replicas lag. A user can write and then not see
  their own write. Fix with read-your-writes routing: send a user's reads to the
  primary for a short window after they write.
- **Synchronous** — no lag, but every write waits for a replica.

Replication scales reads. It does not scale writes at all.

## Scaling writes: partitioning

Split the data across nodes.

- **By hash of key** — even distribution, but range queries must fan out.
- **By range** — range queries are cheap, hot ranges are a real risk.
- **By tenant/geography** — natural isolation, uneven sizes.

What sharding costs you: cross-shard joins, cross-shard transactions, global
unique constraints, and rebalancing. Every one of those was free before.

**Hot partitions** are the usual failure: sharding by `created_at` puts all of
today's traffic on one node. Include a high-cardinality component in the key.

## Before you shard

1. Add indexes and fix the N+1 queries.
2. Cache the hot reads.
3. Add read replicas.
4. Move cold data to a separate store or archive.
5. Vertical scale.

Sharding is the last resort because it is the only one you cannot easily undo.

## Denormalisation

Precomputing joins or storing counts speeds reads and creates a consistency
obligation. Do it when reads dominate, and be explicit about how the copies are
kept in step — usually an event or a scheduled reconciliation job.
""",
            "concepts": [
                ("Replication lag", "The delay before a write appears on a replica."),
                ("Read-your-writes", "Guaranteeing a user sees their own write immediately."),
                ("Sharding", "Partitioning data across nodes to scale writes."),
                ("Hot partition", "A shard receiving disproportionate traffic due to key choice."),
            ],
            "takeaways": [
                "Replicas scale reads only; writes still funnel to the primary",
                "Async replication means a user may not see their own write",
                "Sharding costs joins, transactions and global constraints",
                "Exhaust indexing, caching, replicas and archiving before sharding",
            ],
            "resources": [
                {"kind": "doc", "title": "System Design Primer — Database", "url": "https://github.com/donnemartin/system-design-primer#database"},
                {"kind": "book", "title": "Designing Data-Intensive Applications (reference)", "url": "https://dataintensive.net/"},
            ],
            "video": {"query": "database sharding replication system design explained", "title": "Replication and sharding"},
        },
        {
            "id": "cap",
            "title": "CAP, consistency and correctness",
            "topic": "Consistency",
            "summary": "What a partition forces you to choose, and the models in between.",
            "minutes": 16,
            "body": """
## CAP, stated precisely

**When a network partition occurs**, a distributed system must choose between
**consistency** (every read sees the latest write, or errors) and
**availability** (every request gets an answer, possibly stale).

Partition tolerance is not optional — networks partition. So CAP is really: on a
partition, do you refuse to answer, or answer with possibly-stale data?

The common misreading is "pick two". You do not get to give up P.

## PACELC

The more useful formulation: on a **P**artition, choose **A** or **C**; **E**lse
(normal operation), choose **L**atency or **C**onsistency. Even with no
partition, consistency costs coordination, and coordination costs milliseconds.

## Consistency models

- **Strong / linearisable** — reads see the most recent write. Requires
  coordination; costs latency.
- **Read-your-writes** — you see your own writes; others may lag. Usually what
  users actually notice.
- **Monotonic reads** — you never see time go backwards. Prevents the "my
  comment disappeared on refresh" bug from replica hopping.
- **Eventual** — replicas converge given no new writes.

Choose per operation, not per system. A bank balance wants strong; a follower
count is fine eventually consistent.

## Transactions across services

Distributed transactions (two-phase commit) are slow and block on a coordinator
failure. The practical alternative is a **saga**: a sequence of local
transactions, each with a compensating action.

```
reserve seat → charge card → issue ticket
   ↓ fail          ↓ fail
release seat ← refund card
```

Compensation is not rollback — the intermediate state was visible. Design for
that (hold, then confirm) rather than pretending it was atomic.

## Idempotency

In any distributed system messages are delivered at least once, so consumers
must be idempotent:

```python
if await store.seen(message.id):
    return
await process(message)
await store.mark(message.id)
```

Or make the operation naturally idempotent — `set status = 'paid'` rather than
`balance = balance - 100`.

## Exactly-once

There is no exactly-once *delivery*. There is at-least-once delivery plus
idempotent processing, which produces exactly-once *effects*. Any product
claiming otherwise is describing that.
""",
            "concepts": [
                ("CAP theorem", "Under partition, a system must choose consistency or availability."),
                ("PACELC", "Extends CAP: else, choose latency or consistency."),
                ("Saga", "A sequence of local transactions with compensating actions."),
                ("Idempotent consumer", "A handler that produces the same result if a message is delivered twice."),
            ],
            "takeaways": [
                "Partition tolerance is not optional — the choice is C or A under partition",
                "Even without partitions, consistency trades against latency",
                "Choose a consistency model per operation, not per system",
                "Exactly-once delivery does not exist; at-least-once plus idempotency does",
            ],
            "resources": [
                {"kind": "doc", "title": "System Design Primer — CAP theorem", "url": "https://github.com/donnemartin/system-design-primer#cap-theorem"},
                {"kind": "article", "title": "Martin Kleppmann — Please stop calling databases CP or AP", "url": "https://martin.kleppmann.com/2015/05/11/please-stop-calling-databases-cp-or-ap.html"},
            ],
            "video": {"query": "CAP theorem consistency models explained system design", "title": "CAP and consistency"},
        },
        {
            "id": "async",
            "title": "Queues, events and async processing",
            "topic": "Async",
            "summary": "Decoupling work from the request, and the guarantees you get.",
            "minutes": 16,
            "body": """
## Why async

A request should do the minimum that must happen before the user can continue.
Everything else — emails, thumbnails, indexing, analytics, webhooks — goes on a
queue.

```
POST /orders → validate → persist → enqueue(order.created) → 202 Accepted
                                          ↓
                    [email] [inventory] [analytics] [invoice]
```

Benefits: lower latency, natural retries, load smoothing during spikes, and
consumers that can fail without taking the request path with them.

Costs: eventual consistency, more moving parts, and the need for idempotency
everywhere.

## Queue vs log

- **Queue** (SQS, RabbitMQ) — a message is consumed by one worker and removed.
  Work distribution.
- **Log** (Kafka, Redpanda) — an ordered, retained sequence; many independent
  consumers each track their own offset and can replay. Event streaming.

Choose a log when several systems need the same events, or when replaying
history has value.

## Delivery guarantees

- **At most once** — may lose messages. Rarely acceptable.
- **At least once** — may duplicate. The normal choice; requires idempotent
  consumers.
- **Exactly once** — as an end-to-end *effect*, achieved by at-least-once plus
  deduplication.

## Ordering

Global ordering is expensive. Most systems need ordering only *per entity* —
per user, per order — which a partition key gives you cheaply.

## Dead letter queues

After N failed attempts a message goes to a DLQ instead of retrying forever. Two
rules: alert when it is non-empty, and make it replayable once the bug is fixed.
A DLQ nobody looks at is a silent data-loss channel.

## The transactional outbox

The classic bug: you commit a database transaction and then publish an event —
and the process dies in between. Now the state and the event disagree.

```sql
begin;
  insert into orders ...;
  insert into outbox (topic, payload) values ('order.created', $1);
commit;
-- a separate relay reads outbox and publishes, marking rows as sent
```

One transaction covers both, and a relay publishes at-least-once. This is the
standard fix and worth knowing by name.

## Backpressure

An unbounded queue converts a throughput problem into a memory problem and then
an outage. Bound the queue, shed load at the edge, and monitor **queue depth and
consumer lag** — they are the earliest signals that something downstream is
failing.
""",
            "concepts": [
                ("Message queue", "A buffer decoupling producers from consumers."),
                ("Event log", "A retained, ordered stream that multiple consumers read independently."),
                ("Dead letter queue", "Where messages go after exhausting retries."),
                ("Transactional outbox", "Writing an event in the same transaction as the state change."),
            ],
            "takeaways": [
                "Do only what must be synchronous in the request; queue the rest",
                "Logs allow replay and multiple consumers; queues distribute work",
                "At-least-once delivery is the norm — consumers must be idempotent",
                "Use an outbox so state and events cannot disagree",
            ],
            "resources": [
                {"kind": "doc", "title": "System Design Primer — Asynchronism", "url": "https://github.com/donnemartin/system-design-primer#asynchronism"},
                {"kind": "article", "title": "microservices.io — Transactional outbox", "url": "https://microservices.io/patterns/data/transactional-outbox.html"},
            ],
            "video": {"query": "message queues kafka event driven architecture system design", "title": "Queues and event streaming"},
        },
        {
            "id": "reliability",
            "title": "Reliability & failure handling",
            "topic": "Reliability",
            "summary": "Timeouts, retries, circuit breakers — and what happens without them.",
            "minutes": 16,
            "body": """
## Assume failure

Every network call can be slow, fail, or succeed after the caller gave up. Every
dependency will be down at some point. Design for that as the normal case.

## Timeouts

The single most important reliability setting. Without one, a slow dependency
consumes your threads, then your workers, then your service:

```python
async with httpx.AsyncClient(timeout=2.0) as client:
    response = await client.get(url)
```

Timeouts must shrink as you go deeper: if the user-facing budget is 3s, an
inner call cannot be allowed 5s.

## Retries done right

```python
for attempt in range(3):
    try:
        return await call()
    except TransientError:
        await asyncio.sleep((2 ** attempt) * 0.1 * random.uniform(0.5, 1.5))
raise
```

Three rules:

- **Exponential backoff with jitter.** Synchronised retries are how a blip
  becomes an outage — every client retrying at exactly 1s, 2s, 4s is a DDoS you
  built yourself.
- **Only retry idempotent operations**, or use an idempotency key.
- **Cap total attempts** and give up.

## Circuit breakers

After N consecutive failures, stop calling and fail fast for a cooldown period,
then let one probe through.

```
closed ──(failures ≥ threshold)──▶ open ──(cooldown)──▶ half-open
   ▲                                                        │
   └──────────────(probe succeeds)──────────────────────────┘
```

Retrying a dependency that is down makes its recovery slower. The breaker
protects *them* as much as you.

## Bulkheads and graceful degradation

Isolate resources per dependency so one saturating pool cannot starve the
others. And decide, per feature, what "degraded" means: recommendations can be
empty, the checkout cannot. Cached or partial results beat an error page.

## Idempotency and the ambiguous timeout

A timeout does not mean the operation did not happen. Any write endpoint that
clients retry should accept an idempotency key and return the original result.

## Observability

- **Metrics** — rates, errors, durations, saturation. The four to alert on.
- **Logs** — structured, with a request id.
- **Traces** — a request's path across services; the only way to answer "which
  hop was slow".

Alert on **symptoms users feel** (error rate, latency), not on causes (CPU).
Every alert should be actionable, or it will be ignored.
""",
            "concepts": [
                ("Exponential backoff with jitter", "Increasing, randomised retry delays that avoid synchronised retry storms."),
                ("Circuit breaker", "Failing fast after repeated failures to let a dependency recover."),
                ("Bulkhead", "Isolated resource pools so one failure cannot starve everything."),
                ("Graceful degradation", "Serving reduced functionality instead of failing entirely."),
            ],
            "takeaways": [
                "A missing timeout is the most common cause of cascading failure",
                "Retry only idempotent work, with jittered exponential backoff and a cap",
                "Circuit breakers protect the failing dependency as much as the caller",
                "Alert on user-visible symptoms, not on resource causes",
            ],
            "resources": [
                {"kind": "article", "title": "AWS — Timeouts, retries and backoff with jitter", "url": "https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/"},
                {"kind": "doc", "title": "Google SRE Book", "url": "https://sre.google/sre-book/table-of-contents/"},
            ],
            "video": {"query": "circuit breaker retries backoff resilience patterns explained", "title": "Resilience patterns"},
        },
        {
            "id": "interview",
            "title": "Working through a design question",
            "topic": "Design Process",
            "summary": "A repeatable structure, and a worked example.",
            "minutes": 17,
            "body": """
## The structure

1. **Clarify (5 min).** Scope, users, scale, read/write ratio, latency and
   consistency needs. Write the numbers down. Cut scope explicitly.
2. **Estimate.** rps, storage, bandwidth. Order of magnitude.
3. **API.** The three or four endpoints that matter, with their shapes.
4. **Data model.** Entities, keys, access patterns. The access patterns decide
   the storage choice, not the other way around.
5. **High-level design.** Client → LB → service → cache → database, plus queues.
6. **Deep dive.** Pick the interesting part and go deep: the hot path, the
   sharding key, the consistency boundary.
7. **Bottlenecks and trade-offs.** Name what breaks first and what you would do
   about it.

Talking through trade-offs is the actual assessment. A "correct" architecture
with no stated trade-offs reads worse than a simpler one whose costs you can
articulate.

## Worked example: a URL shortener

**Requirements.** Shorten a URL, redirect, count clicks. 100M new links/month,
10:1 read:write, redirect p99 < 50ms, links never expire.

**Estimates.** ~40 writes/s, ~400 reads/s average, ~1,200 peak. 100M × 500 bytes
≈ 50 GB/month. Small. This is not a scaling problem; it is a latency problem.

**API.**
```
POST /links       {url}      → {short_code}
GET  /{code}                 → 302 Location
```

**Key generation.** Options: hash the URL and take a prefix (collision handling
needed), a counter base62-encoded (short and sequential — enumerable), or a
random 7-character code checked for uniqueness. Seven base62 characters give
~3.5 trillion values; random plus a unique constraint is simplest and not
guessable.

**Storage.** `code` is the primary key and the only access path — key-value
shaped. Postgres handles it easily at this size; a KV store if it grows.

**The hot path.** Redirects are the volume. Cache aggressively: the code→URL
mapping is immutable, so cache it forever. Serve 302 (not 301) if you want click
counts, because browsers cache 301s permanently and you stop seeing traffic.

**Analytics.** Do not write a click row synchronously — enqueue an event and
aggregate asynchronously. The redirect must not wait for analytics.

**What breaks first.** A viral link makes one cache key extremely hot — serve it
from the edge/CDN. Then key-generation contention, then storage growth.

## Common mistakes

- Designing for a million users when asked about a thousand.
- Choosing technology before knowing the access pattern.
- Ignoring the failure case entirely.
- Not stating a single trade-off.
""",
            "concepts": [
                ("Access pattern", "How data is read and written — the primary input to storage choice."),
                ("Hot path", "The highest-volume, latency-critical request flow."),
                ("Trade-off articulation", "Naming what a design gives up, which is the substance of a design answer."),
            ],
            "takeaways": [
                "Clarify and estimate before drawing anything",
                "Access patterns choose the datastore, not the other way round",
                "Go deep on one part rather than shallow on everything",
                "Name what breaks first — a design without stated trade-offs is not a design",
            ],
            "resources": [
                {"kind": "doc", "title": "System Design Primer — Design questions", "url": "https://github.com/donnemartin/system-design-primer#system-design-interview-questions-with-solutions"},
                {"kind": "doc", "title": "System Design Primer — Index of guides", "url": "https://github.com/donnemartin/system-design-primer#index-of-system-design-topics"},
            ],
            "video": {"query": "system design interview walkthrough url shortener", "title": "A design walkthrough"},
            "notes": """
The most reliable signal in a design discussion is whether someone changes their
mind when given a new constraint. Design is not recalling an architecture; it is
responding to numbers.
""",
        },
    ],
    "exams": [
        {
            "id": "sd-exam-1",
            "title": "System Design — Building Blocks",
            "description": "Covers chapters 1–4: fundamentals, load balancing, caching, data layer.",
            "chapter_ids": ["fundamentals", "load-balancing", "caching", "databases"],
            "questions": [
                {
                    "type": "mcq", "topic": "Fundamentals", "chapter_id": "fundamentals",
                    "prompt": "Three services in series each have 99.9% availability. What is the combined availability?",
                    "options": ["99.9%", "About 99.7%", "99.99%", "33.3%"],
                    "answer": 1,
                    "explanation": "Availabilities in series multiply: 0.999³ ≈ 0.997. Every added dependency lowers the ceiling.",
                },
                {
                    "type": "truefalse", "topic": "Fundamentals", "chapter_id": "fundamentals",
                    "prompt": "Average latency is the right metric to design against for user experience.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. Averages hide the tail. p95/p99 is what your most active users actually experience.",
                },
                {
                    "type": "mcq", "topic": "Load Balancing", "chapter_id": "load-balancing",
                    "prompt": "What property must servers have for a load balancer to distribute traffic freely?",
                    "options": ["Identical hardware", "Statelessness", "The same IP range", "Synchronous replication"],
                    "answer": 1,
                    "explanation": "If any server can serve any request, instances become interchangeable. Sticky sessions are the workaround, with costs.",
                },
                {
                    "type": "scenario", "topic": "Load Balancing", "chapter_id": "load-balancing",
                    "prompt": "A readiness probe checks the shared database. The database has a two-second blip and the entire fleet leaves rotation. What was wrong?",
                    "options": [
                        "The probe interval was too short",
                        "Readiness should check what the instance itself controls, not a shared dependency",
                        "It should have been a liveness probe",
                        "The load balancer needed sticky sessions",
                    ],
                    "answer": 1,
                    "explanation": "A shared dependency in a readiness check turns a partial degradation into a total outage.",
                },
                {
                    "type": "mcq", "topic": "Caching", "chapter_id": "caching",
                    "prompt": "What is a cache stampede?",
                    "options": [
                        "A cache growing beyond its memory limit",
                        "Many concurrent requests missing on the same expired hot key and hitting the origin at once",
                        "Replicas disagreeing about a cached value",
                        "Eviction removing recently used entries",
                    ],
                    "answer": 1,
                    "explanation": "Mitigations: jittered TTLs, a recomputation lock, or refreshing early in the background.",
                },
                {
                    "type": "scenario", "topic": "Caching", "chapter_id": "caching",
                    "prompt": "Cached data goes stale because one of six write paths forgets to invalidate. Which strategy removes the class of bug?",
                    "options": [
                        "Longer TTLs",
                        "Versioned cache keys, so a version bump makes old entries unreachable",
                        "A larger cache",
                        "Switching from LRU to LFU",
                    ],
                    "answer": 1,
                    "explanation": "Versioned keys need no delete on any path — old entries simply stop being addressed and expire.",
                },
                {
                    "type": "truefalse", "topic": "Caching", "chapter_id": "caching",
                    "prompt": "Permission and authorisation decisions are good candidates for long-lived caching.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. A revoked permission cached for five minutes is five minutes of unauthorised access.",
                },
                {
                    "type": "mcq", "topic": "Data Layer", "chapter_id": "databases",
                    "prompt": "What does adding read replicas scale?",
                    "options": ["Writes", "Reads only", "Both equally", "Storage capacity"],
                    "answer": 1,
                    "explanation": "All writes still go to the primary. Replicas also introduce lag, so a user may not see their own write.",
                },
                {
                    "type": "scenario", "topic": "Data Layer", "chapter_id": "databases",
                    "prompt": "A table is sharded by `created_at`. One node is at 90% CPU while the others idle. Why?",
                    "options": [
                        "The shard key has low cardinality overall",
                        "All current traffic targets today's range — a hot partition",
                        "Replication lag",
                        "The index is missing",
                    ],
                    "answer": 1,
                    "explanation": "Time-ordered shard keys concentrate all recent activity on one node. Include a high-cardinality component.",
                },
                {
                    "type": "mcq", "topic": "Data Layer", "chapter_id": "databases",
                    "prompt": "Which should you exhaust before sharding?",
                    "options": [
                        "Rewriting the app in another language",
                        "Indexing, caching, read replicas, archiving cold data, vertical scaling",
                        "Adding more application servers",
                        "Switching to a NoSQL database",
                    ],
                    "answer": 1,
                    "explanation": "Sharding costs joins, transactions and global constraints, and is the hardest step to undo.",
                },
            ],
        },
        {
            "id": "sd-exam-2",
            "title": "System Design — Distribution & Reliability",
            "description": "Covers chapters 5–8: CAP and consistency, async processing, reliability, design process.",
            "chapter_ids": ["cap", "async", "reliability", "interview"],
            "questions": [
                {
                    "type": "mcq", "topic": "Consistency", "chapter_id": "cap",
                    "prompt": "What does the CAP theorem actually force you to choose between?",
                    "options": [
                        "Any two of consistency, availability and partition tolerance",
                        "Consistency or availability, but only during a network partition",
                        "Latency or throughput",
                        "SQL or NoSQL",
                    ],
                    "answer": 1,
                    "explanation": "Partition tolerance is not optional. The choice appears when a partition occurs: refuse to answer, or answer possibly-stale.",
                },
                {
                    "type": "scenario", "topic": "Consistency", "chapter_id": "cap",
                    "prompt": "A user posts a comment and sees it, refreshes, and it is gone — then it reappears. Which guarantee is missing?",
                    "options": [
                        "Strong consistency across the whole system",
                        "Read-your-writes / monotonic reads, e.g. by pinning the user's reads after a write",
                        "At-most-once delivery",
                        "A larger cache",
                    ],
                    "answer": 1,
                    "explanation": "The refresh hit a lagging replica. Route a user's reads to the primary (or a consistent replica) for a window after they write.",
                },
                {
                    "type": "mcq", "topic": "Consistency", "chapter_id": "cap",
                    "prompt": "How is 'exactly-once' processing actually achieved?",
                    "options": [
                        "Exactly-once delivery guaranteed by the broker",
                        "At-least-once delivery plus idempotent consumers or deduplication",
                        "At-most-once delivery with retries disabled",
                        "Two-phase commit across all consumers",
                    ],
                    "answer": 1,
                    "explanation": "Exactly-once delivery is impossible over an unreliable network. Exactly-once *effects* are what systems provide.",
                },
                {
                    "type": "code", "topic": "Async", "chapter_id": "async", "language": "sql",
                    "prompt": "What problem does this pattern solve?",
                    "code": "begin;\n  insert into orders ...;\n  insert into outbox (topic, payload) values ('order.created', $1);\ncommit;\n-- a relay publishes outbox rows and marks them sent",
                    "options": [
                        "Queue backpressure",
                        "The state/event divergence when a process dies between committing and publishing",
                        "Message ordering across partitions",
                        "Dead letter accumulation",
                    ],
                    "answer": 1,
                    "explanation": "The transactional outbox makes the state change and the event atomic; a relay then publishes at-least-once.",
                },
                {
                    "type": "mcq", "topic": "Async", "chapter_id": "async",
                    "prompt": "When should you choose an event log (Kafka) over a work queue?",
                    "options": [
                        "When only one worker consumes each message",
                        "When multiple independent consumers need the same events, or replay has value",
                        "When messages must be deleted after processing",
                        "When ordering does not matter",
                    ],
                    "answer": 1,
                    "explanation": "A log retains events and lets each consumer track its own offset; a queue distributes work and removes messages.",
                },
                {
                    "type": "truefalse", "topic": "Async", "chapter_id": "async",
                    "prompt": "An unbounded queue is a safe way to absorb traffic spikes.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. It converts a throughput problem into a memory problem and then an outage. Bound queues and monitor consumer lag.",
                },
                {
                    "type": "mcq", "topic": "Reliability", "chapter_id": "reliability",
                    "prompt": "Why must retries use exponential backoff with jitter?",
                    "options": [
                        "To reduce log volume",
                        "Without jitter, all clients retry in lockstep and turn a blip into a self-inflicted DDoS",
                        "It is required by HTTP",
                        "It makes retries idempotent",
                    ],
                    "answer": 1,
                    "explanation": "Randomising the delay spreads the retry load so the recovering dependency is not hit by a synchronised wave.",
                },
                {
                    "type": "scenario", "topic": "Reliability", "chapter_id": "reliability",
                    "prompt": "A downstream service is failing and your service's threads are all blocked waiting on it. Which pattern fails fast and helps the dependency recover?",
                    "options": [
                        "More aggressive retries",
                        "A circuit breaker that opens after repeated failures",
                        "A larger thread pool",
                        "Sticky sessions",
                    ],
                    "answer": 1,
                    "explanation": "The breaker stops sending traffic during the cooldown, protecting both caller and callee, then probes for recovery.",
                },
                {
                    "type": "mcq", "topic": "Reliability", "chapter_id": "reliability",
                    "prompt": "What should alerts be based on?",
                    "options": [
                        "CPU and memory thresholds",
                        "User-visible symptoms such as error rate and latency",
                        "Deployment frequency",
                        "Queue creation events",
                    ],
                    "answer": 1,
                    "explanation": "Alert on symptoms; use resource metrics for diagnosis. Non-actionable alerts get ignored, including the real ones.",
                },
                {
                    "type": "scenario", "topic": "Design Process", "chapter_id": "interview",
                    "prompt": "In a URL shortener, why serve a 302 rather than a 301 for redirects?",
                    "options": [
                        "301 is not supported by all browsers",
                        "Browsers cache 301 permanently, so click analytics stop arriving",
                        "302 is faster to parse",
                        "301 requires HTTPS",
                    ],
                    "answer": 1,
                    "explanation": "A permanent redirect is cached by the client, meaning subsequent visits never reach your service — and the counts flatline.",
                },
            ],
        },
    ],
}
