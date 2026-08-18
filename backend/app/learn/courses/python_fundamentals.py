"""Python Fundamentals."""

from __future__ import annotations

COURSE = {
    "id": "python",
    "title": "Python Fundamentals",
    "short_title": "Python",
    "subtitle": "The language, properly — from names to modules",
    "difficulty": "beginner",
    "tags": ["Language", "Backend"],
    "description": (
        "Python is easy to start and easy to misunderstand. This course builds "
        "the model that stops the surprises: what a variable actually is, how "
        "mutability decides half of your bugs, why comprehensions read better "
        "than loops, how exceptions and context managers keep resources safe, "
        "and how modules and virtual environments fit together."
    ),
    "objectives": [
        "Explain Python's name-and-object model and its consequences",
        "Choose the right built-in data structure for a task",
        "Write functions with safe defaults, *args/**kwargs and type hints",
        "Use comprehensions, generators and iterators idiomatically",
        "Handle errors with specific exceptions and context managers",
        "Organise code into modules, packages and virtual environments",
    ],
    "resources": [
        {"kind": "doc", "title": "The Python Tutorial (official)", "url": "https://docs.python.org/3/tutorial/"},
        {"kind": "doc", "title": "W3Schools — Python tutorial", "url": "https://www.w3schools.com/python/"},
        {"kind": "book", "title": "OpenStax — Introduction to Python Programming", "url": "https://openstax.org/details/books/introduction-python-programming"},
    ],
    "chapters": [
        {
            "id": "names-objects",
            "title": "Names, objects and types",
            "topic": "Core Model",
            "summary": "Variables are labels, not boxes — and everything downstream follows from that.",
            "minutes": 15,
            "body": """
## A variable is a name bound to an object

In C, a variable is a box holding a value. In Python it is a **label attached
to an object**. Assignment never copies; it rebinds a name.

```python
a = [1, 2, 3]
b = a          # b labels the SAME list
b.append(4)
print(a)       # [1, 2, 3, 4]
```

If that surprises you, most Python bugs will. Use `is` to ask "same object?"
and `==` to ask "same value?".

```python
x = [1, 2]; y = [1, 2]
x == y     # True  — equal values
x is y     # False — two objects
```

## Dynamic, but strongly typed

Names have no type; objects do. And Python will not silently coerce across
types:

```python
"3" + 4        # TypeError — no implicit conversion
int("3") + 4   # 7
```

## The types you use every day

| Type | Mutable | Ordered | Notes |
|---|---|---|---|
| `int`, `float`, `bool` | no | – | `bool` is a subclass of `int` |
| `str` | no | yes | every "modification" makes a new string |
| `list` | yes | yes | the default sequence |
| `tuple` | no | yes | fixed record; hashable if its items are |
| `dict` | yes | yes (insertion) | the workhorse |
| `set` | yes | no | membership and de-duplication |

## Truthiness

Empty containers, `0`, `""` and `None` are falsy; almost everything else is
truthy. That enables `if items:` — but note the trap:

```python
def f(count=None):
    if not count:        # 0 takes this branch too!
        count = 10
    # correct:
    if count is None:
        count = 10
```

Use `is None` when `None` is the thing you mean.
""",
            "concepts": [
                ("Name binding", "Assignment attaches a name to an object; it never copies the object."),
                ("Mutability", "Whether an object can change in place. Lists, dicts and sets can; strings and tuples cannot."),
                ("Truthiness", "The implicit boolean value of an object — empty containers, 0, '' and None are falsy."),
            ],
            "takeaways": [
                "Assignment rebinds a name; two names can label one object",
                "`is` compares identity, `==` compares value",
                "Python is dynamically but strongly typed — no silent coercion",
                "Use `is None` rather than truthiness when None is what you mean",
            ],
            "resources": [
                {"kind": "doc", "title": "Python Tutorial — An informal introduction", "url": "https://docs.python.org/3/tutorial/introduction.html"},
                {"kind": "doc", "title": "W3Schools — Python variables", "url": "https://www.w3schools.com/python/python_variables.asp"},
            ],
            "video": {"id": "rfscVS0vtbw", "title": "Learn Python — Full Course for Beginners", "channel": "freeCodeCamp"},
        },
        {
            "id": "data-structures",
            "title": "Lists, dicts, sets and tuples",
            "topic": "Data Structures",
            "summary": "Picking the structure that makes the operation you repeat cheap.",
            "minutes": 17,
            "body": """
## Choose by the operation you repeat

| Need | Use | Cost |
|---|---|---|
| Ordered sequence, append often | `list` | append O(1), `in` O(n) |
| Lookup by key | `dict` | get/set O(1) |
| Membership, de-duplication | `set` | `in` O(1) |
| Fixed record, dict key | `tuple` | immutable, hashable |

The most common performance mistake in Python is `if x in big_list` inside a
loop. Converting to a set turns O(n²) into O(n).

## Slicing

```python
xs = [0, 1, 2, 3, 4, 5]
xs[1:4]    # [1, 2, 3]      start inclusive, stop exclusive
xs[::2]    # [0, 2, 4]      step
xs[::-1]   # [5, 4, 3, 2, 1, 0]   reversed copy
```

A slice of a list is a **shallow copy** — a new list holding the same objects.

## Dictionaries

```python
counts = {}
counts["a"] = counts.get("a", 0) + 1        # safe default

from collections import defaultdict, Counter
counts = defaultdict(int); counts["a"] += 1
Counter("mississippi").most_common(2)       # [('i', 4), ('s', 4)]

merged = {**defaults, **overrides}          # right wins
for key, value in d.items(): ...
```

Dicts preserve insertion order (guaranteed since 3.7).

## Copying

```python
import copy
shallow = original[:]              # or list(original), dict(original)
deep    = copy.deepcopy(original)  # nested structures too
```

A shallow copy protects the outer container only. If it holds mutable objects,
both copies still share them.

## Unpacking

```python
first, *rest = [1, 2, 3, 4]      # 1, [2, 3, 4]
a, b = b, a                      # swap, no temp
name, age = ("Ada", 36)
```
""",
            "concepts": [
                ("Shallow copy", "A new container holding references to the same inner objects."),
                ("Hashable", "An object usable as a dict key or set member — requires immutability."),
                ("defaultdict", "A dict that creates a default value on first access to a missing key."),
            ],
            "takeaways": [
                "Membership in a set is O(1); in a list it is O(n)",
                "Slices copy shallowly — inner objects stay shared",
                "Dicts keep insertion order and are the default aggregation tool",
                "Only immutable objects can be dict keys or set members",
            ],
            "resources": [
                {"kind": "doc", "title": "Python Tutorial — Data structures", "url": "https://docs.python.org/3/tutorial/datastructures.html"},
                {"kind": "doc", "title": "W3Schools — Python dictionaries", "url": "https://www.w3schools.com/python/python_dictionaries.asp"},
            ],
            "video": {"query": "python data structures lists dicts sets tutorial", "title": "Python data structures"},
        },
        {
            "id": "control-flow",
            "title": "Control flow",
            "topic": "Control Flow",
            "summary": "Loops that read as intent, and the match statement.",
            "minutes": 13,
            "body": """
## Iterate over things, not indices

```python
for item in items: ...                     # the default
for i, item in enumerate(items, start=1): ...
for name, score in zip(names, scores): ...
for key, value in mapping.items(): ...
```

Reaching for `range(len(items))` is almost always a sign that one of the four
above fits better.

## `for ... else`

The `else` runs when the loop finished **without** `break` — useful for search:

```python
for row in rows:
    if row.id == target:
        break
else:
    raise LookupError(target)
```

## Conditional expressions

```python
label = "even" if n % 2 == 0 else "odd"
value = maybe or "fallback"        # careful: 0 and "" are falsy
value = maybe if maybe is not None else "fallback"
```

## Structural pattern matching (3.10+)

`match` is not a switch — it destructures.

```python
match event:
    case {"type": "click", "pos": (x, y)}:
        handle_click(x, y)
    case {"type": "key", "key": str(k)} if k.isalpha():
        handle_letter(k)
    case _:
        ignore(event)
```

Bare names in a pattern **bind**, they do not compare. `case status:` matches
everything and assigns; to compare against a constant use a dotted name such as
`case Status.OPEN:`.

## Walrus

```python
while (line := f.readline()):
    process(line)
```

Assign inside an expression. Use sparingly — it earns its place in loop
conditions and comprehension filters, and hurts readability elsewhere.
""",
            "concepts": [
                ("enumerate", "Yields index/value pairs so you never need range(len(x))."),
                ("for/else", "An else clause that runs only if the loop was not broken out of."),
                ("Structural pattern matching", "match/case, which destructures and binds rather than merely comparing."),
            ],
            "takeaways": [
                "Iterate over objects; enumerate and zip cover the index cases",
                "`for/else` expresses 'searched and found nothing' cleanly",
                "In `match`, a bare name binds — it does not compare",
                "The walrus operator belongs in loop conditions, not everywhere",
            ],
            "resources": [
                {"kind": "doc", "title": "Python Tutorial — Control flow", "url": "https://docs.python.org/3/tutorial/controlflow.html"},
                {"kind": "doc", "title": "W3Schools — Python for loops", "url": "https://www.w3schools.com/python/python_for_loops.asp"},
            ],
            "video": {"query": "python control flow match case tutorial", "title": "Control flow in Python"},
        },
        {
            "id": "functions",
            "title": "Functions, scope and type hints",
            "topic": "Functions",
            "summary": "Arguments, the mutable-default trap, closures and typing.",
            "minutes": 17,
            "body": """
## Parameters

```python
def report(title, *rows, sep=" | ", upper=False, **meta):
    ...
```

- `*rows` collects extra positional arguments into a tuple.
- Anything after `*rows` is **keyword-only** — a good way to stop call sites
  from passing booleans positionally.
- `**meta` collects extra keyword arguments into a dict.
- `def f(a, b, /, c)` makes `a` and `b` positional-only.

## The mutable default trap

Defaults are evaluated **once**, when the function is defined:

```python
def add(item, bucket=[]):     # WRONG — one list shared by every call
    bucket.append(item)
    return bucket

def add(item, bucket=None):   # right
    bucket = [] if bucket is None else bucket
    bucket.append(item)
    return bucket
```

## Scope: LEGB

Local, Enclosing, Global, Built-in — searched in that order. Assigning to a
name anywhere in a function makes it local for the whole function.

```python
def counter():
    n = 0
    def tick():
        nonlocal n     # without this, `n = n + 1` would be a local
        n += 1
        return n
    return tick        # a closure over n
```

## Type hints

Hints are not enforced at runtime; they are for readers and for tools like
mypy and pyright.

```python
from collections.abc import Iterable, Callable

def top(scores: dict[str, int], n: int = 3) -> list[tuple[str, int]]:
    return sorted(scores.items(), key=lambda kv: -kv[1])[:n]

def apply(items: Iterable[int], fn: Callable[[int], int]) -> list[int]:
    return [fn(i) for i in items]

def find(key: str) -> str | None: ...
```

Hint the boundaries — public functions, dataclasses, module APIs. Hinting every
local is noise.

## Docstrings

The first statement in a function, module or class. `help()` and every IDE read
it; a comment above the `def` is invisible to both.
""",
            "concepts": [
                ("Keyword-only argument", "A parameter after *args that must be passed by name."),
                ("Mutable default", "A default argument evaluated once at definition — shared across all calls."),
                ("Closure", "A nested function capturing names from its enclosing scope."),
                ("LEGB", "The name-resolution order: Local, Enclosing, Global, Built-in."),
            ],
            "takeaways": [
                "Never use a mutable object as a default — use None and build inside",
                "Keyword-only parameters stop opaque positional booleans",
                "`nonlocal` rebinds an enclosing name; `global` rebinds a module-level one",
                "Type hints document boundaries and power static checking; they are not runtime checks",
            ],
            "resources": [
                {"kind": "doc", "title": "Python Tutorial — Defining functions", "url": "https://docs.python.org/3/tutorial/controlflow.html#defining-functions"},
                {"kind": "doc", "title": "Python docs — typing", "url": "https://docs.python.org/3/library/typing.html"},
            ],
            "video": {"query": "python functions args kwargs type hints tutorial", "title": "Functions and type hints"},
        },
        {
            "id": "comprehensions",
            "title": "Comprehensions, generators and iterators",
            "topic": "Iteration",
            "summary": "Expressing a transform as one expression, and streaming data you cannot hold.",
            "minutes": 16,
            "body": """
## Comprehensions

```python
squares  = [x * x for x in range(10)]
evens    = [x for x in xs if x % 2 == 0]
by_id    = {u.id: u for u in users}
domains  = {e.split("@")[1] for e in emails}
pairs    = [(x, y) for x in xs for y in ys if x != y]
```

Read left to right: *output expression*, then the loops, then the filters. If a
comprehension needs more than two clauses or a nested conditional, write the
loop — clarity beats compactness.

## Generators

Swap the brackets for parentheses and nothing is materialised:

```python
total = sum(x * x for x in range(1_000_000))   # constant memory
```

A `yield` in a function makes it a generator function. Calling it returns a
generator; the body runs only as items are pulled.

```python
def read_lines(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            yield line.rstrip("\\n")

for line in read_lines("huge.log"):   # one line in memory at a time
    ...
```

Generators are the idiomatic way to process a file larger than RAM, or a stream
with no end.

## Iterators, underneath

`for` calls `iter(obj)` to get an iterator, then `next()` until `StopIteration`.
Anything implementing `__iter__` and `__next__` works in a `for`.

```python
class Countdown:
    def __init__(self, n): self.n = n
    def __iter__(self): return self
    def __next__(self):
        if self.n <= 0: raise StopIteration
        self.n -= 1
        return self.n + 1
```

## itertools

```python
from itertools import islice, chain, groupby, count

first_ten = list(islice(infinite_stream, 10))
everything = chain(list_a, list_b)
```

## The one caveat

A generator is **single-pass**. Once consumed it is empty. If you need the data
twice, materialise it with `list()` — deliberately.
""",
            "concepts": [
                ("Comprehension", "A single expression that builds a list, dict or set from an iterable."),
                ("Generator", "A lazily evaluated iterator produced by `yield` or a generator expression."),
                ("Iterator protocol", "`__iter__` and `__next__`, raising StopIteration when exhausted."),
                ("Lazy evaluation", "Producing values on demand rather than building the whole result."),
            ],
            "takeaways": [
                "Comprehensions express a transform as one expression — but do not nest them deeply",
                "Generator expressions use constant memory; list comprehensions materialise",
                "`yield` turns a function into a stream, ideal for large files",
                "Generators are single-pass — consuming one empties it",
            ],
            "resources": [
                {"kind": "doc", "title": "Python Tutorial — List comprehensions", "url": "https://docs.python.org/3/tutorial/datastructures.html#list-comprehensions"},
                {"kind": "doc", "title": "Python docs — itertools", "url": "https://docs.python.org/3/library/itertools.html"},
            ],
            "video": {"query": "python generators yield comprehensions tutorial", "title": "Generators and comprehensions"},
        },
        {
            "id": "errors",
            "title": "Errors, exceptions and context managers",
            "topic": "Errors",
            "summary": "Failing precisely, and releasing resources whatever happens.",
            "minutes": 15,
            "body": """
## Catch what you can handle

```python
try:
    config = json.loads(raw)
except json.JSONDecodeError as exc:
    log.warning("bad config: %s", exc)
    config = DEFAULTS
else:
    log.info("config loaded")       # runs only if no exception
finally:
    handle.close()                  # always runs
```

`except Exception:` around a whole function hides bugs — it will swallow the
typo alongside the network error. Catch the specific class, at the smallest
scope where you can actually do something about it.

Never write a bare `except:` — it also catches `KeyboardInterrupt` and
`SystemExit`.

## EAFP

Python's idiom is "easier to ask forgiveness than permission":

```python
# LBYL — checks then acts, and races between the two
if os.path.exists(path):
    with open(path) as f: ...

# EAFP — idiomatic
try:
    with open(path) as f: ...
except FileNotFoundError:
    ...
```

## Raising well

```python
class ConfigError(Exception):
    \"\"\"Raised when configuration is present but unusable.\"\"\"

raise ConfigError(f"missing key: {key!r}")

try:
    parse(raw)
except ValueError as exc:
    raise ConfigError("could not parse config") from exc   # keeps the cause
```

Custom exception types let callers handle *your* failures without catching
everything. `raise ... from exc` preserves the chain.

## Context managers

`with` guarantees cleanup, including on an exception:

```python
from contextlib import contextmanager
import time

@contextmanager
def timed(label):
    start = time.perf_counter()
    try:
        yield
    finally:
        log.info("%s took %.1fms", label, (time.perf_counter() - start) * 1000)

with timed("index"):
    build_index()
```

Anything with acquire/release semantics — files, locks, transactions, sockets,
temporary directories — should be a context manager rather than a pair of calls
someone will forget to balance.
""",
            "concepts": [
                ("EAFP", "Try the operation and handle the exception, rather than checking first."),
                ("Exception chaining", "`raise X from exc`, preserving the original cause in the traceback."),
                ("Context manager", "An object implementing __enter__/__exit__ so `with` guarantees cleanup."),
            ],
            "takeaways": [
                "Catch specific exceptions at the smallest scope that can act on them",
                "Never use a bare `except:` — it swallows KeyboardInterrupt",
                "`finally` always runs; `else` runs only when nothing was raised",
                "Anything acquired must be released — make it a context manager",
            ],
            "resources": [
                {"kind": "doc", "title": "Python Tutorial — Errors and exceptions", "url": "https://docs.python.org/3/tutorial/errors.html"},
                {"kind": "doc", "title": "W3Schools — Python try/except", "url": "https://www.w3schools.com/python/python_try_except.asp"},
            ],
            "video": {"query": "python exceptions context managers with statement tutorial", "title": "Exceptions and context managers"},
        },
        {
            "id": "classes",
            "title": "Classes and dataclasses",
            "topic": "Classes",
            "summary": "Objects when you need them — and the lighter tools that usually suffice.",
            "minutes": 16,
            "body": """
## A class

```python
class Account:
    interest_rate = 0.02              # class attribute — shared

    def __init__(self, owner: str, balance: float = 0.0) -> None:
        self.owner = owner            # instance attributes
        self.balance = balance

    def deposit(self, amount: float) -> None:
        if amount <= 0:
            raise ValueError("amount must be positive")
        self.balance += amount

    def __repr__(self) -> str:
        return f"Account({self.owner!r}, {self.balance})"
```

`self` is explicit because a method is just a function whose first argument is
the instance.

## Dataclasses

Most classes are records. `@dataclass` writes `__init__`, `__repr__` and
`__eq__` for you:

```python
from dataclasses import dataclass, field

@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float
    tags: list[str] = field(default_factory=list)   # not `= []`
```

`frozen=True` makes instances immutable and hashable; `slots=True` cuts memory
and blocks accidental attribute typos. The mutable-default rule applies here
too — hence `default_factory`.

## Properties

Start with a plain attribute. Promote to a property only when you need
computation or validation, without changing any call site:

```python
class Circle:
    def __init__(self, r): self.r = r

    @property
    def area(self) -> float:
        return 3.141592653589793 * self.r ** 2
```

## Inheritance, sparingly

Prefer composition. Where you do inherit, call `super()`:

```python
class TimestampedAccount(Account):
    def __init__(self, owner, balance=0.0):
        super().__init__(owner, balance)
        self.created = datetime.now(timezone.utc)
```

Use `abc.ABC` with `@abstractmethod` to declare an interface, or — often
better — a `Protocol` for structural typing, which requires no base class at
all.

## Dunder methods

`__repr__` (debugging), `__str__` (display), `__eq__`, `__len__`, `__iter__`,
`__enter__`/`__exit__`. Implementing the right dunder is what makes your object
work with the language rather than beside it.
""",
            "concepts": [
                ("Dataclass", "A decorator generating __init__, __repr__ and __eq__ from annotated fields."),
                ("Property", "A method accessed like an attribute, allowing computation behind a plain name."),
                ("Protocol", "Structural typing — an interface satisfied by shape rather than by inheritance."),
                ("Dunder method", "A double-underscore method that hooks an object into language syntax."),
            ],
            "takeaways": [
                "Class attributes are shared; instance attributes are per-object",
                "Use @dataclass for records; frozen=True makes them immutable and hashable",
                "Use default_factory for mutable dataclass fields",
                "Prefer composition and Protocols over deep inheritance",
            ],
            "resources": [
                {"kind": "doc", "title": "Python Tutorial — Classes", "url": "https://docs.python.org/3/tutorial/classes.html"},
                {"kind": "doc", "title": "Python docs — dataclasses", "url": "https://docs.python.org/3/library/dataclasses.html"},
            ],
            "video": {"query": "python classes dataclasses object oriented tutorial", "title": "Classes and dataclasses"},
        },
        {
            "id": "modules",
            "title": "Modules, packages and environments",
            "topic": "Modules",
            "summary": "How imports resolve, how to lay out a project, and why every project gets its own venv.",
            "minutes": 15,
            "body": """
## Modules and packages

A module is a `.py` file. A package is a directory of modules. Import binds a
name in your namespace:

```python
import json                      # json.loads(...)
from pathlib import Path         # Path(...)
from . import helpers            # relative — inside a package only
```

Avoid `from module import *`: it hides where names come from and shadows
built-ins silently.

## `__name__ == "__main__"`

A module's top level runs on import. Guard anything that should only happen
when the file is executed directly:

```python
def main() -> None:
    ...

if __name__ == "__main__":
    main()
```

Without the guard, importing your script runs it.

## Layout that works

```
project/
  pyproject.toml
  src/
    myapp/
      __init__.py
      config.py
      api/
        __init__.py
        routes.py
  tests/
    test_routes.py
```

The `src/` layout stops your tests from accidentally importing the local
directory instead of the installed package — a real class of "works on my
machine".

## Virtual environments

Every project gets its own interpreter and its own dependency set:

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
pip freeze > requirements.txt
```

Never install project dependencies globally. Two projects will want two
versions of the same library, and the loser fails at import time.

## Circular imports

`a` imports `b`, `b` imports `a`, one of them explodes. Fixes, best first:
extract the shared piece into a third module; import inside the function that
needs it; or use `if TYPE_CHECKING:` when the import exists only for hints.
""",
            "concepts": [
                ("Module", "A single .py file, and the unit of import."),
                ("Virtual environment", "An isolated interpreter and dependency set for one project."),
                ("src layout", "Placing packages under src/ so tests import the installed package."),
                ("Circular import", "Two modules importing each other, breaking at import time."),
            ],
            "takeaways": [
                "Module top-level code runs on import — guard entry points with __main__",
                "Avoid `import *`; it hides origins and shadows built-ins",
                "One virtual environment per project, always",
                "Break circular imports by extracting the shared module",
            ],
            "resources": [
                {"kind": "doc", "title": "Python Tutorial — Modules", "url": "https://docs.python.org/3/tutorial/modules.html"},
                {"kind": "doc", "title": "Python docs — venv", "url": "https://docs.python.org/3/library/venv.html"},
            ],
            "video": {"query": "python modules packages virtual environments tutorial", "title": "Modules and virtual environments"},
            "notes": """
Debugging imports: `python -c "import myapp, sys; print(myapp.__file__)"` tells
you which copy you actually loaded. Nine times in ten a mysterious import bug
is a second copy on `sys.path`.
""",
        },
    ],
    "exams": [
        {
            "id": "py-exam-1",
            "title": "Python — Core Assessment",
            "description": "Covers chapters 1–4: names and objects, data structures, control flow, functions.",
            "chapter_ids": ["names-objects", "data-structures", "control-flow", "functions"],
            "questions": [
                {
                    "type": "code", "topic": "Core Model", "chapter_id": "names-objects", "language": "python",
                    "prompt": "What does this print?",
                    "code": "a = [1, 2, 3]\nb = a\nb.append(4)\nprint(a)",
                    "options": ["[1, 2, 3]", "[1, 2, 3, 4]", "[4]", "TypeError"],
                    "answer": 1,
                    "explanation": "Assignment binds a second name to the same list object. Mutating through either name is visible through both.",
                },
                {
                    "type": "mcq", "topic": "Core Model", "chapter_id": "names-objects",
                    "prompt": "What is the difference between `is` and `==`?",
                    "options": [
                        "`is` compares values, `==` compares identity",
                        "`is` compares identity (same object), `==` compares value",
                        "They are interchangeable",
                        "`is` works only on numbers",
                    ],
                    "answer": 1,
                    "explanation": "`is` asks whether two names label the same object; `==` asks whether two objects are equal.",
                },
                {
                    "type": "code", "topic": "Functions", "chapter_id": "functions", "language": "python",
                    "prompt": "What is the output of the second call?",
                    "code": "def add(item, bucket=[]):\n    bucket.append(item)\n    return bucket\n\nprint(add(1))\nprint(add(2))",
                    "options": ["[2]", "[1, 2]", "[1]", "TypeError"],
                    "answer": 1,
                    "explanation": "The default list is created once at definition time and shared by every call. Use `bucket=None` and build inside.",
                },
                {
                    "type": "mcq", "topic": "Data Structures", "chapter_id": "data-structures",
                    "prompt": "Which structure gives O(1) membership testing?",
                    "options": ["list", "tuple", "set", "str"],
                    "answer": 2,
                    "explanation": "Sets (and dict keys) hash their members. Lists and tuples scan linearly.",
                },
                {
                    "type": "scenario", "topic": "Data Structures", "chapter_id": "data-structures",
                    "prompt": "A loop over 100,000 records does `if record_id in known_ids` where `known_ids` is a list of 50,000 strings. It is far too slow. What is the fix?",
                    "options": [
                        "Sort the list first",
                        "Convert known_ids to a set",
                        "Use a tuple instead of a list",
                        "Use enumerate in the loop",
                    ],
                    "answer": 1,
                    "explanation": "`in` on a list is O(n), making the loop O(n·m). A set turns each test into O(1).",
                },
                {
                    "type": "truefalse", "topic": "Data Structures", "chapter_id": "data-structures",
                    "prompt": "A list slice such as `xs[:]` creates a deep copy of the list and everything in it.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. It is a shallow copy: a new list containing the same inner objects.",
                },
                {
                    "type": "mcq", "topic": "Control Flow", "chapter_id": "control-flow",
                    "prompt": "When does a `for ... else` block's `else` clause run?",
                    "options": [
                        "Every time the loop ends",
                        "Only when the loop completed without hitting `break`",
                        "Only when the loop body never ran",
                        "Only when an exception was raised",
                    ],
                    "answer": 1,
                    "explanation": "It expresses 'searched everything and found nothing'.",
                },
                {
                    "type": "code", "topic": "Control Flow", "chapter_id": "control-flow", "language": "python",
                    "prompt": "In this match statement, what does `case other:` do?",
                    "code": "match value:\n    case 0:\n        print('zero')\n    case other:\n        print(other)",
                    "options": [
                        "Compares value against a variable named `other`",
                        "Matches anything and binds it to the name `other`",
                        "Raises a NameError if `other` is undefined",
                        "Matches only strings",
                    ],
                    "answer": 1,
                    "explanation": "A bare name in a pattern is a capture pattern: it always matches and binds. Use a dotted name to compare against a constant.",
                },
                {
                    "type": "mcq", "topic": "Functions", "chapter_id": "functions",
                    "prompt": "What does `nonlocal` do?",
                    "options": [
                        "Makes a name global across all modules",
                        "Rebinds a name in the nearest enclosing function scope",
                        "Deletes a local variable",
                        "Marks a variable as thread-safe",
                    ],
                    "answer": 1,
                    "explanation": "Without it, assignment inside a nested function creates a new local rather than updating the closed-over name.",
                },
                {
                    "type": "truefalse", "topic": "Functions", "chapter_id": "functions",
                    "prompt": "Type hints are enforced by the Python interpreter at runtime.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. They are metadata for readers and static checkers such as mypy and pyright.",
                },
            ],
        },
        {
            "id": "py-exam-2",
            "title": "Python — Applied Assessment",
            "description": "Covers chapters 5–8: iteration, errors, classes, modules.",
            "chapter_ids": ["comprehensions", "errors", "classes", "modules"],
            "questions": [
                {
                    "type": "code", "topic": "Iteration", "chapter_id": "comprehensions", "language": "python",
                    "prompt": "How much memory does this use as `n` grows?",
                    "code": "total = sum(x * x for x in range(n))",
                    "options": [
                        "O(n) — the squares are materialised in a list",
                        "O(1) — the generator expression yields one value at a time",
                        "O(n log n)",
                        "It raises MemoryError above 10^6",
                    ],
                    "answer": 1,
                    "explanation": "Parentheses make it a generator expression: values are produced on demand and never stored.",
                },
                {
                    "type": "truefalse", "topic": "Iteration", "chapter_id": "comprehensions",
                    "prompt": "A generator can be iterated over twice and yield the same values each time.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. Generators are single-pass; once exhausted they yield nothing. Materialise with list() if you need the data twice.",
                },
                {
                    "type": "mcq", "topic": "Iteration", "chapter_id": "comprehensions",
                    "prompt": "What signals the end of iteration in the iterator protocol?",
                    "options": ["Returning None", "Raising StopIteration", "Returning an empty list", "Raising IndexError"],
                    "answer": 1,
                    "explanation": "`for` calls `next()` until StopIteration is raised, then exits the loop.",
                },
                {
                    "type": "mcq", "topic": "Errors", "chapter_id": "errors",
                    "prompt": "Why is a bare `except:` dangerous?",
                    "options": [
                        "It is slower than catching a specific class",
                        "It also catches KeyboardInterrupt and SystemExit",
                        "It cannot be combined with finally",
                        "It suppresses the traceback entirely",
                    ],
                    "answer": 1,
                    "explanation": "A bare except catches BaseException, so Ctrl-C and interpreter shutdown get swallowed along with your bug.",
                },
                {
                    "type": "code", "topic": "Errors", "chapter_id": "errors", "language": "python",
                    "prompt": "When does the `else` block run here?",
                    "code": "try:\n    value = parse(raw)\nexcept ValueError:\n    value = None\nelse:\n    log.info('parsed')\nfinally:\n    cleanup()",
                    "options": [
                        "Always, after the try block",
                        "Only when no exception was raised",
                        "Only when an exception was raised",
                        "Only when `finally` is absent",
                    ],
                    "answer": 1,
                    "explanation": "`else` is the success path; `finally` runs either way.",
                },
                {
                    "type": "scenario", "topic": "Errors", "chapter_id": "errors",
                    "prompt": "A function opens a database connection, does work, and closes it. Occasionally an exception leaves the connection open. What is the idiomatic fix?",
                    "options": [
                        "Catch Exception and close in the handler",
                        "Make the connection a context manager and use `with`",
                        "Call close() twice",
                        "Use a global connection",
                    ],
                    "answer": 1,
                    "explanation": "`with` guarantees __exit__ runs on both the success and the exception path. Anything acquired should be released this way.",
                },
                {
                    "type": "mcq", "topic": "Classes", "chapter_id": "classes",
                    "prompt": "In a dataclass, how do you give a field a mutable default such as an empty list?",
                    "options": [
                        "`tags: list[str] = []`",
                        "`tags: list[str] = field(default_factory=list)`",
                        "`tags: list[str] = None`",
                        "`tags = list()` outside the annotation",
                    ],
                    "answer": 1,
                    "explanation": "A bare `= []` would be shared across instances — dataclasses raise an error for exactly this reason. `default_factory` builds a fresh one per instance.",
                },
                {
                    "type": "truefalse", "topic": "Classes", "chapter_id": "classes",
                    "prompt": "`@dataclass(frozen=True)` makes instances immutable and hashable.",
                    "options": ["True", "False"],
                    "answer": 0,
                    "explanation": "True — which is what allows a frozen dataclass to be used as a dict key or set member.",
                },
                {
                    "type": "mcq", "topic": "Modules", "chapter_id": "modules",
                    "prompt": "What does `if __name__ == '__main__':` protect against?",
                    "options": [
                        "Syntax errors in the module",
                        "Script code running when the module is imported",
                        "Circular imports",
                        "Missing dependencies",
                    ],
                    "answer": 1,
                    "explanation": "A module's top level executes on import. The guard confines entry-point behaviour to direct execution.",
                },
                {
                    "type": "scenario", "topic": "Modules", "chapter_id": "modules",
                    "prompt": "Two projects on one machine need different versions of the same library, and installing one breaks the other. What is the fix?",
                    "options": [
                        "Install both globally and pin at import time",
                        "Give each project its own virtual environment",
                        "Rename one of the packages",
                        "Use `import *` to control resolution",
                    ],
                    "answer": 1,
                    "explanation": "A venv per project isolates interpreters and dependency sets. This is exactly the problem it exists to solve.",
                },
            ],
        },
    ],
}
