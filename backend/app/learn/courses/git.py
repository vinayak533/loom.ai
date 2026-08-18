"""Git & Version Control."""

from __future__ import annotations

COURSE = {
    "id": "git",
    "title": "Git & Version Control",
    "short_title": "Git",
    "subtitle": "The model underneath the commands",
    "difficulty": "beginner",
    "tags": ["Tooling", "Workflow"],
    "description": (
        "Git is simple once you see the object graph and stop memorising "
        "commands. This course builds that model — commits, refs, the three "
        "trees — and then covers branching, merge versus rebase, collaboration, "
        "and how to undo anything without losing work."
    ),
    "objectives": [
        "Describe Git's object model and what a branch actually is",
        "Move changes deliberately between working tree, index and HEAD",
        "Choose between merge and rebase and resolve conflicts confidently",
        "Collaborate through remotes, pull requests and review",
        "Recover from mistakes, including 'lost' commits",
        "Use tags, hooks and history search effectively",
    ],
    "resources": [
        {"kind": "book", "title": "Pro Git (free, full text)", "url": "https://git-scm.com/book/en/v2"},
        {"kind": "doc", "title": "Git reference manual", "url": "https://git-scm.com/docs"},
        {"kind": "doc", "title": "W3Schools — Git tutorial", "url": "https://www.w3schools.com/git/"},
    ],
    "chapters": [
        {
            "id": "model",
            "title": "The object model",
            "topic": "Git Model",
            "summary": "Commits are snapshots in a DAG; a branch is a movable pointer.",
            "minutes": 15,
            "body": """
## Four object types

- **blob** — file contents (no name, no path).
- **tree** — a directory: names pointing at blobs and other trees.
- **commit** — a tree, parent commit(s), author, message.
- **tag** — an annotated pointer to an object.

Every object is stored under the SHA of its content, so identical content is
stored once and any change anywhere produces a different hash all the way up.

## Commits are snapshots

Git does not store diffs. Each commit points at a complete tree; diffs are
*computed* between commits when you ask. That is why checkout is fast and why
history is a graph rather than a stack of patches.

```
A ── B ── C          (main)
       \\
        D ── E       (feature)
```

`C`'s parent is `B`. `E`'s parent is `D`. A merge commit simply has two parents.

## A branch is a pointer

```bash
cat .git/refs/heads/main       # a single 40-character SHA
```

That is all a branch is. Committing moves the pointer forward; creating a
branch writes one 41-byte file. This is why branching in Git is cheap in a way
it is not in older systems.

**HEAD** is a pointer to the current branch (usually) — or directly to a commit
in "detached HEAD" state.

## Where things live

```
working tree  →  index (staging area)  →  repository (.git)
     edit             git add                  git commit
```

Three places, three states. `git status` describes exactly which of them a file
sits between, and most confusion about Git is really confusion about these
three.

## Inspecting

```bash
git log --oneline --graph --all --decorate
git show <sha>
git cat-file -p <sha>          # the raw object
git reflog                     # where HEAD has been — your safety net
```
""",
            "concepts": [
                ("Blob/tree/commit", "Git's content-addressed object types."),
                ("DAG", "The directed acyclic graph of commits formed by parent links."),
                ("Branch", "A movable pointer to a commit — a file containing one SHA."),
                ("HEAD", "A pointer to the currently checked-out branch or commit."),
            ],
            "takeaways": [
                "Commits store complete snapshots; diffs are computed on demand",
                "A branch is a movable pointer, which is why branching is free",
                "Working tree, index and repository are three distinct states",
                "`git reflog` records where HEAD has been — it is the recovery tool",
            ],
            "resources": [
                {"kind": "book", "title": "Pro Git — Git internals", "url": "https://git-scm.com/book/en/v2/Git-Internals-Git-Objects"},
                {"kind": "doc", "title": "W3Schools — Git intro", "url": "https://www.w3schools.com/git/git_intro.asp"},
            ],
            "video": {"id": "RGOj5yH7evk", "title": "Git and GitHub for Beginners — Crash Course", "channel": "freeCodeCamp"},
        },
        {
            "id": "basics",
            "title": "Staging, committing and history",
            "topic": "Basics",
            "summary": "Using the index deliberately, and writing commits someone can read.",
            "minutes": 14,
            "body": """
## The index is a feature

The staging area lets you commit *part* of your work:

```bash
git add -p                # choose hunks interactively
git add src/api.py        # stage one file
git restore --staged f    # unstage, keep the edit
git restore f             # discard the edit  (destructive)
git diff                  # working tree vs index
git diff --staged         # index vs HEAD
```

`git add -p` is the single habit that most improves commit quality: it forces
you to read your own diff before it becomes history.

## Commit messages

```
Fix stale progress after chapter completion

The progress cache keyed on course id alone, so completing a chapter in
one course served a stale summary for another. Key on (user, course).

Fixes #412
```

Subject in the imperative, under ~50 characters, no full stop. Blank line. Body
explaining **why**, wrapped at 72. The diff already says what changed; only the
message can say why.

## Atomic commits

One logical change per commit. A commit mixing a rename, a bug fix and a
refactor cannot be reviewed, reverted or bisected. If the subject needs "and",
it is two commits.

## Reading history

```bash
git log --oneline -20
git log -p path/to/file          # history of one file, with diffs
git log -S "functionName"        # commits that added/removed that string
git log --author=vinayak --since="2 weeks ago"
git blame -w file                # who last touched each line, ignoring whitespace
git bisect start / bad / good    # binary search for the breaking commit
```

`git log -S` (the "pickaxe") and `git bisect` are the two that feel like
superpowers the first time a real bug forces you to use them.

## .gitignore

Never commit build output, dependencies, `.env` files or credentials. If a
secret does get committed, rotating it is the fix — rewriting history does not
un-publish what people already fetched.
""",
            "concepts": [
                ("Index", "The staging area holding the next commit's content."),
                ("Atomic commit", "One logical change, independently reviewable and revertible."),
                ("Pickaxe search", "`git log -S` finds commits that changed a given string."),
                ("Bisect", "Binary search through history to find the commit that introduced a bug."),
            ],
            "takeaways": [
                "`git add -p` makes you read your own diff before committing it",
                "Imperative subject, why-focused body — the diff already shows what",
                "One logical change per commit keeps revert and bisect usable",
                "A committed secret must be rotated, not just rewritten out",
            ],
            "resources": [
                {"kind": "book", "title": "Pro Git — Recording changes", "url": "https://git-scm.com/book/en/v2/Git-Basics-Recording-Changes-to-the-Repository"},
                {"kind": "doc", "title": "Git — git-bisect", "url": "https://git-scm.com/docs/git-bisect"},
            ],
            "video": {"query": "git add commit log bisect blame tutorial", "title": "Everyday Git"},
        },
        {
            "id": "branching",
            "title": "Branching and merging",
            "topic": "Branching",
            "summary": "Cheap branches, fast-forwards, and resolving conflicts without fear.",
            "minutes": 16,
            "body": """
## Branching

```bash
git switch -c feature/exams       # create and switch (modern)
git switch main
git branch -d feature/exams       # delete merged
git branch -D feature/exams       # force delete
```

`switch` and `restore` split the two jobs the overloaded `checkout` used to do.

## Merge

```bash
git switch main
git merge feature/exams
```

- **Fast-forward** — if `main` has not moved, the pointer just advances. No
  merge commit.
- **Three-way merge** — both branches moved: Git finds the merge base and
  creates a commit with two parents.

```bash
git merge --no-ff feature/exams    # always create a merge commit
git merge --squash feature/exams   # one commit, no branch history
```

`--no-ff` keeps the fact that a branch existed; squash keeps history flat. Both
are legitimate — pick one per repository and be consistent.

## Conflicts

```
<<<<<<< HEAD
const limit = 20;
=======
const limit = 50;
>>>>>>> feature/exams
```

```bash
git status                 # which files conflict
# edit, removing ALL markers
git add file
git merge --continue       # or: git merge --abort
```

Conflicts are not errors. They are Git declining to guess, which is exactly
what you want. `git diff --check` catches leftover markers before you commit
them — a mistake everyone makes once.

Reduce them structurally: short-lived branches, frequent integration, and not
letting a "small refactor" reformat a file someone else is editing.

## Strategies

- **Trunk-based** — everyone on `main`, tiny short-lived branches, feature
  flags for incomplete work. Best fit for continuous deployment.
- **GitHub flow** — branch, PR, review, merge, deploy. The common default.
- **Git flow** — long-lived develop/release/hotfix branches. Suits versioned,
  shipped software; usually overhead for a web app.
""",
            "concepts": [
                ("Fast-forward", "Advancing a branch pointer when no divergent commits exist."),
                ("Three-way merge", "Combining two branches using their common ancestor."),
                ("Merge conflict", "Overlapping changes Git will not resolve automatically."),
                ("Trunk-based development", "Integrating small changes into main continuously."),
            ],
            "takeaways": [
                "A fast-forward moves a pointer; a real merge creates a two-parent commit",
                "Conflicts are Git refusing to guess — resolve and remove every marker",
                "Short-lived branches are the real conflict prevention",
                "Pick merge-commit or squash per repo and stay consistent",
            ],
            "resources": [
                {"kind": "book", "title": "Pro Git — Branching in a nutshell", "url": "https://git-scm.com/book/en/v2/Git-Branching-Branches-in-a-Nutshell"},
                {"kind": "book", "title": "Pro Git — Basic merging", "url": "https://git-scm.com/book/en/v2/Git-Branching-Basic-Branching-and-Merging"},
            ],
            "video": {"query": "git branching merging conflicts tutorial", "title": "Branching and merging"},
        },
        {
            "id": "rebase",
            "title": "Rebase and rewriting history",
            "topic": "Rebase",
            "summary": "Replaying commits for a linear history — and the one rule you must not break.",
            "minutes": 16,
            "body": """
## What rebase does

Merge combines. Rebase **replays**: it takes your commits, finds the merge base,
and applies them one at a time on top of a new base.

```
before:  A─B─C (main)          after:  A─B─C─D'─E' (feature)
              \\
               D─E (feature)
```

`D'` and `E'` are new commits with new SHAs. The originals still exist until
garbage collection, which is why the reflog can rescue you.

```bash
git switch feature
git rebase main
git rebase --continue        # after fixing a conflict
git rebase --abort           # back to where you started
```

## Interactive rebase

```bash
git rebase -i HEAD~5
```

```
pick   a1b2c3  Add exam model
squash d4e5f6  Fix typo
reword 7g8h9i  Add grading
drop   j1k2l3  Debug logging
```

`squash`/`fixup` combine, `reword` edits a message, `edit` stops to amend,
`drop` removes, and reordering the lines reorders the commits. This is how you
turn eleven "wip" commits into three reviewable ones before opening a PR.

## The golden rule

**Never rewrite history that others have pulled.**

Rebasing shared commits gives everyone a divergent history and forces every
collaborator to repair their clone. Rewrite freely on your own unpushed or
unshared branch; never on `main`.

If you must force-push your own feature branch after a rebase:

```bash
git push --force-with-lease      # refuses if someone else pushed meanwhile
```

Use `--force-with-lease`, never bare `--force`.

## Rebase or merge?

- Rebase **your feature branch onto main** to keep it current and linear.
- Merge **into main** (or squash-merge) to integrate.
- Never rebase `main` itself.

## Related tools

```bash
git commit --amend            # fix the last commit (rewrites it)
git cherry-pick <sha>         # copy one commit somewhere else
git revert <sha>              # a NEW commit undoing an old one — safe on shared history
```

`revert` is the right undo for anything already pushed.
""",
            "concepts": [
                ("Rebase", "Replaying commits onto a new base, producing new SHAs."),
                ("Interactive rebase", "Editing, squashing, reordering or dropping commits before sharing."),
                ("force-with-lease", "A force push that aborts if the remote moved unexpectedly."),
                ("Revert", "A new commit that undoes a previous one, safe on shared history."),
            ],
            "takeaways": [
                "Rebase rewrites commits — never do it to history others have pulled",
                "Interactive rebase is for cleaning your branch before review",
                "Use --force-with-lease, never bare --force",
                "On shared branches, undo with revert rather than rewriting",
            ],
            "resources": [
                {"kind": "book", "title": "Pro Git — Rebasing", "url": "https://git-scm.com/book/en/v2/Git-Branching-Rebasing"},
                {"kind": "doc", "title": "Git — git-rebase", "url": "https://git-scm.com/docs/git-rebase"},
            ],
            "video": {"query": "git rebase interactive squash explained tutorial", "title": "Git rebase"},
        },
        {
            "id": "remotes",
            "title": "Remotes and collaboration",
            "topic": "Remotes",
            "summary": "Fetch, pull, push, and what a pull request is actually for.",
            "minutes": 15,
            "body": """
## Remotes and tracking

```bash
git remote -v
git remote add upstream https://github.com/org/repo.git
git fetch origin                       # download; change nothing locally
git status                             # ahead/behind your upstream branch
git push -u origin feature/exams       # push and set tracking
```

`origin/main` is a **remote-tracking branch**: your last-known snapshot of the
remote. `fetch` updates it and touches nothing else, which makes it always safe.

## Pull = fetch + integrate

```bash
git pull                       # fetch + merge (default)
git pull --rebase              # fetch + rebase — avoids "Merge branch 'main'" noise
git config --global pull.rebase true
```

The merge-commit spam in many repositories is entirely `git pull` on a branch
that had local commits.

## Forks and upstream

```bash
git remote add upstream https://github.com/original/repo.git
git fetch upstream
git rebase upstream/main
```

## Pull requests

A PR is a review request, not a delivery mechanism. What makes one reviewable:

- **Small.** Under ~400 lines changed is where review quality falls off a cliff.
- **One concern.** Refactor and behaviour change go in separate PRs.
- **A description** saying why, how to test, and what was deliberately left out.
- **Green CI** before a human is asked to look.

As a reviewer: comment on correctness, clarity and risk. Style is the linter's
job; if you are arguing about formatting, configure a formatter and stop.

## Protecting main

Require PR review, require CI to pass, forbid force-push. These three settings
prevent most of the accidents that make people afraid of Git.

## Useful conveniences

```bash
git stash push -m "wip"       # park changes
git stash pop
git worktree add ../hotfix main   # a second working directory, same repo
```

`git worktree` beats stashing when you need to jump to another branch mid-task
and keep a build alive.
""",
            "concepts": [
                ("Remote-tracking branch", "A local snapshot of a branch's state on the remote."),
                ("Fetch vs pull", "Fetch downloads; pull downloads and integrates."),
                ("Pull request", "A review-and-discussion wrapper around a proposed merge."),
                ("Worktree", "An additional working directory backed by the same repository."),
            ],
            "takeaways": [
                "`fetch` is always safe; `pull` changes your branch",
                "`pull --rebase` avoids the merge-commit noise",
                "Small, single-concern PRs with green CI get real review",
                "Protect main: required review, required CI, no force-push",
            ],
            "resources": [
                {"kind": "book", "title": "Pro Git — Working with remotes", "url": "https://git-scm.com/book/en/v2/Git-Basics-Working-with-Remotes"},
                {"kind": "book", "title": "Pro Git — Contributing to a project", "url": "https://git-scm.com/book/en/v2/Distributed-Git-Contributing-to-a-Project"},
            ],
            "video": {"query": "git remotes fetch pull push pull request workflow tutorial", "title": "Collaborating with Git"},
        },
        {
            "id": "undo",
            "title": "Undoing anything",
            "topic": "Recovery",
            "summary": "reset, revert, restore, reflog — the map of every undo.",
            "minutes": 16,
            "body": """
## Pick the right undo

| Situation | Command |
|---|---|
| Discard unstaged edits | `git restore <file>` (destructive) |
| Unstage, keep the edit | `git restore --staged <file>` |
| Fix the last commit's message/content | `git commit --amend` |
| Undo local commits, keep changes staged | `git reset --soft HEAD~1` |
| Undo local commits, keep changes unstaged | `git reset HEAD~1` (mixed) |
| Undo local commits, throw work away | `git reset --hard HEAD~1` |
| Undo a **pushed** commit | `git revert <sha>` |
| Recover a "lost" commit | `git reflog`, then `git reset --hard <sha>` |
| Restore one file from another commit | `git restore --source=<sha> -- <file>` |

## reset's three modes

`reset` moves the branch pointer and optionally the index and working tree:

```
--soft    move HEAD only            (changes stay staged)
--mixed   HEAD + index              (changes stay in the working tree)  [default]
--hard    HEAD + index + worktree   (changes are gone)
```

`--hard` is the only genuinely destructive one, and even it usually leaves the
*commits* recoverable via the reflog. Uncommitted work is what `--hard`
destroys permanently — which is the argument for committing early and often.

## reflog

```bash
git reflog
# a1b2c3 HEAD@{0}: reset: moving to HEAD~2
# d4e5f6 HEAD@{1}: commit: Add exam grading
git reset --hard d4e5f6
```

Every movement of HEAD for the last 90 days. A "lost" commit after a bad
rebase, reset or branch deletion is almost always sitting right here.

## Conflict and merge escape hatches

```bash
git merge --abort
git rebase --abort
git cherry-pick --abort
git checkout --ours file / --theirs file    # during a conflict
```

## The mental model

Git very rarely deletes committed data. If it is committed, it is recoverable
until garbage collection. The dangerous operations are the ones touching
**uncommitted** work: `reset --hard`, `restore`, `clean -fd`.
""",
            "concepts": [
                ("reset --soft/--mixed/--hard", "How far back the pointer move propagates: HEAD, index, working tree."),
                ("revert", "A forward commit undoing an earlier one — the safe undo for shared history."),
                ("reflog", "A log of every HEAD movement, used to recover lost commits."),
            ],
            "takeaways": [
                "reset rewrites local history; revert is the shared-history undo",
                "`--hard` is the only reset that destroys work — and only uncommitted work",
                "`git reflog` recovers almost any 'lost' commit",
                "Commit early: committed work is recoverable, uncommitted work is not",
            ],
            "resources": [
                {"kind": "book", "title": "Pro Git — Reset demystified", "url": "https://git-scm.com/book/en/v2/Git-Tools-Reset-Demystified"},
                {"kind": "doc", "title": "Git — git-reflog", "url": "https://git-scm.com/docs/git-reflog"},
            ],
            "video": {"query": "git reset revert reflog undo mistakes tutorial", "title": "Undoing things in Git"},
        },
        {
            "id": "workflow",
            "title": "Tags, releases and hooks",
            "topic": "Workflow",
            "summary": "Marking versions and automating the checks nobody remembers to run.",
            "minutes": 13,
            "body": """
## Tags

```bash
git tag -a v1.4.0 -m "Learning section"     # annotated: author, date, message
git tag v1.4.0-rc1                           # lightweight: just a pointer
git push origin v1.4.0
git tag -l "v1.*"
git describe --tags                          # v1.4.0-12-gabc1234
```

Use **annotated** tags for releases — they are real objects and can be signed.
Tags are not pushed by `git push` unless you ask.

## Semantic versioning

`MAJOR.MINOR.PATCH` — breaking / feature / fix. The value is entirely in the
discipline: consumers can read a version bump and know whether to worry.

## Conventional commits

```
feat(learn): add chapter exams
fix(api): return 404 for unknown chapter id
docs: expand README setup section
refactor!: drop the notebooks endpoint     # ! marks a breaking change
```

A parseable prefix lets tooling generate changelogs and derive the next version.
Adopt it or do not, but be consistent — a half-adopted convention gives you the
overhead without the automation.

## Hooks

Scripts in `.git/hooks` (or a managed directory) that run at lifecycle points:

- `pre-commit` — format, lint, run fast tests. The highest-value hook.
- `commit-msg` — validate the message format.
- `pre-push` — run the test suite.

```bash
#!/bin/sh
# .git/hooks/pre-commit
npm run lint --silent || { echo "lint failed"; exit 1; }
```

Hooks live outside version control by default, so teams use a manager (husky,
pre-commit, lefthook) to share them. Keep them **fast** — a pre-commit hook
taking 30 seconds will be bypassed with `--no-verify` within a week.

Hooks are a convenience, not a control. CI is the gate; the hook just shortens
the feedback loop.

## Configuration worth setting

```bash
git config --global user.name "..."
git config --global user.email "..."
git config --global init.defaultBranch main
git config --global pull.rebase true
git config --global rebase.autoStash true
git config --global diff.algorithm histogram
```
""",
            "concepts": [
                ("Annotated tag", "A tag object with author, date and message — the right kind for releases."),
                ("Semantic versioning", "MAJOR.MINOR.PATCH signalling the nature of a change."),
                ("Git hook", "A script run automatically at a point in the Git lifecycle."),
            ],
            "takeaways": [
                "Use annotated tags for releases and push them explicitly",
                "Conventional commits enable changelog and version automation",
                "Keep pre-commit hooks fast or they will be bypassed",
                "Hooks shorten feedback; CI is the actual gate",
            ],
            "resources": [
                {"kind": "book", "title": "Pro Git — Tagging", "url": "https://git-scm.com/book/en/v2/Git-Basics-Tagging"},
                {"kind": "book", "title": "Pro Git — Git hooks", "url": "https://git-scm.com/book/en/v2/Customizing-Git-Git-Hooks"},
            ],
            "video": {"query": "git tags releases hooks semantic versioning tutorial", "title": "Tags, releases and hooks"},
        },
        {
            "id": "advanced",
            "title": "Large repos and advanced tools",
            "topic": "Advanced Git",
            "summary": "Submodules, LFS, sparse checkout, and searching history properly.",
            "minutes": 14,
            "body": """
## Submodules

```bash
git submodule add https://github.com/org/lib.git vendor/lib
git clone --recurse-submodules <url>
git submodule update --init --recursive
```

A submodule pins another repository at a specific commit. Precise, and a
constant source of "I cloned it and half the files are missing". Prefer a
package manager where one exists; use submodules when you genuinely need source
pinned by SHA.

## Git LFS

Git stores every version of every file forever, so committing a 200MB binary
makes the repository 200MB heavier permanently — for everyone, on every clone.

```bash
git lfs install
git lfs track "*.psd"
git add .gitattributes
```

LFS stores a pointer in Git and the bytes elsewhere. Set it up *before* the
binaries land; retrofitting means rewriting history.

## Partial and sparse checkout

```bash
git clone --filter=blob:none <url>        # blobs fetched on demand
git clone --depth 1 <url>                 # shallow: latest commit only (CI)
git sparse-checkout set apps/web packages/ui
```

These make a monorepo workable on a laptop and cut CI clone times substantially.

## Searching history

```bash
git log -S "processPayment" --oneline     # when was this string added/removed
git log -G "regex" --oneline              # by regex
git log --follow -p -- path/to/file       # across renames
git grep "TODO" $(git rev-list --all)     # search every revision
```

## Maintenance

```bash
git gc --aggressive          # repack
git count-objects -vH        # how big is this repo, really
git fsck                     # integrity check
```

## Rewriting history at scale

To remove a large file or a leaked secret from all history, use `git filter-repo`
(the modern replacement for `filter-branch`). Then understand what it means:
every SHA after the rewrite point changes, every collaborator must re-clone,
and **any secret that was pushed must still be rotated** — it exists in forks,
caches and clones you do not control.
""",
            "concepts": [
                ("Submodule", "A pinned reference to another repository at a specific commit."),
                ("Git LFS", "Large File Storage — pointers in Git, bytes stored externally."),
                ("Sparse checkout", "Materialising only part of a repository's tree."),
                ("filter-repo", "The supported tool for rewriting history across an entire repository."),
            ],
            "takeaways": [
                "Binaries in Git are permanent weight — use LFS from the start",
                "Shallow and sparse clones make large repos and CI practical",
                "`git log -S` finds when a string entered or left the codebase",
                "History rewriting invalidates every downstream clone — and never un-leaks a secret",
            ],
            "resources": [
                {"kind": "book", "title": "Pro Git — Submodules", "url": "https://git-scm.com/book/en/v2/Git-Tools-Submodules"},
                {"kind": "doc", "title": "Git LFS", "url": "https://git-lfs.com/"},
            ],
            "video": {"query": "git submodules lfs sparse checkout monorepo tutorial", "title": "Advanced Git tooling"},
            "notes": """
Two habits that prevent most Git pain: commit small and often (committed work is
recoverable), and never let a branch live longer than a couple of days
(long-lived branches are where painful conflicts are manufactured).
""",
        },
    ],
    "exams": [
        {
            "id": "git-exam-1",
            "title": "Git — Fundamentals Assessment",
            "description": "Covers chapters 1–4: the object model, staging, branching, rebase.",
            "chapter_ids": ["model", "basics", "branching", "rebase"],
            "questions": [
                {
                    "type": "mcq", "topic": "Git Model", "chapter_id": "model",
                    "prompt": "What is a Git branch?",
                    "options": [
                        "A copy of the working directory",
                        "A movable pointer to a commit",
                        "A compressed diff of changes",
                        "A directory inside .git/objects",
                    ],
                    "answer": 1,
                    "explanation": "A branch is a file containing one SHA. That is why creating one is instantaneous.",
                },
                {
                    "type": "truefalse", "topic": "Git Model", "chapter_id": "model",
                    "prompt": "Git stores each commit as a diff against its parent.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. Commits point at complete trees (snapshots); diffs are computed on demand.",
                },
                {
                    "type": "mcq", "topic": "Basics", "chapter_id": "basics",
                    "prompt": "What are the three states a file moves through?",
                    "options": [
                        "Local, remote, origin",
                        "Working tree, index (staging), repository",
                        "Draft, review, merged",
                        "Head, tail, stash",
                    ],
                    "answer": 1,
                    "explanation": "`git add` moves working-tree changes into the index; `git commit` moves the index into the repository.",
                },
                {
                    "type": "code", "topic": "Basics", "chapter_id": "basics", "language": "bash",
                    "prompt": "What does this command do?",
                    "code": "git restore --staged src/api.py",
                    "options": [
                        "Discards the edits to the file",
                        "Unstages the file but keeps the edits in the working tree",
                        "Reverts the last commit touching the file",
                        "Deletes the file",
                    ],
                    "answer": 1,
                    "explanation": "It undoes `git add`. `git restore` without `--staged` is the destructive one.",
                },
                {
                    "type": "mcq", "topic": "Branching", "chapter_id": "branching",
                    "prompt": "When does a merge fast-forward?",
                    "options": [
                        "When there are no conflicts",
                        "When the target branch has no commits the source branch lacks",
                        "When --no-ff is passed",
                        "When the branches have the same name",
                    ],
                    "answer": 1,
                    "explanation": "If the target has not diverged, Git simply advances the pointer — no merge commit is needed.",
                },
                {
                    "type": "scenario", "topic": "Branching", "chapter_id": "branching",
                    "prompt": "A feature branch has been open for six weeks and merging it produces conflicts in 40 files. What is the structural fix for next time?",
                    "options": [
                        "Use a different merge strategy",
                        "Keep branches short-lived and integrate frequently",
                        "Squash before merging",
                        "Disable autocrlf",
                    ],
                    "answer": 1,
                    "explanation": "Conflict volume is a function of divergence time. Short-lived branches are the actual prevention.",
                },
                {
                    "type": "mcq", "topic": "Rebase", "chapter_id": "rebase",
                    "prompt": "What is the golden rule of rebasing?",
                    "options": [
                        "Never rebase more than 10 commits",
                        "Never rewrite history that others have already pulled",
                        "Always rebase before every commit",
                        "Only rebase on Fridays",
                    ],
                    "answer": 1,
                    "explanation": "Rebasing creates new SHAs. Doing it to shared commits forces every collaborator to repair their clone.",
                },
                {
                    "type": "code", "topic": "Rebase", "chapter_id": "rebase", "language": "bash",
                    "prompt": "Why prefer this over `git push --force`?",
                    "code": "git push --force-with-lease",
                    "options": [
                        "It is faster",
                        "It aborts if someone else pushed to the branch since your last fetch",
                        "It skips CI",
                        "It creates a backup tag",
                    ],
                    "answer": 1,
                    "explanation": "Bare --force overwrites whatever is there. --force-with-lease refuses when the remote moved unexpectedly.",
                },
                {
                    "type": "mcq", "topic": "Rebase", "chapter_id": "rebase",
                    "prompt": "You need to undo a commit that is already on main and pulled by the team. What do you use?",
                    "options": ["git reset --hard", "git revert", "git rebase -i", "git commit --amend"],
                    "answer": 1,
                    "explanation": "`revert` creates a new commit undoing the old one, leaving shared history intact.",
                },
                {
                    "type": "truefalse", "topic": "Basics", "chapter_id": "basics",
                    "prompt": "Rewriting history to remove an accidentally committed API key means the key no longer needs rotating.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. It exists in forks, clones, CI caches and mirrors. Rotate the credential — always.",
                },
            ],
        },
        {
            "id": "git-exam-2",
            "title": "Git — Collaboration & Recovery",
            "description": "Covers chapters 5–8: remotes, undoing, workflow, advanced tooling.",
            "chapter_ids": ["remotes", "undo", "workflow", "advanced"],
            "questions": [
                {
                    "type": "mcq", "topic": "Remotes", "chapter_id": "remotes",
                    "prompt": "What is the difference between fetch and pull?",
                    "options": [
                        "None — pull is an alias",
                        "fetch downloads without changing your branch; pull downloads and integrates",
                        "fetch works only on tags",
                        "pull is read-only",
                    ],
                    "answer": 1,
                    "explanation": "That is why `fetch` is always safe and `pull` can create merge commits or conflicts.",
                },
                {
                    "type": "scenario", "topic": "Remotes", "chapter_id": "remotes",
                    "prompt": "A repository's history is full of 'Merge branch main into main' commits. What causes it?",
                    "options": [
                        "Squash merging",
                        "`git pull` merging when the local branch has its own commits",
                        "Force pushing",
                        "Annotated tags",
                    ],
                    "answer": 1,
                    "explanation": "Set `pull.rebase true` (or use `git pull --rebase`) to replay local commits instead of merging.",
                },
                {
                    "type": "mcq", "topic": "Recovery", "chapter_id": "undo",
                    "prompt": "Which reset mode keeps your changes staged?",
                    "options": ["--hard", "--mixed", "--soft", "--keep"],
                    "answer": 2,
                    "explanation": "--soft moves HEAD only, leaving the index and working tree untouched — ideal for recomposing a commit.",
                },
                {
                    "type": "scenario", "topic": "Recovery", "chapter_id": "undo",
                    "prompt": "After a botched rebase, three commits appear to be gone. What recovers them?",
                    "options": [
                        "git fsck --lost-found only",
                        "git reflog to find the pre-rebase SHA, then reset --hard to it",
                        "Re-clone the repository",
                        "Nothing — rebased commits are deleted immediately",
                    ],
                    "answer": 1,
                    "explanation": "The reflog records every HEAD movement for ~90 days. The old commits still exist until garbage collection.",
                },
                {
                    "type": "truefalse", "topic": "Recovery", "chapter_id": "undo",
                    "prompt": "`git reset --hard` can permanently destroy uncommitted work.",
                    "options": ["True", "False"],
                    "answer": 0,
                    "explanation": "True. Committed work is recoverable via the reflog; uncommitted work is not. Commit early.",
                },
                {
                    "type": "mcq", "topic": "Workflow", "chapter_id": "workflow",
                    "prompt": "Why use an annotated tag rather than a lightweight one for a release?",
                    "options": [
                        "It is smaller",
                        "It is a real object carrying author, date and message, and it can be signed",
                        "Lightweight tags cannot be pushed",
                        "Annotated tags update automatically",
                    ],
                    "answer": 1,
                    "explanation": "A lightweight tag is just a pointer. A release wants provenance.",
                },
                {
                    "type": "scenario", "topic": "Workflow", "chapter_id": "workflow",
                    "prompt": "A pre-commit hook runs the full test suite and takes 45 seconds. Developers have started using `--no-verify`. What is the fix?",
                    "options": [
                        "Ban --no-verify in the contributing guide",
                        "Move the slow checks to CI and keep only fast formatting/linting in the hook",
                        "Make the hook run in the background",
                        "Remove hooks entirely",
                    ],
                    "answer": 1,
                    "explanation": "Hooks shorten the feedback loop; CI is the gate. A slow hook is a bypassed hook.",
                },
                {
                    "type": "mcq", "topic": "Advanced Git", "chapter_id": "advanced",
                    "prompt": "Why is committing large binaries directly to Git a problem?",
                    "options": [
                        "Git cannot store binary files",
                        "Every version is kept forever, so every clone pays the full weight permanently",
                        "Binaries break merges",
                        "GitHub rejects them",
                    ],
                    "answer": 1,
                    "explanation": "History is immutable, so the bytes stay in the repository for good. LFS keeps pointers in Git and bytes elsewhere.",
                },
                {
                    "type": "code", "topic": "Advanced Git", "chapter_id": "advanced", "language": "bash",
                    "prompt": "What does this find?",
                    "code": "git log -S \"processPayment\" --oneline",
                    "options": [
                        "Commits whose message mentions processPayment",
                        "Commits that added or removed the string processPayment",
                        "Files currently containing processPayment",
                        "Branches named processPayment",
                    ],
                    "answer": 1,
                    "explanation": "The 'pickaxe' search finds where a string entered or left the codebase — invaluable for archaeology.",
                },
                {
                    "type": "mcq", "topic": "Remotes", "chapter_id": "remotes",
                    "prompt": "Which three branch protections prevent most Git accidents on main?",
                    "options": [
                        "Squash merge, rebase merge, linear history",
                        "Required review, required CI, no force-push",
                        "Signed commits, LFS, submodules",
                        "Shallow clones, sparse checkout, worktrees",
                    ],
                    "answer": 1,
                    "explanation": "They make the destructive and unreviewed paths unavailable rather than merely discouraged.",
                },
            ],
        },
    ],
}
