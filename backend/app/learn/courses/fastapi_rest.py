"""REST APIs & FastAPI."""

from __future__ import annotations

COURSE = {
    "id": "fastapi",
    "title": "REST APIs & FastAPI",
    "short_title": "FastAPI",
    "subtitle": "Design an HTTP API, then build it properly",
    "difficulty": "intermediate",
    "tags": ["Backend", "API"],
    "description": (
        "Half of API quality is design decisions made before any code: "
        "resources, verbs, status codes, errors, versioning. The other half is "
        "the implementation — validation, dependency injection, async, auth, "
        "testing and deployment. This course does both, using FastAPI as the "
        "vehicle."
    ),
    "objectives": [
        "Model an API around resources with correct verbs and status codes",
        "Validate requests and responses with Pydantic models",
        "Use dependency injection for shared concerns",
        "Choose correctly between sync and async endpoints",
        "Implement authentication and authorisation safely",
        "Test an API and deploy it with sane operational defaults",
    ],
    "resources": [
        {"kind": "doc", "title": "FastAPI — Tutorial", "url": "https://fastapi.tiangolo.com/tutorial/"},
        {"kind": "doc", "title": "MDN — REST glossary", "url": "https://developer.mozilla.org/en-US/docs/Glossary/REST"},
        {"kind": "doc", "title": "W3Schools — HTTP methods", "url": "https://www.w3schools.com/tags/ref_httpmethods.asp"},
    ],
    "chapters": [
        {
            "id": "rest-design",
            "title": "REST design",
            "topic": "REST Design",
            "summary": "Resources, verbs and the constraints that make an API predictable.",
            "minutes": 16,
            "body": """
## Resources, not procedures

A REST API exposes **nouns**; the HTTP method is the verb.

```
GET    /courses               list
POST   /courses               create
GET    /courses/{id}          read one
PATCH  /courses/{id}          partial update
PUT    /courses/{id}          replace
DELETE /courses/{id}          remove
GET    /courses/{id}/chapters sub-collection
```

`POST /getCourse` and `POST /deleteCourse` are RPC wearing REST's clothes. They
throw away caching, idempotency and every convention a client already knows.

## Safety and idempotency

| Method | Safe (no change) | Idempotent (repeat = same result) |
|---|---|---|
| GET | yes | yes |
| PUT | no | yes |
| DELETE | no | yes |
| PATCH | no | not necessarily |
| POST | no | no |

This is not trivia. It decides what a proxy may cache, what a client may retry
after a timeout, and where you need an idempotency key. Any endpoint that
charges money should accept one.

## Statelessness

Every request carries what the server needs to handle it — the token, the
parameters. No server-side session affinity. That is what lets you run ten
instances behind a load balancer without sticky sessions.

## Collections

```
GET /courses?difficulty=beginner&sort=-created_at&limit=20&cursor=eyJpZCI6...
```

- **Filter** with query parameters.
- **Sort** with an explicit parameter, `-` for descending.
- **Paginate** with a cursor for large or live data; `limit`/`offset` is fine
  for small, stable sets but drifts when rows are inserted.
- Always cap `limit` server-side. An unbounded list endpoint is an outage.

## Versioning

Put it in the path — `/api/v1/...`. It is visible in logs, easy to route, and
trivially testable. Change without a version bump only in additive ways: new
optional fields and new endpoints. Removing a field or tightening validation is
breaking, whatever the changelog says.
""",
            "concepts": [
                ("Resource", "The addressable noun an endpoint exposes."),
                ("Idempotency", "A repeated identical request leaves the same state."),
                ("Cursor pagination", "Paging by an opaque pointer rather than an offset, stable under inserts."),
                ("Statelessness", "Each request carries everything needed to serve it."),
            ],
            "takeaways": [
                "URLs name resources; HTTP methods are the verbs",
                "GET is safe; PUT and DELETE are idempotent; POST is neither",
                "Cap every list endpoint's page size server-side",
                "Adding optional fields is safe; removing or tightening is breaking",
            ],
            "resources": [
                {"kind": "doc", "title": "MDN — HTTP request methods", "url": "https://developer.mozilla.org/en-US/docs/Web/HTTP/Methods"},
                {"kind": "article", "title": "MDN — REST", "url": "https://developer.mozilla.org/en-US/docs/Glossary/REST"},
            ],
            "video": {"query": "REST API design best practices tutorial", "title": "REST API design"},
        },
        {
            "id": "status-errors",
            "title": "Status codes & error design",
            "topic": "Status & Errors",
            "summary": "Saying what happened in a way clients can act on.",
            "minutes": 14,
            "body": """
## The codes that matter

| Code | Meaning | Typical use |
|---|---|---|
| 200 | OK | successful GET/PATCH |
| 201 | Created | POST created a resource (send `Location`) |
| 204 | No Content | successful DELETE |
| 400 | Bad Request | malformed syntax |
| 401 | Unauthorized | missing or invalid credentials |
| 403 | Forbidden | authenticated, not allowed |
| 404 | Not Found | no such resource |
| 409 | Conflict | duplicate, or state conflict |
| 422 | Unprocessable | well-formed but semantically invalid |
| 429 | Too Many Requests | rate limited (send `Retry-After`) |
| 500 | Server Error | your bug |
| 503 | Unavailable | dependency down, overloaded |

**401 vs 403**: 401 means "I do not know who you are"; 403 means "I know, and
you may not." Returning 200 with `{"error": ...}` breaks every client's error
handling and every monitoring tool.

## Error bodies

Be consistent and machine-readable:

```json
{
  "error": {
    "code": "chapter_not_found",
    "message": "No chapter 'retrieval' in course 'rag'.",
    "details": [{"field": "chapter_id", "issue": "unknown"}]
  }
}
```

A stable `code` is what clients branch on; `message` is for humans. Never leak
stack traces, SQL or internal hostnames.

## FastAPI

```python
from fastapi import FastAPI, HTTPException, status

@app.get("/courses/{course_id}", response_model=CourseOut)
async def read_course(course_id: str):
    course = await repo.find(course_id)
    if not course:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such course.")
    return course

@app.exception_handler(DomainError)
async def domain_error(request, exc: DomainError):
    return JSONResponse(status_code=exc.status, content={"error": exc.as_dict()})
```

An app-wide exception handler is how you get one error shape everywhere instead
of one per endpoint.

## Do not leak existence

If a user may not see a resource, 404 is often safer than 403 — a 403 confirms
the resource exists. Choose deliberately and apply it consistently.
""",
            "concepts": [
                ("401 vs 403", "Unauthenticated versus authenticated-but-not-permitted."),
                ("Problem details", "A consistent, machine-readable error body with a stable code."),
                ("Retry-After", "A header telling the client when it may try again."),
            ],
            "takeaways": [
                "Use the status code as the primary signal — never 200 with an error body",
                "401 means unknown identity; 403 means known and refused",
                "Give errors a stable machine-readable code plus a human message",
                "Handle errors app-wide so the shape is identical everywhere",
            ],
            "resources": [
                {"kind": "doc", "title": "MDN — HTTP response status codes", "url": "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status"},
                {"kind": "doc", "title": "FastAPI — Handling errors", "url": "https://fastapi.tiangolo.com/tutorial/handling-errors/"},
            ],
            "video": {"query": "http status codes rest api error handling tutorial", "title": "Status codes and errors"},
        },
        {
            "id": "fastapi-basics",
            "title": "FastAPI fundamentals",
            "topic": "FastAPI Basics",
            "summary": "Path, query and body parameters — and the type hints that document them.",
            "minutes": 15,
            "body": """
## Hello, typed

```python
from fastapi import FastAPI, Query, Path

app = FastAPI(title="Learning API", version="1.0.0")

@app.get("/courses/{course_id}/chapters")
async def list_chapters(
    course_id: str = Path(min_length=1),
    limit: int = Query(20, ge=1, le=100),
    q: str | None = None,
):
    ...
```

Type hints are not decoration here: FastAPI derives request parsing, validation,
error messages and the OpenAPI schema from them. A parameter in the path is a
path parameter; a scalar not in the path is a query parameter; a Pydantic model
is the body.

## Routers

```python
# app/api/courses.py
from fastapi import APIRouter
router = APIRouter(prefix="/api/courses", tags=["courses"])

@router.get("")
async def list_courses(): ...

# app/main.py
app.include_router(courses.router)
```

One router per resource keeps `main.py` a wiring file rather than the whole
application.

## Response models

```python
@app.post("/courses", response_model=CourseOut, status_code=201)
async def create_course(payload: CourseIn) -> CourseOut:
    ...
```

`response_model` filters the response to the declared fields. That is a security
feature as much as a documentation one: return the ORM object directly and you
will eventually ship a password hash.

## Automatic documentation

`/docs` (Swagger UI) and `/openapi.json` come from your annotations. Keep them
accurate — `summary`, `description`, `tags` and `responses={404: {...}}` all
appear there, and a generated client is only as good as the schema behind it.

## Project layout

```
app/
  main.py           app instance, middleware, router wiring
  api/              routers, one per resource
  models/           Pydantic schemas
  db/               repositories
  core/             config, security, dependencies
```

Keep HTTP concerns in `api/` and business logic below it. A function that takes
a `Request` is hard to reuse and harder to test.
""",
            "concepts": [
                ("Path/query/body", "Where a parameter comes from, inferred from its declaration."),
                ("APIRouter", "A group of routes mounted under a prefix."),
                ("response_model", "A schema that filters and validates what leaves the endpoint."),
            ],
            "takeaways": [
                "FastAPI derives parsing, validation and OpenAPI from type hints",
                "One router per resource; main.py stays a wiring file",
                "response_model prevents accidental field leaks",
                "Keep HTTP concerns out of business logic",
            ],
            "resources": [
                {"kind": "doc", "title": "FastAPI — First steps", "url": "https://fastapi.tiangolo.com/tutorial/first-steps/"},
                {"kind": "doc", "title": "FastAPI — Bigger applications", "url": "https://fastapi.tiangolo.com/tutorial/bigger-applications/"},
            ],
            "video": {"query": "fastapi tutorial for beginners full course", "title": "FastAPI tutorial"},
        },
        {
            "id": "pydantic",
            "title": "Pydantic & validation",
            "topic": "Validation",
            "summary": "Schemas at the boundary, and separate models for in and out.",
            "minutes": 15,
            "body": """
## Models are the contract

```python
from pydantic import BaseModel, Field, EmailStr, field_validator

class CourseIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    difficulty: Literal["beginner", "intermediate", "advanced"] = "beginner"
    tags: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str) -> str:
        return v.strip()

class CourseOut(CourseIn):
    id: str
    created_at: datetime
```

Invalid input never reaches your handler — FastAPI returns 422 with a
field-level explanation before the function body runs.

## In, out and DB models are different

- **In** — what a client may send. No `id`, no `created_at`, no `role`.
- **Out** — what you return. No password hash, no internal flags.
- **DB** — what you store.

Collapsing them into one model is how mass-assignment bugs happen: a client
sends `{"role": "admin"}` and your ORM happily saves it.

## Nested models and coercion

```python
class Address(BaseModel):
    city: str
    postcode: str

class UserIn(BaseModel):
    name: str
    address: Address
    tags: set[str] = set()
```

Pydantic v2 coerces sensibly (`"3"` → `3` for an `int`) but refuses nonsense.
Use `model_config = ConfigDict(extra="forbid")` to reject unknown fields rather
than silently dropping them — a typo'd field name should be an error, not a
silent no-op.

## Settings

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    api_key: str
    debug: bool = False
    model_config = {"env_file": ".env"}
```

The app fails at startup with a clear message when configuration is missing,
instead of at 3am with a `KeyError`.

## Where validation belongs

At the edge, once. Inside, work with validated objects and stop re-checking.
Every `if not isinstance(...)` deep in business logic is a boundary that leaked.
""",
            "concepts": [
                ("Schema model", "A Pydantic class defining and validating a data shape."),
                ("Mass assignment", "Letting a client set fields it should not, by reusing one model everywhere."),
                ("extra=forbid", "Rejecting unknown fields rather than ignoring them."),
            ],
            "takeaways": [
                "Validation happens before your handler runs — invalid input never arrives",
                "Separate In, Out and DB models; never accept a client-supplied id or role",
                "Forbid extra fields so typos fail loudly",
                "Validate once at the boundary, then trust the objects",
            ],
            "resources": [
                {"kind": "doc", "title": "Pydantic — Models", "url": "https://docs.pydantic.dev/latest/concepts/models/"},
                {"kind": "doc", "title": "FastAPI — Request body", "url": "https://fastapi.tiangolo.com/tutorial/body/"},
            ],
            "video": {"query": "pydantic v2 fastapi validation tutorial", "title": "Pydantic validation"},
        },
        {
            "id": "dependencies",
            "title": "Dependency injection",
            "topic": "Dependencies",
            "summary": "Shared concerns declared as parameters instead of repeated in every handler.",
            "minutes": 14,
            "body": """
## Declaring what you need

```python
from fastapi import Depends

async def get_db() -> AsyncIterator[Session]:
    async with SessionLocal() as session:
        yield session          # teardown runs after the response

async def current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    user = await decode(token, db)
    if not user:
        raise HTTPException(401, "Invalid credentials")
    return user

@app.get("/me")
async def me(user: User = Depends(current_user)) -> UserOut:
    return user
```

Dependencies compose: `current_user` uses `get_db`, and FastAPI resolves the
graph. Within one request a dependency is **cached** — `get_db` is called once
even if three dependencies ask for it.

## What belongs in a dependency

Database sessions, the authenticated user, pagination parameters, rate limits,
feature flags, request-scoped correlation ids. Anything cross-cutting that a
handler needs but should not construct.

```python
class Pagination(BaseModel):
    limit: int = Field(20, ge=1, le=100)
    cursor: str | None = None

@app.get("/courses")
async def list_courses(page: Pagination = Depends()): ...
```

## Router- and app-level dependencies

```python
router = APIRouter(dependencies=[Depends(require_admin)])
```

Applied to every route in the router. Enforcing auth at the router is far safer
than remembering it on each endpoint — the failure mode of forgetting becomes
"a whole section is locked" rather than "one endpoint is open".

## Testing

```python
app.dependency_overrides[get_db] = lambda: test_session
```

Override in tests and hit real endpoints against a test database with no
monkey-patching. This is the practical payoff of DI, and the reason to inject
rather than import a global.

## Middleware vs dependency

Middleware sees every request including 404s and static files; use it for CORS,
compression, request logging and timing. Dependencies are typed, testable and
per-route — use them for everything else.
""",
            "concepts": [
                ("Dependency injection", "Declaring required collaborators as parameters and letting the framework supply them."),
                ("yield dependency", "A dependency with setup before `yield` and teardown after the response."),
                ("dependency_overrides", "The test hook that swaps a dependency's implementation."),
            ],
            "takeaways": [
                "Dependencies compose and are cached per request",
                "Put auth on the router, not on each endpoint",
                "`yield` dependencies give reliable teardown for sessions and connections",
                "Overrides make endpoint tests trivial — inject rather than import globals",
            ],
            "resources": [
                {"kind": "doc", "title": "FastAPI — Dependencies", "url": "https://fastapi.tiangolo.com/tutorial/dependencies/"},
                {"kind": "doc", "title": "FastAPI — Testing dependencies with overrides", "url": "https://fastapi.tiangolo.com/advanced/testing-dependencies/"},
            ],
            "video": {"query": "fastapi dependency injection depends tutorial", "title": "FastAPI dependencies"},
        },
        {
            "id": "async",
            "title": "Async, concurrency and I/O",
            "topic": "Async",
            "summary": "When `async def` helps, and the one mistake that destroys throughput.",
            "minutes": 15,
            "body": """
## The rule

- `async def` + `await` on async libraries → the endpoint yields while waiting;
  one worker serves many concurrent requests.
- Plain `def` → FastAPI runs it in a **thread pool**, so blocking I/O is safe.
- `async def` containing a **blocking** call → the event loop is stalled and
  every other request on that worker waits.

That third line is the classic FastAPI performance bug:

```python
@app.get("/bad")
async def bad():
    return requests.get(url).json()      # BLOCKS the event loop

@app.get("/good")
async def good():
    async with httpx.AsyncClient() as client:
        return (await client.get(url)).json()

@app.get("/also-fine")
def also_fine():                          # sync def → thread pool
    return requests.get(url).json()
```

If a library has no async version, either use a plain `def` endpoint or push
the call off the loop:

```python
result = await asyncio.to_thread(blocking_call, arg)
```

## Concurrency within a request

```python
courses, progress = await asyncio.gather(
    repo.list_courses(user_id),
    repo.list_progress(user_id),
)
```

Two independent queries in the time of the slower one. `asyncio.gather` is the
cheapest latency win in most APIs.

## Background work

```python
@app.post("/reports")
async def create_report(payload: ReportIn, tasks: BackgroundTasks):
    report = await repo.create(payload)
    tasks.add_task(send_email, report.id)     # after the response is sent
    return report
```

`BackgroundTasks` runs in the same process — fine for an email, wrong for
anything long, retryable or critical. That belongs in a real queue.

## Connection pools and timeouts

Create clients and pools **once**, at startup, and reuse them:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=10.0)
    yield
    await app.state.http.aclose()
```

Every outbound call needs a timeout. Without one, a slow dependency exhausts
your workers and takes your service down with it.
""",
            "concepts": [
                ("Event loop blocking", "A synchronous call inside async code that stalls all concurrent requests."),
                ("Thread pool offload", "Running blocking work off the event loop via a sync endpoint or to_thread."),
                ("asyncio.gather", "Running independent awaitables concurrently."),
            ],
            "takeaways": [
                "Blocking calls inside `async def` stall every request on that worker",
                "A plain `def` endpoint is the correct choice for blocking libraries",
                "Use asyncio.gather for independent I/O within one request",
                "Create clients once at startup, and always set timeouts",
            ],
            "resources": [
                {"kind": "doc", "title": "FastAPI — Concurrency and async/await", "url": "https://fastapi.tiangolo.com/async/"},
                {"kind": "doc", "title": "Python docs — asyncio", "url": "https://docs.python.org/3/library/asyncio.html"},
            ],
            "video": {"query": "fastapi async await blocking event loop explained", "title": "Async in FastAPI"},
        },
        {
            "id": "auth",
            "title": "Authentication & authorisation",
            "topic": "Auth",
            "summary": "Proving who, deciding what, and the mistakes that make both worthless.",
            "minutes": 16,
            "body": """
## Two different questions

**Authentication** — who are you? **Authorisation** — what may you do? They
fail differently (401 vs 403) and belong in different layers.

## Passwords

```python
from passlib.context import CryptContext
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

hashed = pwd.hash(plain)
ok = pwd.verify(plain, hashed)
```

Never store or log a plaintext password. Never use a fast hash (MD5, SHA-256)
for passwords — bcrypt, scrypt and argon2 are deliberately slow.

## JWTs

```python
import jwt
from datetime import datetime, timedelta, timezone

token = jwt.encode(
    {"sub": user.id, "exp": datetime.now(timezone.utc) + timedelta(minutes=15)},
    settings.secret_key, algorithm="HS256",
)
payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
```

Things people get wrong:

- **Not verifying the signature** — `decode` without a key, or with
  `verify=False`, accepts anything.
- **Allowing the `none` algorithm**, or letting the token's own header choose
  the algorithm. Pin `algorithms=[...]` explicitly.
- **Long-lived access tokens.** A JWT cannot be revoked. Keep access tokens to
  minutes and use a refresh token you *can* revoke, stored server-side.
- **Putting secrets in the payload.** It is base64, not encryption — anyone
  holding the token can read it.

## Authorisation

Check permissions **per resource**, in the handler or a dependency:

```python
async def owned_course(course_id: str, user: User = Depends(current_user)) -> Course:
    course = await repo.find(course_id)
    if not course:
        raise HTTPException(404, "No such course.")
    if course.owner_id != user.id and not user.is_admin:
        raise HTTPException(403, "Not yours.")
    return course
```

The most common real vulnerability in APIs is **IDOR**: authenticating the user
and then trusting the id in the URL. Authenticated is not authorised.

## The rest of the baseline

- HTTPS only; `Secure`, `HttpOnly`, `SameSite` on cookies.
- CORS as an explicit allowlist — never `*` with credentials.
- Rate-limit login and anything expensive.
- Secrets from the environment, never in the repository.
- Log authentication failures; never log tokens.
""",
            "concepts": [
                ("Authentication vs authorisation", "Establishing identity versus deciding permitted actions."),
                ("IDOR", "Insecure direct object reference — trusting an id in the URL after authenticating."),
                ("Refresh token", "A revocable, longer-lived credential used to mint short access tokens."),
            ],
            "takeaways": [
                "Hash passwords with a deliberately slow algorithm",
                "Pin the JWT algorithm, keep access tokens short, and never put secrets in the payload",
                "Authenticated is not authorised — check ownership on every resource",
                "CORS is an allowlist; `*` with credentials is not permitted for a reason",
            ],
            "resources": [
                {"kind": "doc", "title": "FastAPI — Security", "url": "https://fastapi.tiangolo.com/tutorial/security/"},
                {"kind": "doc", "title": "OWASP API Security Top 10", "url": "https://owasp.org/API-Security/editions/2023/en/0x11-t10/"},
            ],
            "video": {"query": "fastapi jwt authentication oauth2 tutorial", "title": "Auth in FastAPI"},
        },
        {
            "id": "testing-deploy",
            "title": "Testing & deployment",
            "topic": "Testing & Deploy",
            "summary": "Tests that hit real endpoints, and the operational defaults you should ship with.",
            "minutes": 15,
            "body": """
## Testing endpoints

```python
import pytest
from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture
def client():
    app.dependency_overrides[get_db] = lambda: test_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

def test_create_course(client):
    res = client.post("/api/courses", json={"title": "RAG"})
    assert res.status_code == 201
    assert res.json()["title"] == "RAG"

def test_unknown_course_404(client):
    assert client.get("/api/courses/nope").status_code == 404
```

`TestClient` exercises the real routing, validation and dependency stack. Async
tests use `httpx.AsyncClient` with `ASGITransport`.

## What to test

Boundaries and behaviour, not implementation:

- Happy path per endpoint.
- Validation failures (422) with the wrong body shape.
- Auth: unauthenticated (401), wrong user (403), missing resource (404).
- Pagination edges: empty page, limit cap, last page.

Use a real database in tests — a transaction rolled back per test is fast and
catches the constraint violations mocks never will.

## Observability

- **Structured logs** with a request id, propagated to every log line.
- `/health` for liveness; a separate readiness check that verifies dependencies.
- Metrics: request rate, error rate, p50/p95/p99 latency per route.

## Deployment defaults

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

- Workers ≈ CPU cores for async apps; more only if you are thread-pool bound.
- Terminate TLS at a proxy; run behind it.
- Set outbound timeouts everywhere.
- Graceful shutdown: use the lifespan handler to drain in-flight work and close
  pools.
- Configuration from the environment; the app should fail fast on startup when
  something required is missing.

## Documentation

`/docs` is generated, so keep annotations honest — response models, status
codes, and an example per endpoint. In production, consider putting the docs
behind auth if the API is not public.
""",
            "concepts": [
                ("TestClient", "A client that drives the real ASGI app in-process for endpoint tests."),
                ("Readiness vs liveness", "Whether the service can serve traffic versus whether the process is alive."),
                ("Graceful shutdown", "Draining in-flight requests and closing resources before exit."),
            ],
            "takeaways": [
                "Test through the real app with dependency overrides, not mocks of your own code",
                "Cover 401/403/404/422 paths, not just the happy one",
                "Ship structured logs with a request id and per-route latency metrics",
                "Fail fast at startup on missing configuration; time out every outbound call",
            ],
            "resources": [
                {"kind": "doc", "title": "FastAPI — Testing", "url": "https://fastapi.tiangolo.com/tutorial/testing/"},
                {"kind": "doc", "title": "FastAPI — Deployment", "url": "https://fastapi.tiangolo.com/deployment/"},
            ],
            "video": {"query": "fastapi testing pytest deployment docker tutorial", "title": "Testing and deploying FastAPI"},
            "notes": """
A good smoke test for API design: can a new client integrate using only
`/openapi.json`? If they need a wiki page to know which 200 responses are
actually errors, the design is not finished.
""",
        },
    ],
    "exams": [
        {
            "id": "api-exam-1",
            "title": "REST & FastAPI — Design Assessment",
            "description": "Covers chapters 1–4: REST design, status codes, FastAPI basics, validation.",
            "chapter_ids": ["rest-design", "status-errors", "fastapi-basics", "pydantic"],
            "questions": [
                {
                    "type": "mcq", "topic": "REST Design", "chapter_id": "rest-design",
                    "prompt": "Which method is safe *and* idempotent?",
                    "options": ["POST", "GET", "PATCH", "None of them"],
                    "answer": 1,
                    "explanation": "GET changes nothing and repeats harmlessly. PUT and DELETE are idempotent but not safe; POST is neither.",
                },
                {
                    "type": "scenario", "topic": "REST Design", "chapter_id": "rest-design",
                    "prompt": "A payment endpoint times out and the client retries, producing double charges. What is the standard fix?",
                    "options": [
                        "Change the endpoint to GET",
                        "Accept an idempotency key and return the original result for a repeat",
                        "Increase the client timeout",
                        "Return 202 instead of 201",
                    ],
                    "answer": 1,
                    "explanation": "POST is not idempotent. An idempotency key makes a repeat of the same logical request safe.",
                },
                {
                    "type": "truefalse", "topic": "REST Design", "chapter_id": "rest-design",
                    "prompt": "Adding a new optional field to a response is a breaking change.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. Additive changes are safe; removing fields or tightening validation is breaking.",
                },
                {
                    "type": "mcq", "topic": "Status & Errors", "chapter_id": "status-errors",
                    "prompt": "A request has a valid token but the user may not access this resource. Which status?",
                    "options": ["400", "401", "403", "422"],
                    "answer": 2,
                    "explanation": "401 is 'I don't know who you are'; 403 is 'I know, and you may not'.",
                },
                {
                    "type": "scenario", "topic": "Status & Errors", "chapter_id": "status-errors",
                    "prompt": "An API returns 200 with `{\"success\": false, \"error\": \"not found\"}`. Why is this a problem?",
                    "options": [
                        "It uses JSON instead of XML",
                        "Clients, proxies and monitoring all treat 200 as success, so failures are invisible",
                        "It is slower to parse",
                        "It breaks CORS",
                    ],
                    "answer": 1,
                    "explanation": "The status code is the primary signal. Burying failure in a 200 body defeats retries, caching and alerting.",
                },
                {
                    "type": "mcq", "topic": "FastAPI Basics", "chapter_id": "fastapi-basics",
                    "prompt": "What does `response_model` do beyond documentation?",
                    "options": [
                        "Compresses the response",
                        "Filters and validates the outgoing data to the declared fields",
                        "Caches the response",
                        "Sets the status code",
                    ],
                    "answer": 1,
                    "explanation": "It is a security control: fields not in the model never leave the endpoint, even if the object has them.",
                },
                {
                    "type": "code", "topic": "FastAPI Basics", "chapter_id": "fastapi-basics", "language": "python",
                    "prompt": "Where does FastAPI take `limit` from?",
                    "code": "@app.get('/courses/{course_id}/chapters')\nasync def list_chapters(course_id: str, limit: int = 20):\n    ...",
                    "options": [
                        "The request body",
                        "A query parameter",
                        "A header",
                        "The path",
                    ],
                    "answer": 1,
                    "explanation": "A scalar parameter that is not in the path is a query parameter; a Pydantic model would be the body.",
                },
                {
                    "type": "mcq", "topic": "Validation", "chapter_id": "pydantic",
                    "prompt": "Why should request and response models be separate classes?",
                    "options": [
                        "It is faster",
                        "So clients cannot set server-controlled fields, and internal fields never leak out",
                        "Pydantic requires it",
                        "To support multiple content types",
                    ],
                    "answer": 1,
                    "explanation": "One shared model invites mass assignment on the way in and field leaks on the way out.",
                },
                {
                    "type": "truefalse", "topic": "Validation", "chapter_id": "pydantic",
                    "prompt": "With FastAPI, invalid request bodies reach your handler so you can decide what to do.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. Validation runs first; a bad body returns 422 with field detail before the handler executes.",
                },
                {
                    "type": "scenario", "topic": "Validation", "chapter_id": "pydantic",
                    "prompt": "A client sends `{\"titel\": \"RAG\"}` and the API stores an empty title without complaining. What setting prevents this?",
                    "options": [
                        "`extra=\"forbid\"` on the model, so unknown fields are rejected",
                        "A stricter response_model",
                        "Setting the field to Optional",
                        "Enabling CORS",
                    ],
                    "answer": 0,
                    "explanation": "By default unknown fields are ignored. Forbidding extras turns a typo into a loud 422.",
                },
            ],
        },
        {
            "id": "api-exam-2",
            "title": "FastAPI — Implementation Assessment",
            "description": "Covers chapters 5–8: dependencies, async, auth, testing and deployment.",
            "chapter_ids": ["dependencies", "async", "auth", "testing-deploy"],
            "questions": [
                {
                    "type": "mcq", "topic": "Dependencies", "chapter_id": "dependencies",
                    "prompt": "If three dependencies in one request each declare `Depends(get_db)`, how many times does `get_db` run?",
                    "options": ["Three", "Once — dependencies are cached per request", "Once per worker", "It depends on the pool size"],
                    "answer": 1,
                    "explanation": "FastAPI caches dependency results within a request, so a shared session is genuinely shared.",
                },
                {
                    "type": "scenario", "topic": "Dependencies", "chapter_id": "dependencies",
                    "prompt": "An admin section has 14 endpoints and one of them was shipped without an auth check. What structural change prevents a repeat?",
                    "options": [
                        "Add a comment to the endpoint template",
                        "Attach the auth dependency to the router so it applies to every route",
                        "Move auth into middleware that inspects the path",
                        "Rename the endpoints consistently",
                    ],
                    "answer": 1,
                    "explanation": "Router-level dependencies make forgetting impossible: the default becomes locked rather than open.",
                },
                {
                    "type": "code", "topic": "Async", "chapter_id": "async", "language": "python",
                    "prompt": "What is wrong with this endpoint?",
                    "code": "@app.get('/data')\nasync def get_data():\n    return requests.get(URL).json()",
                    "options": [
                        "requests cannot return JSON",
                        "A blocking call inside async def stalls the event loop for every concurrent request",
                        "The endpoint needs a response_model",
                        "Nothing — FastAPI handles it",
                    ],
                    "answer": 1,
                    "explanation": "Use httpx.AsyncClient with await, or declare the endpoint as a plain `def` so it runs in the thread pool.",
                },
                {
                    "type": "mcq", "topic": "Async", "chapter_id": "async",
                    "prompt": "Two independent database queries take 40ms and 60ms. What does `asyncio.gather` give you?",
                    "options": ["100ms total", "About 60ms total", "20ms total", "No change"],
                    "answer": 1,
                    "explanation": "They overlap, so the total is roughly the slower one — the cheapest latency win in most APIs.",
                },
                {
                    "type": "truefalse", "topic": "Async", "chapter_id": "async",
                    "prompt": "Declaring an endpoint as plain `def` in FastAPI is always a mistake.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. Sync endpoints run in a thread pool, which is exactly the right home for blocking libraries.",
                },
                {
                    "type": "mcq", "topic": "Auth", "chapter_id": "auth",
                    "prompt": "Why must the JWT algorithm be pinned when decoding?",
                    "options": [
                        "It speeds up verification",
                        "Otherwise an attacker can choose the algorithm — including 'none' — via the token header",
                        "It is required for HTTPS",
                        "To support refresh tokens",
                    ],
                    "answer": 1,
                    "explanation": "Letting the token declare its own algorithm is a classic authentication bypass.",
                },
                {
                    "type": "scenario", "topic": "Auth", "chapter_id": "auth",
                    "prompt": "`GET /invoices/{id}` verifies the JWT and returns the invoice. A user changes the id and sees someone else's invoice. What is this?",
                    "options": [
                        "A CORS misconfiguration",
                        "IDOR — authenticated but not authorised for that resource",
                        "A JWT expiry problem",
                        "A rate-limiting failure",
                    ],
                    "answer": 1,
                    "explanation": "Insecure direct object reference. Ownership must be checked per resource, not just identity per request.",
                },
                {
                    "type": "truefalse", "topic": "Auth", "chapter_id": "auth",
                    "prompt": "JWT payloads are encrypted, so sensitive data can safely be stored in them.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. The payload is base64-encoded and signed, not encrypted. Anyone holding the token can read it.",
                },
                {
                    "type": "mcq", "topic": "Testing & Deploy", "chapter_id": "testing-deploy",
                    "prompt": "What makes `dependency_overrides` valuable in tests?",
                    "options": [
                        "It disables validation for speed",
                        "It swaps a real dependency (e.g. the DB session) without patching internals, so tests hit real routing and validation",
                        "It generates test data",
                        "It bypasses authentication automatically",
                    ],
                    "answer": 1,
                    "explanation": "You exercise the real app end to end while controlling the collaborators — the practical payoff of dependency injection.",
                },
                {
                    "type": "scenario", "topic": "Testing & Deploy", "chapter_id": "testing-deploy",
                    "prompt": "One slow upstream dependency takes the whole service down under load. Which single default would most likely have prevented it?",
                    "options": [
                        "More workers",
                        "A timeout on every outbound call",
                        "A larger connection pool",
                        "Gzip compression",
                    ],
                    "answer": 1,
                    "explanation": "Without timeouts, workers pile up waiting and the service exhausts its capacity — the classic cascading failure.",
                },
            ],
        },
    ],
}
