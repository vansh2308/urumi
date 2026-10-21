# Production on a VPS (k3s)

The same Helm charts as the local k3d cluster, with configuration changes only. Target: one
Ubuntu or Debian VPS (2 vCPU / 4 GB RAM is enough), single-node k3s, one domain.

| | Local (k3d) | VPS (k3s) |
| --- | --- | --- |
| Values | `values.yaml` + `values-local.yaml` | `values.yaml` + `values-production.yaml` (+ `--set` from `deploy-vps.sh`) |
| Hosts | `urumi.local`, `gateway.urumi.local`, `wordpress.local` (`/etc/hosts`) | `app.<domain>`, `gateway.<domain>`, `shop.<domain>` (DNS A records) |
| TLS | none (HTTP) | cert-manager + Let's Encrypt, Traefik `websecure` |
| Images | built locally, `k3d image import`, tag = commit | pushed to a registry (GHCR), tag = commit |
| Replicas | 1 | 2 + PodDisruptionBudget |
| Store access from the MCP server | HTTP + OAuth 1.0a, `wordpress.local` pinned to the store Service | HTTPS + Basic auth, `shop.<domain>` pinned to Traefik's ClusterIP (no hairpin) |
| Storage | k3d `local-path` | k3s `local-path` (or your provider's class), PVCs kept on uninstall |
| Secrets | `create-local-secrets.sh` from `.env` | same script with `ENV_SUFFIX=.production`, or a secret manager |
| Gateway → Kubernetes API egress | k3d Docker network | the node's IP (`extraEgress`) |
| API docs | on | off |
| Provisioned stores | `http://<slug>.localtest.me` | `https://<slug>.shop.<domain>`, wildcard DNS, one certificate per store host ([stores.md](stores.md)) |

## 1. Server

1. Point DNS A records for `app`, `gateway` and `shop` at the server's public IP, and a wildcard
   record `*.shop` for provisioned stores (`acme.shop.<domain>`, …).
2. Open ports 22, 80 and 443 only (the Kubernetes API, 6443, stays closed to the internet; use
   an SSH tunnel for `kubectl`).
3. On the server, as root:

   ```bash
   sudo bash deploy/scripts/vps-bootstrap.sh
   ```

   It installs pinned k3s (Traefik, local-path storage, NetworkPolicy controller), Helm and
   cert-manager, creates the `urumi` namespace with the `restricted` Pod Security Standard, and
   prints the Kubernetes API endpoint IP.
4. Set your email in `deploy/vps/cluster-issuer.yaml` and apply it.
5. Copy `/etc/rancher/k3s/k3s.yaml` to your machine, set its `server:` to
   `https://127.0.0.1:6443`, and open a tunnel: `ssh -N -L 6443:127.0.0.1:6443 you@server`.
   Run the remaining steps from your machine with `KUBECONFIG` pointing at that file.

## 2. Secrets

Create `services/<service>/.env.production` (gitignored) with production values: new
`AUTH_TOKEN_PEPPER` (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`), new
`UPSTREAM_CREDENTIAL_KEYS` (`cd services/gateway && poetry run gateway-admin generate-credential-key`),
production `DB_URL`s (a separate Neon project or branch), `GEMINI_API_KEY`, and the WooCommerce
keys from step 3. Then:

```bash
ENV_SUFFIX=.production deploy/scripts/create-local-secrets.sh urumi
```

Only key names reach the command line; values pass through a private temp file. With a secret
manager, create the same three Secrets (`woocommerce-mcp-secrets`, `gateway-secrets`,
`agent-secrets`) with External Secrets or Sealed Secrets instead.

## 3. Store

```bash
cp deploy/helm/woocommerce/values-production.example.yaml \
   deploy/helm/woocommerce/values-production.secret.yaml   # set domain and passwords
helm upgrade --install store oci://registry-1.docker.io/bitnamicharts/wordpress \
  --version <the chart version you run locally> -n urumi \
  -f deploy/helm/woocommerce/values-production.secret.yaml --atomic --wait --timeout 15m
```

Open `https://shop.<domain>/wp-admin`, install and activate WooCommerce, enable Cash on
delivery, and create REST API keys (WooCommerce → Settings → Advanced → REST API, read/write).
Put them in `services/woocommerce-mcp/.env.production` and re-run the secrets script.

## 4. Images

Push with the workflow in `.github/workflows/images.yml` (on a `v*` tag), or from your machine:

```bash
docker login ghcr.io
REGISTRY=ghcr.io/<you> deploy/scripts/push-images.sh      # builds linux/amd64, tag = commit
```

For a private registry, add an `imagePullSecret` to the `urumi` namespace's default
ServiceAccount, or make the packages public.

## 5. Deploy

Write a small values file with the node's IP for the gateway's Kubernetes API rule (printed by
the bootstrap script):

```yaml
# my-vps.yaml
gateway:
  networkPolicy:
    extraEgress:
      - to: [{ ipBlock: { cidr: 10.0.0.5/32 } }]   # your node IP
        ports: [{ port: 6443, protocol: TCP }]
```

```bash
DOMAIN=<domain> REGISTRY=ghcr.io/<you> EXTRA_VALUES=my-vps.yaml deploy/scripts/deploy-vps.sh
```

Helm runs the database migrations first, then rolls out two replicas of each service.
`--atomic` rolls the release back by itself if anything fails.

## 6. First run

```bash
kubectl -n urumi exec deploy/gateway -- gateway-admin grant-platform-admin you@example.com
```

Register at `https://app.<domain>`, grant yourself platform admin as above, sign in again, then:

* MCP servers → Add server → Register by URL: slug `woocommerce`, URL
  `http://woocommerce-mcp.urumi.svc.cluster.local:8000/mcp` (the only allow-listed in-cluster URL).
* Deploy the GitHub and web search servers from their images (see [hosted-mcp.md](hosted-mcp.md)).
* Enable the servers for your team; invite members.

MCP clients connect to `https://gateway.<domain>/v1/mcp`, either with only that URL (OAuth: the
user approves them at `https://app.<domain>/oauth/consent`; `deploy-vps.sh` sets both URLs from
`DOMAIN`) or with a `urk_` key. See [oauth.md](oauth.md).

## 7. Verify

```bash
curl -I https://shop.<domain>/                      # 200, valid certificate
curl https://gateway.<domain>/health/ready          # {"status":"ready",...}
curl -o /dev/null -w '%{http_code}\n' https://gateway.<domain>/metrics   # 404: internal only
kubectl -n urumi get pods,ingress,certificate
kubectl -n urumi-mcp get deploy,pods,networkpolicy
```

Upgrades and rollbacks are in [deployment.md](deployment.md#5-upgrades-and-rollbacks).

## Notes

* **Databases**: the services keep using managed PostgreSQL (Neon), so the VPS holds no
  application database; only the store's MariaDB and WordPress files are on local volumes. Back
  them up (see the upgrade section) or move to a provider storage class.
* **Single node**: two replicas protect against pod failures and make rolling updates seamless,
  not against losing the node. A second node only needs `topologySpreadConstraints` and
  storage that is not node-local.
* **Firewall**: k3s programs iptables; keep the provider firewall limited to 22/80/443.
* **Provisioned stores**: `deploy-vps.sh` sets `STORES_BASE_DOMAIN=shop.<domain>` and the store
  image tags; `push-images.sh` also pushes `urumi-store-wordpress`. Each store is about 1 GB at
  first boot, so `STORES_MAX_TOTAL` (10 in `values-production.yaml`) should match the server's
  memory; on a 4 GB VPS set it to 2. Let's Encrypt rate limits (50 certificates per registered
  domain per week) apply to store hosts too.
