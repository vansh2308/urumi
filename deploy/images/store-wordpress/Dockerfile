# urumi/store-wordpress: the Bitnami WordPress image the core store runs, with WooCommerce baked
# in and the idempotent setup script for provisioned stores. No plugin download at first boot,
# so a store namespace needs no internet egress.
#
#   docker build -t urumi/store-wordpress:<tag> deploy/images/store-wordpress
#
# The base is pinned by digest (Bitnami's `latest` tag moves; versioned tags moved to
# bitnamilegacy in 2025). It is the image the `store` release runs (WordPress 7.1.3).
ARG WORDPRESS_IMAGE=registry-1.docker.io/bitnami/wordpress@sha256:845d250ecd73f5e9b8ab2c42ab8b32407992456bab4dcb79547f2c0b7d25959b

FROM alpine:3.22 AS plugins
ARG WOOCOMMERCE_VERSION=11.2.1
ARG WOOCOMMERCE_SHA256=5317434cb381b5fdc7c1dd6ee258324f08c390976aaf8d1199f2a0e9a3ab8586
ADD --checksum=sha256:${WOOCOMMERCE_SHA256} \
    https://downloads.wordpress.org/plugin/woocommerce.${WOOCOMMERCE_VERSION}.zip /tmp/woocommerce.zip
RUN mkdir /plugins && unzip -q /tmp/woocommerce.zip -d /plugins && chmod -R g+rwX /plugins

FROM ${WORDPRESS_IMAGE}
ARG WOOCOMMERCE_VERSION=11.2.1
LABEL org.opencontainers.image.title="urumi/store-wordpress" \
      org.opencontainers.image.description="Bitnami WordPress with WooCommerce ${WOOCOMMERCE_VERSION} for Urumi stores"
# Same ownership as the rest of the Bitnami tree (daemon:root, group-writable): the chart's
# prepare-base-dir init container copies it, and first boot moves wp-content to the volume.
COPY --from=plugins --chown=daemon:root /plugins/woocommerce /opt/bitnami/wordpress/wp-content/plugins/woocommerce
COPY --chmod=0755 store-setup.sh /opt/urumi/store-setup.sh
