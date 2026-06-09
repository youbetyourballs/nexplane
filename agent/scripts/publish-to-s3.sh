#!/usr/bin/env bash
# Builds the Nexplane agent binaries and publishes them to the S3 download bucket.
# Usage: ./scripts/publish-to-s3.sh [VERSION]
# Requires: go, aws CLI with valid credentials, write access to nexplane-agent-downloads bucket.

set -euo pipefail

VERSION="${1:-$(git describe --tags --always --dirty 2>/dev/null || echo "0.2.0")}"
BUCKET="nexplane-agent-downloads"
DIST="$(cd "$(dirname "$0")/.." && pwd)/dist"

echo "=== Nexplane Agent Publisher ==="
echo "Version: $VERSION"
echo "Bucket:  s3://$BUCKET"
echo ""

mkdir -p "$DIST"

echo "Building linux-amd64..."
GOOS=linux GOARCH=amd64 go build -ldflags "-X main.Version=$VERSION" -o "$DIST/nexplane-agent-linux-amd64" ./

echo "Building linux-arm64..."
GOOS=linux GOARCH=arm64 go build -ldflags "-X main.Version=$VERSION" -o "$DIST/nexplane-agent-linux-arm64" ./

echo "Building windows-amd64..."
GOOS=windows GOARCH=amd64 go build -ldflags "-X main.Version=$VERSION" -o "$DIST/nexplane-agent-windows-amd64-${VERSION}.exe" ./

echo "Building darwin-arm64..."
CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -ldflags "-X main.Version=$VERSION" -o "$DIST/nexplane-agent-darwin-arm64-${VERSION}" ./

echo ""
echo "Uploading to S3..."
aws s3 cp "$DIST/nexplane-agent-linux-amd64" "s3://$BUCKET/nexplane-agent-linux-amd64-${VERSION}"aws s3 cp "$DIST/nexplane-agent-linux-arm64" "s3://$BUCKET/nexplane-agent-linux-arm64-${VERSION}"aws s3 cp "$DIST/nexplane-agent-windows-amd64-${VERSION}.exe" "s3://$BUCKET/nexplane-agent-windows-amd64-${VERSION}.exe"aws s3 cp "$DIST/nexplane-agent-darwin-arm64-${VERSION}" "s3://$BUCKET/nexplane-agent-darwin-arm64-${VERSION}"
# Update the version pointer
echo -n "$VERSION" | aws s3 cp - "s3://$BUCKET/version" --acl public-read --content-type text/plain

echo ""
echo "=== Published version $VERSION ==="
echo "Binary URL: https://$BUCKET.s3.us-east-1.amazonaws.com/nexplane-agent-linux-amd64-${VERSION}"
echo "Version URL: https://$BUCKET.s3.us-east-1.amazonaws.com/version"
