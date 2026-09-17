# The session sandbox image

Every Code, Chat and Agents session runs its shell commands in an E2B
sandbox. This directory is the one place that decides what that sandbox
contains beyond E2B's stock base image.

## Why a template

Before this existed, the only tool that needed something the base image
lacked — `lint_code`, which wants `ruff` — installed it at runtime with a
`pip install` inside the user's live sandbox the first time it ran. That is
the kind of change this directory forbids: it altered what a session could do
depending on which tool happened to run first, it did nothing in a sandbox
without network access, and nobody could reproduce it from the repository.

Now:

* **`e2b.Dockerfile`** is the image. Anything a tool needs is installed there,
  pinned. It is the only file that may put software into a sandbox.
* **`e2b.toml`** names the template and points at the Dockerfile.
* **`package.json`** pins the E2B CLI that builds it, so two people building
  the template get the same tool.
* **`E2B_TEMPLATE`** in `backend/.env` selects the built template. Unset, the
  backend uses E2B's base image and every tool that needs something from
  here reports it as missing — honestly, with a `degraded` flag — rather than
  installing it.
* **`SANDBOX_RUNTIME_LINTER_INSTALL`** is the escape hatch for a deployment
  that cannot build a template. It is off by default and documented as the
  thing the template exists to replace.

## Building it

```bash
cd backend/sandbox
npm ci                    # installs the pinned @e2b/cli into ./node_modules
npx e2b auth login        # once per machine
npx e2b template build --name loom-sandbox
```

Docker must be running. The build produces a template id; the CLI writes it
into `e2b.toml`, and that change should be committed so the next build updates
the same template. Then:

```
# backend/.env
E2B_TEMPLATE=loom-sandbox
```

and restart the backend. New sandboxes are created from the template; nothing
already running is touched.

## Checking a sandbox is on the template

From the Code section's terminal panel:

```
ruff --version
```

On the template it prints the pinned version. On the base image it prints
`command not found`, and Agent 10's `lint_code` reports its Python lint as a
syntax-only check with `degraded: true` — which is the correct answer for an
image that has no linter, and the reason the template is worth building.

## Adding a tool

1. Add the install to `e2b.Dockerfile`, pinned to a version.
2. If the same tool is used by the people working on this repository (as
   `ruff` is), pin the same version in `backend/requirements-dev.txt`.
3. Rebuild and bump nothing else — the backend reads the template by name.

Do not add a `pip install` or `npm install` to a tool's code path. If a tool
needs something the image lacks, it should say so in its result, the way
`lint_code` does.
