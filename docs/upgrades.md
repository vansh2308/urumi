# Upgrades and rollbacks

Two Helm releases share the `urumi` namespace and are upgraded independently:

| Release | Contents | State |
| --- | --- | --- |
| `urumi` (`deploy/helm/urumi`) | WooCommerce MCP server, gateway, agent, dashboard | stateless pods; data in managed PostgreSQL |
| `store` (Bitnami `wordpress` chart) | WordPress + MariaDB | PersistentVolumeClaims `store-wordpress`, `data-store-mariadb-0` |

Hosted MCP servers are not Helm releases: the gateway owns them (update their image from the
dashboard or `PATCH /v1/hosted-servers/{id}`; it rolls with `maxUnavailable: 0`).

## How a `urumi` upgrade runs

1. Images are tagged with the commit (`global.imageTag`), so every Helm revision maps to exact
   code, and rolling back means old images, not rebuilt ones.
2. Pre-upgrade hooks run one migration Job per service (`alembic upgrade head`). If one fails,
   the upgrade fails before any pod changes.
3. Deployments roll with `maxSurge: 1, maxUnavailable: 0`: a new pod must pass readiness (the
   database is reachable and at the expected schema revision) before an old one is stopped.
4. An old pod first sleeps 5 s in `preStop` while its endpoint is removed from kube-proxy,
   Traefik and nginx, then gets `SIGTERM` and drains in-flight requests (uvicorn graceful
   shutdown, up to the service's grace period).
5. `--atomic` (in both deploy scripts) rolls the release back to the previous revision by itself
   if the rollout does not become ready within the timeout.

Production values run two replicas per service with a PodDisruptionBudget (`minAvailable: 1`),
so node drains and upgrades always keep one serving.

## Upgrade the platform (MCP server, gateway, agent, dashboard)

```bash
git switch master && git pull                                   # the code to ship
deploy/scripts/deploy-local.sh                                  # local: build, import, upgrade
REGISTRY=ghcr.io/<you> deploy/scripts/push-images.sh            # VPS: push the images
DOMAIN=<domain> REGISTRY=ghcr.io/<you> EXTRA_VALUES=my-vps.yaml deploy/scripts/deploy-vps.sh
helm -n urumi history urumi                                     # the new revision
```

One service only (e.g. a gateway hotfix): `helm upgrade urumi deploy/helm/urumi -n urumi
--reuse-values --set gateway.image.tag=<sha> --atomic --wait`.

## Roll the platform back

```bash
helm -n urumi history urumi
helm -n urumi rollback urumi <revision> --wait --timeout 5m
```

A rollback restores the images, ConfigMaps, NetworkPolicies and every other chart object of that
revision. It does **not** undo database migrations: they are forward-only and written to stay
compatible with the previous release (add a column, backfill, remove only in a later release),
because old pods keep serving during every rollout. If a migration itself must be reverted, run
`alembic downgrade <revision>` from the old image as a one-off Job before rolling back, after a
database snapshot (Neon: create a branch first, which is instant).

### Drill (local, 2026-10-19)

Requests sent continuously while switching revisions (`list_products` through the gateway plus
the dashboard's gateway health, one pair every ~2 s):

| Change | Duration | Requests | Failed |
| --- | --- | --- | --- |
| `rollback 15 → 14` (all four images change), before the preStop fix | 8 s | 48 | 5 (Traefik 502s to stopped pods) |
| `rollback 22 → 21` (all four images change), with `preStopSleepSeconds: 5` | 8 s | 28 | 0 |

Locally each service has one replica; with production's two replicas and the PodDisruptionBudget
there is additional headroom.

## Upgrade the store

Back up first. The dump is consistent without locking the store, and the password never appears
in a process's arguments:

```bash
deploy/scripts/backup-store.sh            # -> backups/store-db-<ts>.sql.gz, store-wp-content-<ts>.tar.gz
```

Then upgrade with a pinned chart version (read its upgrade notes; major versions can change
MariaDB):

```bash
helm -n urumi history store
helm upgrade store oci://registry-1.docker.io/bitnamicharts/wordpress --version <new version> \
  -n urumi -f deploy/helm/woocommerce/values-local.secret.yaml --atomic --wait --timeout 15m
curl -4 -I http://wordpress.local/ && curl -4 -I http://wordpress.local/wp-admin/
```

WordPress core, plugin (WooCommerce) and theme updates done from wp-admin change the files on the
`store-wordpress` volume and the database; take the same backup before them.

## Roll the store back

* **Chart or image only** (the database schema did not change): `helm -n urumi rollback store
  <revision> --wait`. Volumes are untouched by Helm.
* **The database was migrated** (e.g. a WooCommerce or WordPress upgrade ran its updater): roll
  back the release and restore the dump taken before the upgrade. This replaces the store's data
  with the backup, so orders placed since then are lost; export them first if needed:

  ```bash
  helm -n urumi rollback store <revision> --wait
  gunzip -c backups/store-db-<ts>.sql.gz | kubectl -n urumi exec -i statefulset/store-mariadb -c mariadb -- \
    bash -c 'mariadb --defaults-extra-file=<(printf "[client]\nuser=root\npassword=%s\n" "$(cat "$MARIADB_ROOT_PASSWORD_FILE")")'
  kubectl -n urumi exec -i deploy/store-wordpress -c wordpress -- \
    bash -c 'rm -rf /bitnami/wordpress/wp-content && tar -C /bitnami/wordpress -xzf -' < backups/store-wp-content-<ts>.tar.gz
  kubectl -n urumi rollout restart deploy/store-wordpress
  ```

The WordPress and MariaDB volumes carry `helm.sh/resource-policy: keep` (WordPress) or come from a
StatefulSet template (MariaDB), so neither an upgrade nor an uninstall deletes them.
