"""The store provisioner: turns ``stores`` rows into Helm releases and back (docs/stores.md).

It runs as its own Deployment (``gateway-provisioner``) from the gateway image, with cluster
rights the internet-facing gateway does not have, and no inbound traffic but its probes.
"""
