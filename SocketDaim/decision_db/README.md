# decision_db/  ⚠️ HANDED OFF

This directory used to hold the decision DB DDL and dev seed for the
Egress Gateway.  As of **2026-05-04** ownership has been fully handed off
to the Decision Agent project.

## What lives where now

| Artifact | New location |
|---|---|
| Schema (`init_db.sql`) | `c:\decision_agent\init_db.sql` |
| Mapping seed (`seed_mapping.sql`) | `c:\decision_agent\seed_mapping.sql` |
| Dev decision seed (`seed_test_decisions.sql`) | `c:\decision_agent\seed_test_decisions.sql` |
| `postgres-decision` container + `decision-pgdata` volume | `c:\decision_agent\docker-compose.yml` |

The SocketDaim repo no longer mounts anything from this directory.  The
`postgres-decision` service has been removed from `../docker-compose.yml`.

## Why this directory is kept

This `README.md` exists to leave a discoverable breadcrumb for anyone who
finds an old reference (commit, doc, search result) pointing at
`decision_db/`.  The directory itself has no runtime role.

## Boot order (dev)

1. `cd C:\SocketDaim     && docker compose up -d`   → creates `gw-net`
2. `cd C:\decision_agent && docker compose up -d`   → joins `gw-net`,
   brings up `postgres-decision` and `decision-agent`

Tear down in reverse.  `egress-gw` will tolerate the decision DB being
absent (it logs connection failures and retries).
