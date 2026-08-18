"""TypeScript & Modern JavaScript."""

from __future__ import annotations

COURSE = {
    "id": "typescript",
    "title": "TypeScript & Modern JavaScript",
    "short_title": "TypeScript",
    "subtitle": "Types that describe JavaScript as it is actually written",
    "difficulty": "intermediate",
    "tags": ["Language", "Frontend"],
    "description": (
        "TypeScript only makes sense if JavaScript does. This course covers the "
        "modern JavaScript that TypeScript is layered over — scoping, closures, "
        "the event loop, modules — and then the type system itself: unions and "
        "narrowing, generics, structural typing, utility types and the compiler "
        "settings that decide how much safety you actually get."
    ),
    "objectives": [
        "Explain let/const scoping, closures and the value of `this`",
        "Reason about the event loop, promises and async/await",
        "Use unions, literal types and narrowing to model real data",
        "Write generic functions and constrained type parameters",
        "Apply utility types and understand structural typing",
        "Configure strict mode and validate data crossing the type boundary",
    ],
    "resources": [
        {"kind": "doc", "title": "MDN — JavaScript", "url": "https://developer.mozilla.org/en-US/docs/Web/JavaScript"},
        {"kind": "doc", "title": "TypeScript Handbook", "url": "https://www.typescriptlang.org/docs/handbook/intro.html"},
        {"kind": "doc", "title": "W3Schools — JavaScript tutorial", "url": "https://www.w3schools.com/js/"},
    ],
    "chapters": [
        {
            "id": "modern-js",
            "title": "Modern JavaScript essentials",
            "topic": "JavaScript Core",
            "summary": "let/const, destructuring, spread, and the operators that removed a decade of boilerplate.",
            "minutes": 15,
            "body": """
## Declarations

```js
const user = { name: "Ada" };   // binding cannot be reassigned
user.name = "Grace";            // but the object can still be mutated
let count = 0;                  // reassignable
// var — function-scoped and hoisted; do not use it
```

`const` is the default. Reach for `let` only when you genuinely reassign.

## Destructuring

```js
const { name, role = "member", ...rest } = user;
const [first, second = 0] = numbers;
const { data: { items } } = response;      // nested
function draw({ x, y, colour = "black" }) {}
```

## Spread and rest

```js
const merged = { ...defaults, ...overrides };   // right wins
const copy = [...items];                        // shallow copy
const withNew = [...items, newItem];            // never mutate props
function sum(...numbers) { return numbers.reduce((a, b) => a + b, 0); }
```

Spread is a **shallow** copy. Nested objects are still shared.

## Optional chaining and nullish coalescing

```js
const city = user?.address?.city;         // undefined instead of a TypeError
const port = config.port ?? 3000;         // only null/undefined fall through
const bad  = config.port || 3000;         // 0 also falls through — a bug
```

`??` versus `||` is one of the most common real-world defects: `||` treats `0`,
`""` and `false` as missing.

## Array methods that replace loops

```js
items.map(f)  .filter(p)  .reduce(f, init)
items.find(p) .some(p)    .every(p)
Object.entries(obj).map(([k, v]) => ...)
```

`map` and `filter` return new arrays; `sort` and `reverse` mutate in place —
copy first with `[...items].sort()`.

## Equality

Use `===`. `==` performs coercion with rules nobody remembers (`"" == 0` is
true). The one accepted use of `==` is `x == null`, which tests for `null` or
`undefined` together.
""",
            "concepts": [
                ("Block scope", "let and const are visible only inside the block they are declared in."),
                ("Nullish coalescing", "`??` falls back only for null/undefined, unlike `||` which also catches 0 and ''."),
                ("Shallow copy", "Spread copies one level; nested objects remain shared."),
            ],
            "takeaways": [
                "`const` by default; `var` never",
                "`??` not `||` when 0, '' or false are valid values",
                "Spread copies one level only",
                "`===` always; `== null` is the one exception",
            ],
            "resources": [
                {"kind": "doc", "title": "MDN — JavaScript language basics", "url": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide"},
                {"kind": "doc", "title": "W3Schools — JavaScript ES6", "url": "https://www.w3schools.com/js/js_es6.asp"},
            ],
            "video": {"id": "zQnBQ4tB3ZA", "title": "TypeScript in 100 Seconds", "channel": "Fireship"},
        },
        {
            "id": "functions-closures",
            "title": "Functions, closures and `this`",
            "topic": "Functions",
            "summary": "Why arrow functions exist, and what a closure actually captures.",
            "minutes": 15,
            "body": """
## Closures

A function keeps a live reference to the scope it was created in:

```js
function counter() {
  let n = 0;
  return () => ++n;      // closes over n
}
const tick = counter();
tick(); tick();          // 1, 2
```

This is the mechanism behind React hooks, module privacy, memoisation and every
"stale value" bug you will meet — the closure captured the *variable*, and if
that variable was captured at an old render, that is the value you get.

## `this`

`this` in a normal function is decided by **how it is called**:

```js
const obj = {
  name: "Ada",
  greet() { return `Hi ${this.name}`; },
};
obj.greet();                    // "Hi Ada"
const loose = obj.greet;
loose();                        // this is undefined (strict mode)
```

Arrow functions have no `this` of their own; they inherit it lexically. That is
why callbacks should be arrows:

```js
class Timer {
  seconds = 0;
  start() {
    setInterval(() => { this.seconds++; }, 1000);   // `this` is the Timer
  }
}
```

## Higher-order functions

```js
const withRetry = (fn, times = 3) => async (...args) => {
  let lastError;
  for (let i = 0; i < times; i++) {
    try { return await fn(...args); }
    catch (err) { lastError = err; }
  }
  throw lastError;
};
```

Functions are values: pass them, return them, store them.

## Purity

A pure function returns the same output for the same input and touches nothing
else. Pure functions are trivially testable, memoisable and safe to reorder —
push side effects to the edges of your program.
""",
            "concepts": [
                ("Closure", "A function plus the scope it captured at creation."),
                ("Lexical this", "Arrow functions inherit `this` from where they are defined, not how they are called."),
                ("Higher-order function", "A function that takes or returns another function."),
            ],
            "takeaways": [
                "A closure captures the variable, not a snapshot of its value",
                "`this` in a normal function depends on the call site",
                "Arrow functions inherit `this` lexically — use them for callbacks",
                "Keep side effects at the edges; pure functions in the middle",
            ],
            "resources": [
                {"kind": "doc", "title": "MDN — Closures", "url": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Closures"},
                {"kind": "doc", "title": "MDN — this", "url": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Operators/this"},
            ],
            "video": {"query": "javascript closures this arrow functions explained", "title": "Closures and `this`"},
        },
        {
            "id": "async",
            "title": "Async JavaScript & the event loop",
            "topic": "Async",
            "summary": "One thread, a queue, and why `await` in a loop is usually wrong.",
            "minutes": 17,
            "body": """
## One thread

JavaScript runs your code on a single thread. Asynchronous work is handed to
the host (browser or Node), which calls back later. The **event loop** runs
your synchronous code to completion, then drains the **microtask** queue
(promise callbacks), then takes one **macrotask** (timers, I/O) — and repeats.

```js
console.log("1");
setTimeout(() => console.log("4"), 0);     // macrotask
Promise.resolve().then(() => console.log("3"));  // microtask
console.log("2");
// 1 2 3 4
```

Microtasks always run before the next timer, even a zero-delay one.

## Promises and async/await

```js
async function loadUser(id) {
  const res = await fetch(`/api/users/${id}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
```

`await` unwraps a promise; an `async` function always returns one. Note that
`fetch` **does not reject on 4xx/5xx** — you must check `res.ok` yourself.

## Concurrency

```js
// Sequential — three round trips, one after another
for (const id of ids) results.push(await loadUser(id));

// Concurrent — all at once
const results = await Promise.all(ids.map(loadUser));

// Concurrent, tolerating failures
const settled = await Promise.allSettled(ids.map(loadUser));
```

`await` inside a loop serialises independent work. It is the single most common
performance bug in async JavaScript. Use `Promise.all` when the calls do not
depend on each other — but note it rejects as soon as *any* promise rejects.

## Errors and cancellation

```js
try {
  const user = await loadUser(id);
} catch (err) {
  report(err);
} finally {
  setLoading(false);
}

const controller = new AbortController();
fetch(url, { signal: controller.signal });
controller.abort();     // cancels the request
```

An unhandled rejection in a floating promise is invisible — always `await` or
`.catch()`.
""",
            "concepts": [
                ("Event loop", "The scheduler that alternates between the call stack, microtasks and macrotasks."),
                ("Microtask", "A promise continuation, run before the next timer or I/O callback."),
                ("Promise.all", "Runs promises concurrently and rejects as soon as one rejects."),
                ("AbortController", "The standard mechanism for cancelling in-flight fetches."),
            ],
            "takeaways": [
                "Microtasks (promises) run before macrotasks (timers)",
                "`fetch` does not throw on HTTP errors — check `res.ok`",
                "`await` in a loop serialises independent work; use Promise.all",
                "Promise.all rejects fast; allSettled tolerates partial failure",
            ],
            "resources": [
                {"kind": "doc", "title": "MDN — Using promises", "url": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Using_promises"},
                {"kind": "doc", "title": "MDN — The event loop", "url": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Execution_model"},
            ],
            "video": {"query": "javascript event loop microtasks explained", "title": "The event loop"},
        },
        {
            "id": "ts-basics",
            "title": "TypeScript basics & inference",
            "topic": "Type Basics",
            "summary": "Annotate boundaries, let inference do the rest, and never reach for `any`.",
            "minutes": 15,
            "body": """
## Inference first

```ts
let count = 0;                 // number — inferred
const name = "Ada";            // "Ada" — a literal type, because const
const items = [1, 2, 3];       // number[]
```

Annotate **boundaries** — function signatures, exported values, external data —
and let inference handle locals. Over-annotating adds noise without safety.

## `type` vs `interface`

```ts
interface User { id: string; name: string }
type Status = "draft" | "published" | "archived";
type Result<T> = { ok: true; value: T } | { ok: false; error: string };
```

Use `interface` for object shapes that others may extend; `type` for unions,
intersections, tuples and mapped types. Do not agonise — they overlap heavily.

## The types that matter

- `unknown` — "something, but you must narrow before use". The safe top type.
- `any` — turns off checking and infects everything it touches.
- `never` — impossible. What an exhaustive switch's default should be.
- `void` — a function that returns nothing meaningful.

```ts
function parse(raw: string): unknown { return JSON.parse(raw); }

const data = parse(input);
// data.name          // error — good
if (typeof data === "object" && data !== null && "name" in data) {
  // narrowed
}
```

Every `any` is a hole in the type system. `unknown` forces the check that `any`
skips.

## Optional and readonly

```ts
interface Options {
  readonly id: string;
  retries?: number;                     // number | undefined
  onDone?: (result: string) => void;
}
```

## Strict mode

```json
{ "compilerOptions": { "strict": true, "noUncheckedIndexedAccess": true } }
```

`strict` enables `strictNullChecks`, which is the setting that gives TypeScript
most of its value: without it, `null` and `undefined` are assignable to
everything and the compiler cannot warn you about the most common runtime error
in JavaScript.
""",
            "concepts": [
                ("Type inference", "The compiler deriving a type from an initialiser or return expression."),
                ("unknown", "A top type that must be narrowed before use — the safe alternative to any."),
                ("strictNullChecks", "The flag that makes null and undefined distinct types rather than universal values."),
            ],
            "takeaways": [
                "Annotate boundaries; let inference cover locals",
                "`unknown` forces a check, `any` removes all checks",
                "`interface` for extensible object shapes, `type` for unions and mapped types",
                "Turn `strict` on — strictNullChecks is where most of the value is",
            ],
            "resources": [
                {"kind": "doc", "title": "TypeScript Handbook — Everyday types", "url": "https://www.typescriptlang.org/docs/handbook/2/everyday-types.html"},
                {"kind": "doc", "title": "TSConfig reference — strict", "url": "https://www.typescriptlang.org/tsconfig#strict"},
            ],
            "video": {"query": "typescript basics types inference tutorial", "title": "TypeScript basics"},
        },
        {
            "id": "unions-narrowing",
            "title": "Unions, narrowing and discriminated types",
            "topic": "Narrowing",
            "summary": "Modelling 'one of several shapes' so the compiler proves you handled them all.",
            "minutes": 16,
            "body": """
## Union types

```ts
type Id = string | number;
type Status = "idle" | "loading" | "error";
```

A union is only usable once **narrowed** to one member.

## Narrowing

The compiler follows your control flow:

```ts
function format(value: string | number | Date): string {
  if (typeof value === "string") return value.trim();     // string here
  if (value instanceof Date) return value.toISOString();  // Date here
  return value.toFixed(2);                                // number by elimination
}
```

Also narrowing: `in`, truthiness checks, `Array.isArray`, and equality against
literal types.

## Discriminated unions

The most useful pattern in application TypeScript. Give every member a shared
literal field:

```ts
type Result<T> =
  | { status: "ok"; data: T }
  | { status: "error"; message: string }
  | { status: "loading" };

function render<T>(r: Result<T>) {
  switch (r.status) {
    case "ok":      return show(r.data);        // data exists here
    case "error":   return alert(r.message);    // message exists here
    case "loading": return spinner();
  }
}
```

Impossible states — data *and* an error, loading *with* data — become
unrepresentable rather than merely unlikely.

## Exhaustiveness with `never`

```ts
default: {
  const _exhaustive: never = r;      // compile error if a case is missing
  throw new Error(`unhandled: ${JSON.stringify(_exhaustive)}`);
}
```

Add a fourth member to the union and this line fails to compile — the compiler
tells you every switch that needs updating.

## Type guards

```ts
function isUser(value: unknown): value is User {
  return typeof value === "object" && value !== null
    && "id" in value && typeof (value as User).id === "string";
}
```

A `value is T` return type teaches the compiler about a runtime check. Keep the
check honest: the compiler trusts you here.
""",
            "concepts": [
                ("Union type", "A value that may be one of several types."),
                ("Narrowing", "Using runtime checks so the compiler knows which union member is in play."),
                ("Discriminated union", "A union whose members share a literal-typed tag field."),
                ("Exhaustiveness check", "Assigning to `never` so a missing case becomes a compile error."),
            ],
            "takeaways": [
                "Discriminated unions make impossible states unrepresentable",
                "typeof, instanceof, `in` and literal equality all narrow",
                "A `never` assignment in the default case turns a missed case into a compile error",
                "`value is T` guards are trusted by the compiler — keep them honest",
            ],
            "resources": [
                {"kind": "doc", "title": "TypeScript Handbook — Narrowing", "url": "https://www.typescriptlang.org/docs/handbook/2/narrowing.html"},
                {"kind": "article", "title": "TypeScript Handbook — Unions and intersections", "url": "https://www.typescriptlang.org/docs/handbook/2/everyday-types.html#union-types"},
            ],
            "video": {"query": "typescript discriminated unions narrowing tutorial", "title": "Discriminated unions"},
        },
        {
            "id": "generics",
            "title": "Generics",
            "topic": "Generics",
            "summary": "Types as parameters — preserving information the compiler would otherwise lose.",
            "minutes": 16,
            "body": """
## The problem generics solve

```ts
function firstAny(items: any[]): any { return items[0]; }
const x = firstAny(["a", "b"]);          // any — the type was thrown away

function first<T>(items: T[]): T | undefined { return items[0]; }
const y = first(["a", "b"]);             // string — preserved
```

`T` is a parameter filled in at the call site, usually by inference.

## Constraints

```ts
function longest<T extends { length: number }>(a: T, b: T): T {
  return a.length >= b.length ? a : b;
}
longest("abc", "de");        // fine
longest([1], [2, 3]);        // fine
longest(1, 2);               // error — number has no length
```

`extends` here means "at least this shape".

## keyof and indexed access

```ts
function pluck<T, K extends keyof T>(items: T[], key: K): T[K][] {
  return items.map((item) => item[key]);
}

const names = pluck(users, "name");    // string[]
pluck(users, "nope");                  // compile error
```

This is the pattern that makes property access type-safe across any object
shape.

## Generic components and defaults

```ts
type ApiResponse<T = unknown> = { data: T; requestId: string };

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

const user = await getJson<User>("/api/me");
```

Note what that last one really is: an **assertion**, not a check. `getJson`
promises a `User` it never verified. At an I/O boundary, validate — with a
schema library or a hand-written guard — and let the validator's output type
flow onward.

## When not to reach for generics

If a type parameter appears exactly once in a signature, it is not doing
anything a union or `unknown` would not do more legibly. Generics earn their
place by *relating* two positions — an argument to a return, or one argument to
another.
""",
            "concepts": [
                ("Type parameter", "A placeholder type supplied (or inferred) at the call site."),
                ("Constraint", "`T extends Shape` — restricting what a type parameter may be."),
                ("keyof", "The union of an object type's property names."),
                ("Indexed access type", "`T[K]` — the type of property K on T."),
            ],
            "takeaways": [
                "Generics preserve type information that `any` discards",
                "`extends` constrains a parameter to at least a shape",
                "`K extends keyof T` plus `T[K]` gives type-safe property access",
                "A cast at an I/O boundary is a promise, not a check — validate instead",
            ],
            "resources": [
                {"kind": "doc", "title": "TypeScript Handbook — Generics", "url": "https://www.typescriptlang.org/docs/handbook/2/generics.html"},
                {"kind": "doc", "title": "TypeScript Handbook — keyof", "url": "https://www.typescriptlang.org/docs/handbook/2/keyof-types.html"},
            ],
            "video": {"query": "typescript generics explained tutorial", "title": "TypeScript generics"},
        },
        {
            "id": "utility-types",
            "title": "Utility & mapped types",
            "topic": "Utility Types",
            "summary": "Deriving types from types, so one change propagates everywhere.",
            "minutes": 15,
            "body": """
## The standard library

```ts
interface User { id: string; name: string; email: string; admin: boolean }

Partial<User>                  // every property optional  — patch payloads
Required<User>                 // every property required
Readonly<User>                 // every property readonly
Pick<User, "id" | "name">      // a subset
Omit<User, "admin">            // everything except
Record<string, number>         // an index signature
ReturnType<typeof getUser>     // a function's return type
Awaited<Promise<User>>         // unwraps a promise
NonNullable<string | null>     // string
```

Deriving beats duplicating. If `User` gains a field, every derived type follows
— a hand-written `UserUpdate` does not.

```ts
type UserPatch = Partial<Omit<User, "id">>;
type UserRow = Readonly<User>;
```

## Mapped types

The mechanism behind all of the above:

```ts
type Nullable<T> = { [K in keyof T]: T[K] | null };
type Getters<T> = { [K in keyof T as `get${Capitalize<string & K>}`]: () => T[K] };
// Getters<User> → { getId(): string; getName(): string; ... }
```

`as` in a mapped type renames keys; template literal types build the new names.

## Conditional types

```ts
type Unwrap<T> = T extends Promise<infer U> ? U : T;
type A = Unwrap<Promise<string>>;   // string
type B = Unwrap<number>;            // number
```

`infer` captures a type from a pattern. Powerful, and easy to overdo — a type
nobody on the team can read is a liability whatever it proves.

## satisfies

```ts
const routes = {
  home: "/",
  user: "/users/:id",
} satisfies Record<string, `/${string}`>;

routes.home;   // "/" — literal type preserved
```

`satisfies` checks a value against a type **without widening it**, which is
what `: Record<string, string>` would have done.

## Structural typing

TypeScript compares shapes, not names:

```ts
interface Point { x: number; y: number }
const p = { x: 1, y: 2, z: 3 };
const q: Point = p;               // fine — extra properties allowed via a variable
const r: Point = { x: 1, y: 2, z: 3 };   // error — excess property check on literals
```

Object literals get an excess-property check; the same value through a variable
does not. That asymmetry surprises everyone once.
""",
            "concepts": [
                ("Utility type", "A built-in generic that derives one type from another (Partial, Pick, Omit…)."),
                ("Mapped type", "`{ [K in keyof T]: ... }` — building a type by transforming each key."),
                ("Conditional type", "`T extends U ? X : Y`, often with `infer` to capture a sub-type."),
                ("Structural typing", "Compatibility decided by shape rather than by declared name."),
            ],
            "takeaways": [
                "Derive types with Partial/Pick/Omit rather than duplicating shapes",
                "Mapped types transform keys; `as` renames them",
                "`satisfies` validates without widening the literal type",
                "TypeScript is structural — excess-property checks apply only to object literals",
            ],
            "resources": [
                {"kind": "doc", "title": "TypeScript Handbook — Utility types", "url": "https://www.typescriptlang.org/docs/handbook/utility-types.html"},
                {"kind": "doc", "title": "TypeScript Handbook — Mapped types", "url": "https://www.typescriptlang.org/docs/handbook/2/mapped-types.html"},
            ],
            "video": {"query": "typescript utility types mapped types tutorial", "title": "Utility and mapped types"},
        },
        {
            "id": "tooling",
            "title": "Modules, tooling and the type boundary",
            "topic": "Tooling",
            "summary": "ES modules, tsconfig, and validating everything that enters your program.",
            "minutes": 14,
            "body": """
## ES modules

```ts
export function parse(input: string): Config { ... }
export type { Config };
export default class Client {}

import Client, { parse, type Config } from "./client";
import * as helpers from "./helpers";
```

Modules are file-scoped: nothing is global unless exported. Prefer named
exports — they rename consistently, autocomplete better, and survive
refactoring; a default export can be imported under any name.

`import type` (and inline `type`) marks an import as types-only so the bundler
can erase it entirely.

## tsconfig, the settings that matter

```json
{
  "compilerOptions": {
    "strict": true,
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "noUncheckedIndexedAccess": true,
    "noEmit": true,
    "skipLibCheck": true,
    "paths": { "@/*": ["./*"] }
  }
}
```

- `strict` — the whole point.
- `noUncheckedIndexedAccess` — `arr[i]` becomes `T | undefined`, which is the
  truth.
- `noEmit` — when a bundler does the compiling and `tsc` is your type checker.

## The type boundary

Types are erased at runtime. The compiler proves things about code it can see;
it knows nothing about what arrives over the network.

```ts
const res = await fetch("/api/user");
const user = (await res.json()) as User;    // a lie the compiler believes
```

Validate at the edge instead:

```ts
import { z } from "zod";

const User = z.object({
  id: z.string(),
  name: z.string(),
  email: z.string().email(),
});
type User = z.infer<typeof User>;           // one definition, both worlds

const user = User.parse(await res.json());  // throws on bad data
```

Every API response, every `JSON.parse`, every URL parameter, every form field
is untyped data. Validate once at the boundary and the rest of your program can
trust its types.

## Type checking in CI

`tsc --noEmit` in the pipeline. A type error that only appears in someone's
editor is not a type system — it is a suggestion.
""",
            "concepts": [
                ("Named export", "An export bound to a specific name, unlike a default export."),
                ("Type erasure", "Types exist only at compile time and vanish from the emitted JavaScript."),
                ("Runtime validation", "Checking external data against a schema at the program boundary."),
                ("noUncheckedIndexedAccess", "A flag that types array/index access as possibly undefined."),
            ],
            "takeaways": [
                "Prefer named exports; use `import type` for type-only imports",
                "`strict` plus `noUncheckedIndexedAccess` is the useful baseline",
                "Types are erased — a cast on fetched data proves nothing",
                "Validate external data at the boundary and infer the type from the schema",
            ],
            "resources": [
                {"kind": "doc", "title": "TypeScript Handbook — Modules", "url": "https://www.typescriptlang.org/docs/handbook/2/modules.html"},
                {"kind": "doc", "title": "TSConfig reference", "url": "https://www.typescriptlang.org/tsconfig"},
                {"kind": "doc", "title": "Zod — schema validation", "url": "https://zod.dev/"},
            ],
            "video": {"query": "typescript tsconfig zod runtime validation tutorial", "title": "Tooling and validation"},
            "notes": """
A useful mental split: **the compiler owns the inside of your program, a
validator owns its edges.** Almost every "TypeScript didn't catch it" story is
a missing validator at an edge.
""",
        },
    ],
    "exams": [
        {
            "id": "ts-exam-1",
            "title": "JavaScript & TypeScript — Foundations",
            "description": "Covers chapters 1–4: modern JavaScript, functions, async, TypeScript basics.",
            "chapter_ids": ["modern-js", "functions-closures", "async", "ts-basics"],
            "questions": [
                {
                    "type": "code", "topic": "JavaScript Core", "chapter_id": "modern-js", "language": "javascript",
                    "prompt": "What is the value of `port`?",
                    "code": "const config = { port: 0 };\nconst port = config.port || 3000;",
                    "options": ["0", "3000", "undefined", "null"],
                    "answer": 1,
                    "explanation": "`||` treats 0 as falsy. `??` would give 0, because it only falls back on null/undefined — a very common real bug.",
                },
                {
                    "type": "truefalse", "topic": "JavaScript Core", "chapter_id": "modern-js",
                    "prompt": "`const obj = {...}` prevents the object's properties from being modified.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. `const` prevents rebinding the name; the object itself stays mutable. Use Object.freeze or readonly types for that.",
                },
                {
                    "type": "mcq", "topic": "Functions", "chapter_id": "functions-closures",
                    "prompt": "How does an arrow function determine `this`?",
                    "options": [
                        "From the object it is called on",
                        "Lexically, from the scope where it was defined",
                        "It is always undefined",
                        "From the first argument",
                    ],
                    "answer": 1,
                    "explanation": "Arrow functions have no own `this`; they inherit it from the enclosing scope. That is why they are the right choice for callbacks.",
                },
                {
                    "type": "code", "topic": "Functions", "chapter_id": "functions-closures", "language": "javascript",
                    "prompt": "What do the two calls print?",
                    "code": "function counter() {\n  let n = 0;\n  return () => ++n;\n}\nconst tick = counter();\nconsole.log(tick(), tick());",
                    "options": ["1 1", "1 2", "0 1", "undefined undefined"],
                    "answer": 1,
                    "explanation": "The returned arrow closes over the live variable `n`, so each call increments the same binding.",
                },
                {
                    "type": "code", "topic": "Async", "chapter_id": "async", "language": "javascript",
                    "prompt": "In what order do these log?",
                    "code": "console.log('1');\nsetTimeout(() => console.log('4'), 0);\nPromise.resolve().then(() => console.log('3'));\nconsole.log('2');",
                    "options": ["1 2 3 4", "1 2 4 3", "1 3 2 4", "1 4 3 2"],
                    "answer": 0,
                    "explanation": "Synchronous code first, then the microtask queue (promises), then macrotasks (timers) — even with a zero delay.",
                },
                {
                    "type": "scenario", "topic": "Async", "chapter_id": "async",
                    "prompt": "A page loads 30 independent user records with `for (const id of ids) results.push(await load(id))` and takes 9 seconds. What is the fix?",
                    "options": [
                        "Increase the fetch timeout",
                        "Use `await Promise.all(ids.map(load))` to run them concurrently",
                        "Add try/catch inside the loop",
                        "Convert the function to a generator",
                    ],
                    "answer": 1,
                    "explanation": "`await` in a loop serialises independent requests. Promise.all issues them concurrently.",
                },
                {
                    "type": "truefalse", "topic": "Async", "chapter_id": "async",
                    "prompt": "`fetch()` rejects its promise when the server returns a 500 status.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. It rejects only on network failure. HTTP error statuses resolve normally — you must check `res.ok`.",
                },
                {
                    "type": "mcq", "topic": "Type Basics", "chapter_id": "ts-basics",
                    "prompt": "What is the practical difference between `unknown` and `any`?",
                    "options": [
                        "There is none; `unknown` is an alias",
                        "`unknown` must be narrowed before use, `any` disables checking",
                        "`any` is stricter than `unknown`",
                        "`unknown` cannot be assigned to a variable",
                    ],
                    "answer": 1,
                    "explanation": "`unknown` accepts anything in but requires a check before use. `any` opts out of the type system and spreads.",
                },
                {
                    "type": "mcq", "topic": "Type Basics", "chapter_id": "ts-basics",
                    "prompt": "Which compiler flag is responsible for most of TypeScript's practical safety?",
                    "options": ["noEmit", "strictNullChecks (via strict)", "skipLibCheck", "allowJs"],
                    "answer": 1,
                    "explanation": "Without it, null and undefined are assignable to every type and the most common runtime error in JavaScript is invisible.",
                },
                {
                    "type": "truefalse", "topic": "JavaScript Core", "chapter_id": "modern-js",
                    "prompt": "Spread syntax (`{...obj}`) performs a deep copy.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. It copies one level; nested objects remain shared between the copies.",
                },
            ],
        },
        {
            "id": "ts-exam-2",
            "title": "TypeScript — Type System Assessment",
            "description": "Covers chapters 5–8: narrowing, generics, utility types, tooling.",
            "chapter_ids": ["unions-narrowing", "generics", "utility-types", "tooling"],
            "questions": [
                {
                    "type": "mcq", "topic": "Narrowing", "chapter_id": "unions-narrowing",
                    "prompt": "What makes a union 'discriminated'?",
                    "options": [
                        "All members share the same property names",
                        "Every member has a common property with a distinct literal type",
                        "It contains only two members",
                        "It is declared with `interface`",
                    ],
                    "answer": 1,
                    "explanation": "A shared literal tag such as `status: 'ok' | 'error'` lets the compiler narrow the whole object from one check.",
                },
                {
                    "type": "code", "topic": "Narrowing", "chapter_id": "unions-narrowing", "language": "typescript",
                    "prompt": "What does this default case achieve?",
                    "code": "default: {\n  const _exhaustive: never = result;\n  throw new Error('unhandled');\n}",
                    "options": [
                        "It silences the compiler",
                        "It turns a missing union case into a compile-time error",
                        "It converts the value to a string",
                        "It is required by the switch syntax",
                    ],
                    "answer": 1,
                    "explanation": "If a new union member is added, `result` is no longer `never` and the assignment fails to compile — pointing you at every switch to update.",
                },
                {
                    "type": "code", "topic": "Generics", "chapter_id": "generics", "language": "typescript",
                    "prompt": "What is the inferred type of `names`?",
                    "code": "function pluck<T, K extends keyof T>(items: T[], key: K): T[K][] {\n  return items.map((i) => i[key]);\n}\nconst names = pluck(users, 'name'); // users: User[], User.name: string",
                    "options": ["any[]", "string[]", "keyof User[]", "unknown[]"],
                    "answer": 1,
                    "explanation": "`K` is inferred as the literal `'name'`, so the return type `T[K][]` resolves to `string[]`.",
                },
                {
                    "type": "mcq", "topic": "Generics", "chapter_id": "generics",
                    "prompt": "What does `T extends { length: number }` express?",
                    "options": [
                        "T must be a class inheriting from an interface",
                        "T must have at least a numeric `length` property",
                        "T must be exactly `{ length: number }`",
                        "T must be an array",
                    ],
                    "answer": 1,
                    "explanation": "A constraint is a minimum shape, not an exact match — strings, arrays and custom objects all satisfy it.",
                },
                {
                    "type": "scenario", "topic": "Generics", "chapter_id": "generics",
                    "prompt": "`const user = await res.json() as User` compiles cleanly, yet production throws `Cannot read property 'name' of undefined`. Why?",
                    "options": [
                        "The cast is an assertion — nothing validated the response at runtime",
                        "`as` is not supported in async functions",
                        "The User interface needs `readonly`",
                        "json() returns a string",
                    ],
                    "answer": 0,
                    "explanation": "Types are erased. A cast tells the compiler what to believe; it performs no check. Validate at the boundary instead.",
                },
                {
                    "type": "mcq", "topic": "Utility Types", "chapter_id": "utility-types",
                    "prompt": "Which utility type produces a version of `User` with every property optional?",
                    "options": ["Readonly<User>", "Partial<User>", "Pick<User, keyof User>", "Record<string, User>"],
                    "answer": 1,
                    "explanation": "`Partial<T>` maps every property to optional — the natural type for a PATCH payload.",
                },
                {
                    "type": "code", "topic": "Utility Types", "chapter_id": "utility-types", "language": "typescript",
                    "prompt": "Why use `satisfies` here rather than a type annotation?",
                    "code": "const routes = {\n  home: '/',\n  user: '/users/:id',\n} satisfies Record<string, `/${string}`>;",
                    "options": [
                        "It checks the value while keeping the literal types of each key",
                        "It is faster to compile",
                        "It makes the object readonly",
                        "It allows extra properties",
                    ],
                    "answer": 0,
                    "explanation": "An annotation would widen each value to the declared type. `satisfies` validates without widening.",
                },
                {
                    "type": "truefalse", "topic": "Utility Types", "chapter_id": "utility-types",
                    "prompt": "TypeScript uses structural typing, so an object with extra properties can satisfy an interface when passed through a variable.",
                    "options": ["True", "False"],
                    "answer": 0,
                    "explanation": "True. Excess-property checks apply to object literals assigned directly, not to values passed through a variable.",
                },
                {
                    "type": "mcq", "topic": "Tooling", "chapter_id": "tooling",
                    "prompt": "What does `noUncheckedIndexedAccess` change?",
                    "options": [
                        "It forbids index signatures",
                        "`arr[i]` is typed as `T | undefined`",
                        "It disables array bounds checking at runtime",
                        "It requires all arrays to be readonly",
                    ],
                    "answer": 1,
                    "explanation": "It tells the truth: indexing may find nothing. You then have to handle the undefined case.",
                },
                {
                    "type": "scenario", "topic": "Tooling", "chapter_id": "tooling",
                    "prompt": "An API changed a field from string to number. Nothing failed to compile; the bug reached production. What was missing?",
                    "options": [
                        "A stricter tsconfig",
                        "Runtime schema validation at the fetch boundary",
                        "More generics",
                        "A default export",
                    ],
                    "answer": 1,
                    "explanation": "The compiler cannot see the network. Validate external data with a schema and infer the type from it.",
                },
            ],
        },
    ],
}
