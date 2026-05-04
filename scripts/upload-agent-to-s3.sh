#!/usr/bin/env bash
# Upload nexplane-agent binaries to the public S3 releases bucket.
# Run this after `docker compose build backend`.
#
# Usage:
#   ./scripts/upload-agent-to-s3.sh
#   AWS_PROFILE=myprofile ./scripts/upload-agent-to-s3.sh
#
# Requires: aws CLI, docker
set -euo pipefail

BUCKET="${NEXPLANE_AGENT_BUCKET:-nexplane-agent-downloads}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
IMAGE="nexplane-backend"

echo "==> Extracting agent binaries from Docker image: $IMAGE"
CONTAINER=$(docker create "$IMAGE")
DIST_DIR=$(mktemp -d)
trap "docker rm '$CONTAINER' >/dev/null; rm -rf '$DIST_DIR'" EXIT

docker cp "$CONTAINER:/opt/nexplane-downloads/." "$DIST_DIR/"

VERSION=$(cat "$DIST_DIR/version" | tr -d '[:space:]')
echo "==> Agent version: $VERSION"
echo "==> Uploading to s3://$BUCKET/ ..."

# Upload each file with public-read ACL
for f in "$DIST_DIR"/*; do
    fname=$(basename "$f")
    echo "    $fname"
    aws s3 cp "$f" "s3://$BUCKET/$fname" \
        --region "$REGION" \
        --cache-control "public, max-age=31536000, immutable"
done

# version file gets short cache so clients always get current version
aws s3 cp "$DIST_DIR/version" "s3://$BUCKET/version" \
    --region "$REGION" \
    --cache-control "public, max-age=60" \
    --content-type "text/plain"

echo "==> Done. Binaries available at:"
echo "    https://$BUCKET.s3.$REGION.amazonaws.com/nexplane-agent-linux-amd64-$VERSION"
