# Local cluster and store

The WooCommerce store runs in a k3d cluster (`urumi-local`) behind Traefik, the k3s ingress
controller. The host publishes the standard HTTP/HTTPS ports directly, so the store is at
`http://wordpress.local` with no port and no host-side forwarder (`socat` is no longer used).

## Port mappings

| Host | k3d load balancer | In cluster |
| --- | --- | --- |
| `80` | `80` | Traefik `web` → Ingress `wordpress.local` → `store-wordpress:80` |
| `443` | `443` | Traefik `websecure` (Traefik's default self-signed certificate) |
| `8080`, `8443` | `80`, `443` | Legacy aliases on the existing cluster; WordPress redirects them to port 80 |
| random (`k3d cluster list`) | `6443` | Kubernetes API (`kubectl` context `k3d-urumi-local`) |

The platform services run either in the cluster (Helm release `urumi`, see
[deployment.md](deployment.md)) or on the host for development:

| Service | In the cluster | On the host |
| --- | --- | --- |
| WooCommerce MCP | `woocommerce-mcp.urumi.svc.cluster.local:8000` (private) | `http://127.0.0.1:8000/mcp` |
| Gateway | `http://gateway.urumi.local` | `http://127.0.0.1:8100` |
| Agent | `http://agent.urumi.local` | `http://127.0.0.1:8201` |
| Dashboard | `http://urumi.local` | `http://127.0.0.1:5173` |

Both talk to the store at `WC_BASE_URL=http://wordpress.local`. The gateway's connector URL
decides which MCP server it calls: `http://127.0.0.1:8000/mcp` on the host,
`http://woocommerce-mcp.urumi.svc.cluster.local:8000/mcp` in the cluster.

WordPress's canonical URL is `http://wordpress.local`: it is set as `WP_HOME`/`WP_SITEURL` in
`wp-config.php` and as `home`/`siteurl` in the database, and `wordpressHost` in the Helm values
matches it. Keep all three and the published host port in agreement; a mismatch makes
WordPress redirect every request to the URL it believes is canonical.

## First-time setup

```bash
echo "127.0.0.1 wordpress.local" | sudo tee -a /etc/hosts
k3d cluster create --config deploy/k3d/cluster.yaml
cp deploy/helm/woocommerce/values-local.example.yaml deploy/helm/woocommerce/values-local.secret.yaml
# edit the passwords in values-local.secret.yaml
helm install store oci://registry-1.docker.io/bitnamicharts/wordpress --version 34.1.3 \
  -n urumi --create-namespace -f deploy/helm/woocommerce/values-local.secret.yaml
```

Docker Desktop on macOS binds ports 80 and 443 without `sudo`. On a Linux host the Docker
daemon runs as root, so the same config works there too.

## Existing cluster created with 8080/8443

Add the standard ports in place. This replaces only the k3d load balancer container; nodes,
volumes, the database and Helm releases are untouched:

```bash
k3d cluster edit urumi-local --port-add 80:80@loadbalancer --port-add 443:443@loadbalancer
```

## Daily startup

```bash
k3d cluster start urumi-local
kubectl -n urumi rollout status deploy/store-wordpress
kubectl -n urumi rollout status deploy/gateway            # if the urumi release is installed
```

For host development instead:

```bash
cd services/woocommerce-mcp && poetry run woocommerce-mcp   # terminal 1
cd services/gateway && poetry run gateway                   # terminal 2
cd services/agent && poetry run agent                       # terminal 3
cd apps/frontend && npm run dev                             # terminal 4
```

Checks:

```bash
curl -4 -I http://wordpress.local/                 # 200
curl -4 -I http://wordpress.local/wp-admin/        # 302 to wp-login.php
curl -4 http://127.0.0.1:8000/readyz               # {"status":"ready",...,"woocommerce":"ok"}
curl -4 http://127.0.0.1:8100/health/ready         # {"status":"ready",...}
```

The first readiness probe after the cluster starts can report `woocommerce: fail` while
WordPress warms up (the probe times out after 2 s); it turns `ok` on the next call.

## On a VPS

The same layout holds with a real domain: point DNS at the server, publish 80/443 to the
ingress (k3d as above, or k3s directly, whose bundled Traefik binds 80/443 on the node), set
`wordpressHost` and `WC_BASE_URL` to the domain, and use `https` with a real certificate
(for example cert-manager with Let's Encrypt) instead of `WC_ALLOW_INSECURE_HTTP`. The
step-by-step guide is [vps.md](vps.md).
