"""React & Next.js."""

from __future__ import annotations

COURSE = {
    "id": "react-nextjs",
    "title": "React & Next.js",
    "short_title": "React",
    "subtitle": "Components, state and the modern rendering model",
    "difficulty": "intermediate",
    "tags": ["Frontend", "Framework"],
    "description": (
        "React is a rendering model before it is a library: UI as a function of "
        "state. This course builds that model — components, state, effects, the "
        "hook rules — then moves into performance and into Next.js: the App "
        "Router, server and client components, data fetching and caching."
    ),
    "objectives": [
        "Describe UI as a pure function of props and state",
        "Choose the right state location and shape",
        "Use effects only for genuine synchronisation with the outside world",
        "Write custom hooks that encapsulate behaviour",
        "Diagnose and fix unnecessary re-renders",
        "Split an app correctly between server and client components",
    ],
    "resources": [
        {"kind": "doc", "title": "React — Learn", "url": "https://react.dev/learn"},
        {"kind": "doc", "title": "Next.js documentation", "url": "https://nextjs.org/docs"},
        {"kind": "doc", "title": "W3Schools — React tutorial", "url": "https://www.w3schools.com/react/"},
    ],
    "chapters": [
        {
            "id": "components",
            "title": "Components, props and JSX",
            "topic": "Components",
            "summary": "The unit of React, and why rendering must stay pure.",
            "minutes": 14,
            "body": """
## A component is a function of its inputs

```tsx
type BadgeProps = { label: string; tone?: "info" | "warn" };

export function Badge({ label, tone = "info" }: BadgeProps) {
  return <span className={`badge badge--${tone}`}>{label}</span>;
}
```

Given the same props, a component must render the same output and change
nothing outside itself. React may call your component more than once for a
single visible update, and in development Strict Mode does so deliberately to
surface impurity.

## JSX is an expression language

```tsx
{items.length > 0 && <List items={items} />}       {/* conditional */}
{loading ? <Spinner /> : <Content />}              {/* either/or */}
{items.map((item) => <Row key={item.id} item={item} />)}
```

Careful with `&&`: `{count && <Badge/>}` renders a literal `0` when count is 0.
Use `count > 0 && ...`.

## Keys

A key tells React which item is which across renders. Use a stable id from your
data. An array index is a bug whenever the list can reorder, insert or filter —
React will keep the wrong DOM node and the wrong component state.

## Composition over configuration

```tsx
<Card>
  <Card.Header>Usage</Card.Header>
  <Card.Body><Chart data={data} /></Card.Body>
</Card>
```

Passing `children` (or elements as props) avoids the "twelve boolean props"
component that nobody can change safely. It is also a performance tool: an
element passed as a child is created by the *parent*, so it does not re-render
when the wrapper's own state changes.

## Lists of props are a smell

If a component takes more than about six props, it is usually two components.
""",
            "concepts": [
                ("Component", "A function taking props and returning describing UI."),
                ("Purity", "Rendering must not mutate anything outside the component or depend on hidden state."),
                ("Key", "A stable identity for a list item across renders."),
                ("Composition", "Building behaviour by nesting components rather than by adding props."),
            ],
            "takeaways": [
                "Rendering must be pure — React can and does render twice",
                "Keys must be stable ids, never array indices in a mutable list",
                "`{count && ...}` renders 0; compare explicitly",
                "Composition beats prop explosion",
            ],
            "resources": [
                {"kind": "doc", "title": "React — Your first component", "url": "https://react.dev/learn/your-first-component"},
                {"kind": "doc", "title": "React — Rendering lists", "url": "https://react.dev/learn/rendering-lists"},
            ],
            "video": {"query": "react components props jsx tutorial", "title": "React components and JSX"},
        },
        {
            "id": "state",
            "title": "State and events",
            "topic": "State",
            "summary": "Where state lives, how it updates, and why it must never be mutated.",
            "minutes": 16,
            "body": """
## useState

```tsx
const [count, setCount] = useState(0);

setCount(count + 1);          // uses the value from this render
setCount((c) => c + 1);       // uses the latest — correct in batches
```

State updates are **asynchronous and batched**. Reading `count` right after
calling `setCount` gives you the old value; that is not a bug, it is the render
model. Use the updater form whenever the next value depends on the previous one.

## Never mutate

```tsx
items.push(newItem); setItems(items);          // WRONG — same reference
setItems([...items, newItem]);                 // right
setUser({ ...user, name });                    // right
setRows(rows.map((r) => r.id === id ? { ...r, done: true } : r));
```

React compares by reference. A mutated array is `===` to the old one, so
nothing re-renders.

## Where state should live

Put it in the **closest common ancestor** of the components that need it — and
no higher. Global state for something two sibling components share is how apps
become untraceable.

## Derived state is not state

```tsx
const [items, setItems] = useState<Item[]>([]);
const [count, setCount] = useState(0);      // WRONG — will drift
const count = items.length;                  // right — compute during render
```

If it can be computed from existing state or props, compute it.

## useReducer

When several fields change together, or the next state depends on an action
rather than a value:

```tsx
function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "submit":  return { ...state, status: "loading", error: null };
    case "success": return { status: "idle", data: action.data, error: null };
    case "failure": return { ...state, status: "idle", error: action.error };
  }
}
const [state, dispatch] = useReducer(reducer, initial);
```

One transition function, testable in isolation, and impossible intermediate
states are easy to rule out.

## Controlled inputs

```tsx
<input value={query} onChange={(e) => setQuery(e.target.value)} />
```

The React state is the source of truth. Mixing controlled and uncontrolled —
passing `value={undefined}` and later a string — produces a warning and real
bugs.
""",
            "concepts": [
                ("Batching", "React groups state updates and re-renders once."),
                ("Updater function", "`setX(prev => next)`, correct when the new value depends on the old."),
                ("Lifting state up", "Moving shared state to the closest common ancestor."),
                ("Derived state", "A value computed from existing state — should not be stored separately."),
            ],
            "takeaways": [
                "Never mutate state — React compares by reference",
                "Use the updater form when the next value depends on the previous",
                "Keep state at the lowest common ancestor",
                "Compute derived values during render instead of storing them",
            ],
            "resources": [
                {"kind": "doc", "title": "React — State: a component's memory", "url": "https://react.dev/learn/state-a-components-memory"},
                {"kind": "doc", "title": "React — Choosing the state structure", "url": "https://react.dev/learn/choosing-the-state-structure"},
            ],
            "video": {"query": "react usestate usereducer state management tutorial", "title": "State in React"},
        },
        {
            "id": "effects",
            "title": "Effects and the lifecycle",
            "topic": "Effects",
            "summary": "useEffect is for synchronising with systems outside React — and little else.",
            "minutes": 17,
            "body": """
## What an effect is for

`useEffect` synchronises your component with something **outside React**: a
subscription, a timer, a browser API, an imperative library. It is not a
lifecycle hook and it is not the place to compute values.

```tsx
useEffect(() => {
  const socket = new WebSocket(url);
  socket.onmessage = (e) => setMessage(e.data);
  return () => socket.close();      // cleanup runs before the next effect and on unmount
}, [url]);                          // re-subscribe only when url changes
```

## The dependency array

It is not a list of "when to run" — it is a list of everything from the render
scope the effect reads. Omitting a dependency gives you a stale closure: the
effect keeps the value from the render that created it.

Do not silence the lint rule. Instead, remove the dependency: move the function
inside the effect, use an updater function, or extract stable logic outside the
component.

## Cleanup and race conditions

```tsx
useEffect(() => {
  let cancelled = false;
  (async () => {
    const data = await fetchUser(id);
    if (!cancelled) setUser(data);       // ignore a response for an old id
  })();
  return () => { cancelled = true; };
}, [id]);
```

Without the guard, switching quickly between ids can show the earlier user's
data because responses arrive out of order.

## You probably do not need an effect

- Transforming data for rendering → compute during render.
- Responding to a user event → do it in the handler.
- Resetting state when a prop changes → change the component's `key` instead.
- Fetching data in a framework → use its loader, server component or a data
  library with caching and deduplication.

An effect that calls `setState` on every render is an infinite loop waiting to
happen.

## refs

```tsx
const inputRef = useRef<HTMLInputElement>(null);
inputRef.current?.focus();
```

A ref is a mutable box that survives renders and does **not** trigger one.
Use it for DOM nodes, timer handles and any value the UI does not display.
""",
            "concepts": [
                ("Effect", "Code that synchronises a component with an external system."),
                ("Dependency array", "Everything from the render scope the effect reads."),
                ("Cleanup function", "The function returned from an effect, run before re-running it and on unmount."),
                ("Stale closure", "An effect or callback holding values from an earlier render."),
            ],
            "takeaways": [
                "Effects are for external systems, not for computing values",
                "Dependencies are what the effect reads — never silence the lint rule",
                "Always clean up subscriptions, timers and in-flight requests",
                "Reset state by changing `key`, not with an effect",
            ],
            "resources": [
                {"kind": "doc", "title": "React — Synchronizing with effects", "url": "https://react.dev/learn/synchronizing-with-effects"},
                {"kind": "doc", "title": "React — You might not need an effect", "url": "https://react.dev/learn/you-might-not-need-an-effect"},
            ],
            "video": {"query": "react useEffect explained dependencies cleanup", "title": "useEffect properly"},
        },
        {
            "id": "hooks",
            "title": "Hook rules & custom hooks",
            "topic": "Hooks",
            "summary": "Why order matters, and how to package behaviour rather than markup.",
            "minutes": 14,
            "body": """
## The two rules

1. **Call hooks at the top level.** Never inside a condition, loop, or after an
   early return.
2. **Call hooks only from components or other hooks.**

React tracks hooks by call *order*, not by name. A conditional hook shifts every
later hook onto the wrong slot — which is why the rule is absolute.

```tsx
if (!user) return null;
const [name, setName] = useState("");   // WRONG — after a conditional return
```

## Custom hooks

A function whose name starts with `use` and which calls other hooks. It shares
**logic**, not state — every component that calls it gets its own state.

```tsx
export function useDebounced<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

const query = useDebounced(rawQuery, 250);
```

Custom hooks are the right unit for: data fetching, media queries, local
storage, keyboard shortcuts, timers, anything with setup and teardown.

## The built-ins worth knowing

- `useMemo(fn, deps)` — cache an expensive computed value.
- `useCallback(fn, deps)` — cache a function identity (memoised children).
- `useRef(initial)` — a mutable box that does not re-render.
- `useContext(Ctx)` — read the nearest provider's value.
- `useId()` — a stable unique id for accessibility attributes.

## Context

```tsx
const ThemeContext = createContext<Theme>("dark");

<ThemeContext.Provider value={theme}>{children}</ThemeContext.Provider>
const theme = useContext(ThemeContext);
```

Context solves prop drilling; it is not a state manager. **Every consumer
re-renders when the provider's value changes**, so pass a memoised object and
split rarely-changing values into their own context.
""",
            "concepts": [
                ("Rules of hooks", "Hooks run unconditionally at the top level of a component or another hook."),
                ("Custom hook", "A `use`-prefixed function packaging stateful logic for reuse."),
                ("Context", "A way to pass a value down the tree without threading props."),
            ],
            "takeaways": [
                "Hooks are matched by call order — never conditional, never after a return",
                "Custom hooks share logic, not state",
                "Context solves prop drilling, not state management",
                "Every context consumer re-renders when the value's identity changes",
            ],
            "resources": [
                {"kind": "doc", "title": "React — Reusing logic with custom hooks", "url": "https://react.dev/learn/reusing-logic-with-custom-hooks"},
                {"kind": "doc", "title": "React — Rules of hooks", "url": "https://react.dev/reference/rules/rules-of-hooks"},
            ],
            "video": {"query": "react custom hooks tutorial rules of hooks", "title": "Custom hooks"},
        },
        {
            "id": "performance",
            "title": "Rendering & performance",
            "topic": "Performance",
            "summary": "Measure first: what actually causes a re-render, and which fixes are real.",
            "minutes": 16,
            "body": """
## What causes a re-render

A component re-renders when its own state changes, when its parent re-renders,
or when a context it consumes changes. Note what is *not* on that list: prop
values. A parent re-render re-renders children regardless of whether their props
changed — unless the child is memoised.

## Memoisation, used correctly

```tsx
const Row = memo(function Row({ item, onSelect }: RowProps) { ... });

// In the parent — otherwise memo() is defeated on every render:
const handleSelect = useCallback((id: string) => select(id), [select]);
const rows = useMemo(() => items.filter(visible), [items]);
```

`memo` compares props shallowly. A new object, array or inline arrow as a prop
is a new reference every render, so `memo` never hits. That is why `memo`,
`useCallback` and `useMemo` usually have to be applied together — and why
applying `memo` alone often does nothing.

Do not memoise everything. Comparison has a cost, and the React Compiler is
increasingly able to do this automatically.

## Cheaper structural fixes

- **Move state down.** State used by one subtree should live in that subtree.
- **Pass children through.** `<Wrapper>{expensive}</Wrapper>` — the child is
  created by the outer parent, so the wrapper's state changes do not re-render it.
- **Split contexts** so a fast-changing value does not re-render slow consumers.

These beat memoisation because they remove the render rather than skipping it.

## Lists

Virtualise long lists — render the visible window only. A thousand DOM rows is
slow no matter how well memoised.

## Loading less

```tsx
const Editor = lazy(() => import("./Editor"));
<Suspense fallback={<Skeleton />}><Editor /></Suspense>
```

Code-splitting at route and heavy-component boundaries usually beats every
render optimisation, because the fastest render is the one whose code you never
downloaded.

## Measure

React DevTools Profiler shows which components rendered and why. Optimise what
it shows you, not what you assume.
""",
            "concepts": [
                ("Re-render", "React calling a component again — cheap in itself; the cost is in the tree below."),
                ("memo", "A wrapper that skips re-rendering when props are shallowly equal."),
                ("Referential stability", "Keeping object/function identities constant across renders so memoisation works."),
                ("Code splitting", "Loading a component's code only when it is needed."),
            ],
            "takeaways": [
                "A parent's re-render re-renders children unless they are memoised",
                "memo without useCallback/useMemo on the props usually does nothing",
                "Moving state down and passing children removes renders rather than skipping them",
                "Profile before optimising; code-splitting often beats memoisation",
            ],
            "resources": [
                {"kind": "doc", "title": "React — memo", "url": "https://react.dev/reference/react/memo"},
                {"kind": "doc", "title": "React — Keeping components pure", "url": "https://react.dev/learn/keeping-components-pure"},
            ],
            "video": {"query": "react performance memo usememo usecallback profiler", "title": "React performance"},
        },
        {
            "id": "nextjs-routing",
            "title": "Next.js App Router",
            "topic": "Next.js Routing",
            "summary": "Files as routes, nested layouts, and the states a route can be in.",
            "minutes": 15,
            "body": """
## Routes are folders

```
app/
  layout.tsx            → wraps everything
  page.tsx              → /
  loading.tsx           → suspense fallback for this segment
  error.tsx             → error boundary (must be a client component)
  not-found.tsx         → 404
  courses/
    page.tsx            → /courses
    [id]/
      page.tsx          → /courses/:id
      chapters/
        [chapterId]/page.tsx  → /courses/:id/chapters/:chapterId
```

`page.tsx` makes a segment routable. `layout.tsx` wraps it and **persists
across navigations within that segment** — state inside a layout survives when
only the page below changes.

Other conventions: `(group)` folders organise without affecting the URL,
`[...slug]` catches all, `@slot` defines parallel routes.

## Params and search params

```tsx
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ page?: string }>;
}) {
  const { id } = await params;
  const { page = "1" } = await searchParams;
  ...
}
```

## Navigation

```tsx
import Link from "next/link";
<Link href={`/courses/${id}`} prefetch>Open</Link>

"use client";
import { useRouter, usePathname } from "next/navigation";
const router = useRouter();
router.push("/courses");
router.refresh();          // re-fetch server data, keep client state
```

`Link` prefetches routes in the viewport, which is why App Router navigation
feels instant. A raw `<a>` triggers a full page load and loses all client state.

## Loading and error states

`loading.tsx` is a Suspense boundary for the segment: the shell renders
immediately while the page's data resolves. `error.tsx` is an error boundary
receiving `error` and `reset`. Both are per-segment, so a slow panel does not
block the whole page.

## Metadata

```tsx
export const metadata = { title: "Courses" };
export async function generateMetadata({ params }) { ... }   // dynamic
```
""",
            "concepts": [
                ("App Router", "Next.js routing where folders define URL segments and files define behaviour."),
                ("Layout", "A component wrapping a segment that persists across navigations within it."),
                ("Route group", "A `(name)` folder that organises files without affecting the URL."),
                ("Streaming", "Sending the shell first and filling slower parts in as they resolve."),
            ],
            "takeaways": [
                "page.tsx makes a segment routable; layout.tsx persists across its navigations",
                "loading.tsx and error.tsx are per-segment Suspense and error boundaries",
                "Use `Link` — a raw anchor forces a full reload and loses client state",
                "`router.refresh()` re-fetches server data without discarding client state",
            ],
            "resources": [
                {"kind": "doc", "title": "Next.js — Routing fundamentals", "url": "https://nextjs.org/docs/app/building-your-application/routing"},
                {"kind": "doc", "title": "Next.js — Pages and layouts", "url": "https://nextjs.org/docs/app/api-reference/file-conventions/layout"},
            ],
            "video": {"query": "next.js app router tutorial layouts routing", "title": "Next.js App Router"},
        },
        {
            "id": "server-components",
            "title": "Server & client components",
            "topic": "Server Components",
            "summary": "The default is the server; `use client` is a boundary, not a file-level switch.",
            "minutes": 17,
            "body": """
## Two kinds of component

In the App Router, components are **server components by default**. They run
only on the server, never ship their code to the browser, and can be `async`.

```tsx
// app/courses/page.tsx — a server component
export default async function Courses() {
  const courses = await db.course.findMany();   // direct data access, no API call
  return <CourseGrid courses={courses} />;
}
```

`"use client"` marks the boundary where the client bundle begins. Everything
imported below that point is client code.

```tsx
"use client";
import { useState } from "react";
export function Filter() {
  const [query, setQuery] = useState("");
  ...
}
```

## Which to use

| Needs | Component type |
|---|---|
| Data fetching, secrets, large dependencies | Server |
| `useState`, `useEffect`, event handlers | Client |
| Browser APIs (`window`, `localStorage`) | Client |
| Rendering static content | Server |

The rule that matters: **push `use client` as far down the tree as possible.**
One `"use client"` at the top of a page drags the entire subtree into the
browser bundle.

## Composing them

A client component cannot import a server component — but it can *receive* one
as `children`:

```tsx
// server component
<InteractiveShell>          {/* client */}
  <HeavyServerContent />    {/* still rendered on the server */}
</InteractiveShell>
```

Props crossing the boundary must be serialisable: no functions, no class
instances, no Dates in older versions. Server functions are the exception —
they cross as references.

## Data fetching and caching

```tsx
const res = await fetch(url, { next: { revalidate: 60, tags: ["courses"] } });
const fresh = await fetch(url, { cache: "no-store" });   // always dynamic
```

Fetch results can be cached and revalidated by time or by tag. `revalidateTag`
and `revalidatePath` invalidate on demand — typically from a server action after
a write.

## Server actions

```tsx
"use server";
export async function completeChapter(courseId: string, chapterId: string) {
  await db.progress.upsert({ ... });
  revalidatePath(`/courses/${courseId}`);
}
```

A mutation callable from a client component without hand-writing an API route.
It is a public endpoint: validate its arguments and check authorisation inside
it, exactly as you would for a route handler.
""",
            "concepts": [
                ("Server component", "A component that runs only on the server and ships no JavaScript."),
                ("Client boundary", "The `use client` directive marking where the browser bundle begins."),
                ("Server action", "A server function callable from the client, used for mutations."),
                ("Revalidation", "Invalidating cached data by time, tag or path."),
            ],
            "takeaways": [
                "Server components are the default and ship no client JavaScript",
                "Push `use client` down the tree — it pulls everything below it into the bundle",
                "Client components can render server components passed as children",
                "Server actions are public endpoints: validate and authorise inside them",
            ],
            "resources": [
                {"kind": "doc", "title": "Next.js — Server and client components", "url": "https://nextjs.org/docs/app/building-your-application/rendering/composition-patterns"},
                {"kind": "doc", "title": "Next.js — Data fetching and caching", "url": "https://nextjs.org/docs/app/building-your-application/data-fetching"},
            ],
            "video": {"query": "next.js server components client components explained", "title": "Server vs client components"},
        },
        {
            "id": "patterns",
            "title": "Forms, data and production patterns",
            "topic": "Patterns",
            "summary": "Optimistic updates, error boundaries, accessibility and what to check before shipping.",
            "minutes": 15,
            "body": """
## Forms

```tsx
"use client";
const [state, formAction, pending] = useActionState(submitForm, initialState);

<form action={formAction}>
  <input name="email" type="email" required />
  <button disabled={pending}>{pending ? "Saving…" : "Save"}</button>
</form>
```

Use the platform: native validation, real `name` attributes, and a form that
works before JavaScript loads. Validate on the server regardless of what the
client checked — client validation is UX, not security.

## Optimistic updates

```tsx
const [optimistic, addOptimistic] = useOptimistic(items, (state, next) => [...state, next]);

async function onAdd(item) {
  addOptimistic(item);          // paints immediately
  await save(item);             // reconciles (or reverts) when it resolves
}
```

Optimistic UI is what makes an app feel instant. The rule: apply it where
failure is rare and reversible, and always show the reverted state honestly if
the write fails.

## Error boundaries

```tsx
"use client";
export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div role="alert">
      <p>Something went wrong.</p>
      <button onClick={reset}>Try again</button>
    </div>
  );
}
```

Put boundaries around independently-failing regions so one broken panel does not
blank the page.

## Accessibility, the parts always missed

- Every interactive element is a `<button>` or `<a>` — not a `div` with onClick.
- Labels are associated with inputs (`htmlFor` / `id`).
- Focus is visible and managed after navigation or dialog open/close.
- Loading and error regions use `aria-live` so screen readers announce them.
- Colour is never the only signal.

## A pre-ship checklist

1. No `key={index}` on a reorderable list.
2. No `any` at the data boundary — validate API responses.
3. Every effect cleans up.
4. Loading, empty and error states exist for every async view.
5. Images use `next/image` with explicit dimensions.
6. The bundle has been looked at at least once.
""",
            "concepts": [
                ("Optimistic update", "Rendering the expected result before the server confirms it."),
                ("Error boundary", "A component that catches render errors in its subtree and shows a fallback."),
                ("Progressive enhancement", "Building so the core flow works before client JavaScript loads."),
            ],
            "takeaways": [
                "Client-side validation is UX; the server must validate too",
                "Optimistic updates suit rare, reversible failures — and must revert visibly",
                "Scope error boundaries so one failure does not blank the page",
                "Every async view needs loading, empty and error states",
            ],
            "resources": [
                {"kind": "doc", "title": "React — useOptimistic", "url": "https://react.dev/reference/react/useOptimistic"},
                {"kind": "doc", "title": "Next.js — Error handling", "url": "https://nextjs.org/docs/app/building-your-application/routing/error-handling"},
            ],
            "video": {"query": "next.js forms server actions optimistic updates tutorial", "title": "Forms and server actions"},
            "notes": """
Rule of thumb for state: URL for anything shareable or back-button-able, server
for anything persistent, React state for anything ephemeral. Most "we need a
global store" conversations end when those three are used properly.
""",
        },
    ],
    "exams": [
        {
            "id": "react-exam-1",
            "title": "React — Core Assessment",
            "description": "Covers chapters 1–4: components, state, effects, hooks.",
            "chapter_ids": ["components", "state", "effects", "hooks"],
            "questions": [
                {
                    "type": "mcq", "topic": "Components", "chapter_id": "components",
                    "prompt": "Why is using an array index as a list key a problem?",
                    "options": [
                        "It is slower to compute",
                        "On reorder or insertion React keeps the wrong DOM node and component state",
                        "React forbids numeric keys",
                        "It breaks server rendering",
                    ],
                    "answer": 1,
                    "explanation": "Keys are identity. When items move, index keys tell React that a different item is the same one.",
                },
                {
                    "type": "code", "topic": "Components", "chapter_id": "components", "language": "tsx",
                    "prompt": "What renders when `count` is 0?",
                    "code": "{count && <Badge value={count} />}",
                    "options": ["Nothing", "A literal 0 in the DOM", "The Badge with value 0", "A React warning only"],
                    "answer": 1,
                    "explanation": "`0` is falsy but is still a valid React node, so it is rendered. Use `count > 0 && ...`.",
                },
                {
                    "type": "code", "topic": "State", "chapter_id": "state", "language": "tsx",
                    "prompt": "Why does the list not update?",
                    "code": "items.push(newItem);\nsetItems(items);",
                    "options": [
                        "push is asynchronous",
                        "The array reference is unchanged, so React sees no change",
                        "setItems requires an updater function",
                        "newItem needs a key",
                    ],
                    "answer": 1,
                    "explanation": "React compares by reference. Mutating in place leaves the identity the same. Use `setItems([...items, newItem])`.",
                },
                {
                    "type": "mcq", "topic": "State", "chapter_id": "state",
                    "prompt": "When must you use the updater form `setCount(c => c + 1)`?",
                    "options": [
                        "Always — the direct form is deprecated",
                        "When the new value depends on the previous one, especially across batched updates",
                        "Only inside useEffect",
                        "Only with objects",
                    ],
                    "answer": 1,
                    "explanation": "The direct form closes over the value from this render; in a batch of updates that value is stale.",
                },
                {
                    "type": "scenario", "topic": "State", "chapter_id": "state",
                    "prompt": "A component stores `items` and also `itemCount` in state, and the count sometimes disagrees with the list. What is the fix?",
                    "options": [
                        "Add an effect that syncs itemCount whenever items changes",
                        "Delete itemCount and compute `items.length` during render",
                        "Wrap both in useReducer",
                        "Memoise the component",
                    ],
                    "answer": 1,
                    "explanation": "Derived values should be computed, not stored. Two sources of truth will drift.",
                },
                {
                    "type": "mcq", "topic": "Effects", "chapter_id": "effects",
                    "prompt": "What is the dependency array of useEffect actually a list of?",
                    "options": [
                        "The events that should trigger the effect",
                        "Every value from the render scope the effect reads",
                        "The state setters the effect calls",
                        "Components that should re-render",
                    ],
                    "answer": 1,
                    "explanation": "Omitting a dependency produces a stale closure — the effect keeps values from the render that created it.",
                },
                {
                    "type": "code", "topic": "Effects", "chapter_id": "effects", "language": "tsx",
                    "prompt": "What does the `cancelled` flag prevent?",
                    "code": "useEffect(() => {\n  let cancelled = false;\n  fetchUser(id).then((d) => { if (!cancelled) setUser(d); });\n  return () => { cancelled = true; };\n}, [id]);",
                    "options": [
                        "Memory leaks from the fetch itself",
                        "A stale response for an old id overwriting the current user",
                        "Duplicate network requests",
                        "The effect from running twice in Strict Mode",
                    ],
                    "answer": 1,
                    "explanation": "Responses can arrive out of order. The cleanup marks the old request irrelevant before the new one starts.",
                },
                {
                    "type": "scenario", "topic": "Effects", "chapter_id": "effects",
                    "prompt": "A component must reset its internal state whenever the selected user changes. What is the idiomatic solution?",
                    "options": [
                        "An effect that calls setState when the userId prop changes",
                        "Give the component `key={userId}` so React remounts it",
                        "Move the state to context",
                        "Use useRef instead of useState",
                    ],
                    "answer": 1,
                    "explanation": "Changing the key remounts with fresh state — no effect, no intermediate render with stale values.",
                },
                {
                    "type": "truefalse", "topic": "Hooks", "chapter_id": "hooks",
                    "prompt": "It is safe to call a hook inside an `if` block as long as the condition is stable.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. Hooks are matched by call order across renders. Conditional hooks shift every later hook onto the wrong slot.",
                },
                {
                    "type": "mcq", "topic": "Hooks", "chapter_id": "hooks",
                    "prompt": "What do two components calling the same custom hook share?",
                    "options": [
                        "The same state instance",
                        "Only the logic — each gets its own state",
                        "The same effect cleanup",
                        "A shared context value",
                    ],
                    "answer": 1,
                    "explanation": "Custom hooks package logic. State created inside is per-caller, like any other hook call.",
                },
            ],
        },
        {
            "id": "react-exam-2",
            "title": "React & Next.js — Applied Assessment",
            "description": "Covers chapters 5–8: performance, App Router, server components, production patterns.",
            "chapter_ids": ["performance", "nextjs-routing", "server-components", "patterns"],
            "questions": [
                {
                    "type": "mcq", "topic": "Performance", "chapter_id": "performance",
                    "prompt": "Why does wrapping a child in `memo()` often change nothing?",
                    "options": [
                        "memo only works on server components",
                        "Inline objects or arrow-function props create a new reference every render, so the shallow comparison always fails",
                        "memo requires a key prop",
                        "memo is disabled in development",
                    ],
                    "answer": 1,
                    "explanation": "memo compares props shallowly. Without useCallback/useMemo on the props, identity changes every render.",
                },
                {
                    "type": "scenario", "topic": "Performance", "chapter_id": "performance",
                    "prompt": "Typing in a search box re-renders a heavy dashboard on every keystroke. Which fix removes the work rather than skipping it?",
                    "options": [
                        "Wrap every dashboard child in memo",
                        "Move the search state into the search component so the dashboard is not part of that render",
                        "Increase the debounce to 1000ms",
                        "Switch to useReducer",
                    ],
                    "answer": 1,
                    "explanation": "Moving state down means the dashboard never re-renders. Memoisation only skips work that was still scheduled.",
                },
                {
                    "type": "truefalse", "topic": "Performance", "chapter_id": "performance",
                    "prompt": "Every consumer of a React context re-renders when the provider's value identity changes.",
                    "options": ["True", "False"],
                    "answer": 0,
                    "explanation": "True — which is why a fast-changing value should live in its own context and why provider values are usually memoised.",
                },
                {
                    "type": "mcq", "topic": "Next.js Routing", "chapter_id": "nextjs-routing",
                    "prompt": "What does `loading.tsx` provide for a route segment?",
                    "options": [
                        "A redirect while data loads",
                        "A Suspense fallback so the shell renders while that segment's data resolves",
                        "A cache configuration",
                        "A client-side spinner component you must import",
                    ],
                    "answer": 1,
                    "explanation": "It is a per-segment Suspense boundary, which is what enables streaming: fast parts paint while slow ones resolve.",
                },
                {
                    "type": "truefalse", "topic": "Next.js Routing", "chapter_id": "nextjs-routing",
                    "prompt": "A layout re-mounts on every navigation within its segment.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. Layouts persist across navigations inside their segment, which is what preserves scroll position and layout state.",
                },
                {
                    "type": "mcq", "topic": "Server Components", "chapter_id": "server-components",
                    "prompt": "What is the effect of adding `\"use client\"` at the top of a page component?",
                    "options": [
                        "Only that file becomes a client component",
                        "That component and everything it imports become client code in the browser bundle",
                        "It disables server rendering entirely",
                        "It enables server actions",
                    ],
                    "answer": 1,
                    "explanation": "`use client` marks a boundary. Everything below it ships to the browser — which is why it belongs as far down the tree as possible.",
                },
                {
                    "type": "scenario", "topic": "Server Components", "chapter_id": "server-components",
                    "prompt": "You need an interactive wrapper around content that is expensive to render on the server. How do you keep the content on the server?",
                    "options": [
                        "Import the server component inside the client component",
                        "Render the server component in a parent and pass it as `children` to the client wrapper",
                        "Add `use server` to the client component",
                        "It is impossible — anything inside a client component is client code",
                    ],
                    "answer": 1,
                    "explanation": "A client component cannot import a server component, but it can receive one as children — the parent renders it on the server.",
                },
                {
                    "type": "code", "topic": "Server Components", "chapter_id": "server-components", "language": "tsx",
                    "prompt": "What does this fetch option do?",
                    "code": "const res = await fetch(url, { next: { revalidate: 60, tags: ['courses'] } });",
                    "options": [
                        "Retries the request every 60 seconds",
                        "Caches the response for 60 seconds and allows on-demand invalidation via the 'courses' tag",
                        "Times the request out after 60 seconds",
                        "Disables caching",
                    ],
                    "answer": 1,
                    "explanation": "Time-based revalidation plus a tag that `revalidateTag('courses')` can invalidate after a write.",
                },
                {
                    "type": "mcq", "topic": "Patterns", "chapter_id": "patterns",
                    "prompt": "Client-side form validation is best described as:",
                    "options": [
                        "A security control that can replace server validation",
                        "A UX improvement — the server must still validate",
                        "Required only for file uploads",
                        "Unnecessary when using server actions",
                    ],
                    "answer": 1,
                    "explanation": "Anything the client enforces can be bypassed. Server actions and route handlers are public endpoints.",
                },
                {
                    "type": "scenario", "topic": "Patterns", "chapter_id": "patterns",
                    "prompt": "An optimistic 'mark complete' toggle shows success, but the request fails silently and the UI stays wrong until reload. What is missing?",
                    "options": [
                        "A longer optimistic timeout",
                        "Reverting to the server state on failure and surfacing the error",
                        "Moving the state into context",
                        "Memoising the toggle",
                    ],
                    "answer": 1,
                    "explanation": "Optimistic UI is a promise about the likely case. When the write fails the UI must revert visibly and say so.",
                },
            ],
        },
    ],
}
