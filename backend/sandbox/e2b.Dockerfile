# The session sandbox image, built as an E2B template.
#
# Every tool the agents may need beyond E2B's stock base image is installed
# HERE, at build time, pinned — and nowhere else. The alternative this replaces
# was `lint_code` running `pip install ruff` inside a user's live sandbox the
# first time it was asked to lint Python: a change to what that session could
# do, made at the moment a tool discovered it was missing, that could not be
# reproduced from the repository and did not happen at all in a sandbox that
# had no network. What this image contains is what this file says it contains.
#
# Build and publish (once per change to this file; see README.md beside it):
#
#     cd backend/sandbox
#     e2b template build --name loom-sandbox
#
# then set `E2B_TEMPLATE=loom-sandbox` in backend/.env. With the variable
# unset the backend keeps using E2B's base image, and every tool that needs
# something from here reports it as missing rather than installing it.

FROM e2bdev/code-interpreter:latest

# --- Python linting for Agent 10 (`lint_code`) -------------------------------
# The same version this repository is checked with (requirements-dev.txt), so
# an agent's audit and CI disagree about nothing.
RUN pip install --no-cache-dir --disable-pip-version-check "ruff==0.16.6"

# --- git identity defaults -----------------------------------------------------
# `tools/git.py` sets a repo-local identity at init and never relies on these;
# they exist so a user who runs `git commit` by hand in the terminal panel
# before the agent has initialised anything is not stopped by "Please tell me
# who you are". Nothing here is a credential.
RUN git config --system init.defaultBranch main \
 && git config --system user.name "Loom Sandbox" \
 && git config --system user.email "sandbox@loom.local"
