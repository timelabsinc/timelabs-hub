# Labs OS agent guide

## System

- Production runs from `/root/ops-dashboard` on the `timelabs` VPS.
- The working branch is `order-form`. Do not merge it into `main` until the owner says the unfinished order work is complete.
- `CONTEXT.md` is the canonical architecture and handoff document. Read it before changing behavior.
- Secrets are gitignored. Never print or commit `.env`, OAuth files, tokens, password hashes, private keys, database contents, or customer data.

## Safety

- Treat the VPS checkout as production, not a scratch directory. Prepare and review a diff before applying it there.
- Before a database migration or production deployment, run the existing backup and verify the archive and SQLite integrity.
- Preserve existing records unless the requested migration explicitly says otherwise.
- Test nginx with `nginx -t` before any reload.
- Never hand-edit `/var/www/ops/*.html` or `/var/www/drop/index.html`; they are generated output.
- Shopify/store writes need a dry run or explicit owner approval. Read-only Shopify queries are allowed for validation.

## Architecture rules

- Generated pages come from Python generators and reuse `hub_shell.py`.
- Register new generators in both `tools.py` and `generate.py`.
- `agent_chat_server.py` runs on system Python. Keep modules it imports compatible with that environment.
- Generators and `drop_server.py` use `/root/ops-dashboard/venv/bin/python`.
- Order photos live under `data/order-photos/<order-id>/` and are served only through role-checked endpoints.
- Every order from every channel is logged locally. New orders start with `supplier_visible=0`; a person must explicitly send an order to the supplier queue.
- Never hide an in-progress, shipped, billed, or assigned supplier order automatically.

## Verification

For changed Python files:

```sh
python3 -c "import ast, pathlib; [ast.parse(p.read_text(), filename=str(p)) for p in pathlib.Path('.').glob('*.py')]"
```

Regenerate affected pages, then extract and check emitted JavaScript with `node --check`. Run:

```sh
/root/ops-dashboard/venv/bin/python /root/ops-dashboard/healthcheck.py
```

Before handoff:

- review `git diff --check` and `git diff`;
- confirm `git status` contains only intended files;
- verify services and timers remain healthy;
- commit with a message explaining the problem, cause, and fix.
