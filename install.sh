#!/usr/bin/env sh
# Nexplane one-line installer
# Usage: curl -fsSL https://raw.githubusercontent.com/youbetyourballs/nexplane/master/install.sh | sh
# Or:    wget -qO- https://raw.githubusercontent.com/youbetyourballs/nexplane/master/install.sh | sh

set -e

# ── Recovery mode ─────────────────────────────────────────────────────────────
# Usage: install.sh --recover
# Reads the upgrade sentinel file and restores the previous version without Docker.

recover_from_upgrade() {
  NEXPLANE_DATA_DIR="${NEXPLANE_DATA_DIR:-/home/ec2-user/nexplane-data}"
  SENTINEL="$NEXPLANE_DATA_DIR/upgrade/sentinel.json"
  NEXPLANE_DIR="${NEXPLANE_DIR:-/home/ec2-user/nexplane}"
  ENV_FILE="$NEXPLANE_DIR/.env"

  printf '\n\033[1m=== Nexplane Recovery Mode ===\033[0m\n\n'

  if [ ! -f "$SENTINEL" ]; then
    printf '\033[31mERROR: Sentinel file not found at %s\033[0m\n' "$SENTINEL"
    printf 'Nothing to recover. If the platform is running, no recovery is needed.\n'
    exit 1
  fi

  STATE=$(python3 -c "import json,sys; d=json.load(open('$SENTINEL')); print(d.get('state','unknown'))")
  PREVIOUS_TAG=$(python3 -c "import json,sys; d=json.load(open('$SENTINEL')); print(d.get('previous_image_tag',''))")
  SNAPSHOT=$(python3 -c "import json,sys; d=json.load(open('$SENTINEL')); print(d.get('snapshot_path',''))")
  PREV_VERSION=$(python3 -c "import json,sys; d=json.load(open('$SENTINEL')); print(d.get('previous_version','unknown'))")

  printf '  Sentinel state : %s\n' "$STATE"
  printf '  Previous image : %s\n' "$PREVIOUS_TAG"
  printf '  Snapshot       : %s\n' "$SNAPSHOT"
  printf '\n'

  if [ "$STATE" = "upgrade_complete" ]; then
    printf '\033[32mUpgrade completed successfully. No recovery needed.\033[0m\n'
    exit 0
  fi

  printf 'Stopping backend container...\n'
  cd "$NEXPLANE_DIR" && docker compose stop backend 2>/dev/null || true

  # Restore IMAGE_TAG in .env
  if [ -n "$PREVIOUS_TAG" ]; then
    TAG_ONLY="${PREVIOUS_TAG##*:}"
    if grep -q "^IMAGE_TAG=" "$ENV_FILE" 2>/dev/null; then
      sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=$TAG_ONLY|" "$ENV_FILE"
    else
      printf 'IMAGE_TAG=%s\n' "$TAG_ONLY" >> "$ENV_FILE"
    fi
    printf 'Restored IMAGE_TAG=%s in %s\n' "$TAG_ONLY" "$ENV_FILE"
  fi

  # pg_restore from snapshot
  if [ -n "$SNAPSHOT" ] && [ -f "$SNAPSHOT" ]; then
    printf 'Running pg_restore from %s ...\n' "$SNAPSHOT"
    # shellcheck disable=SC1090
    . "$ENV_FILE" 2>/dev/null || true
    DB_HOST="${DB_HOST:-localhost}"
    DB_PORT="${DB_PORT:-5432}"
    DB_USER="${DB_USER:-nexplane}"
    DB_NAME="${DB_NAME:-nexplane}"
    export PGPASSWORD="${DB_PASSWORD:-nexplane_dev}"
    zcat "$SNAPSHOT" | pg_restore --clean --if-exists -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" || true
    printf 'pg_restore complete.\n'
  else
    printf '\033[33mWARNING: No valid snapshot found at '"'"'%s'"'"'. Skipping pg_restore.\033[0m\n' "$SNAPSHOT"
  fi

  # Restart on previous image
  printf 'Starting backend on previous image...\n'
  cd "$NEXPLANE_DIR" && docker compose up -d backend

  # Update sentinel
  python3 -c "
import json
with open('$SENTINEL') as f:
    d = json.load(f)
d['state'] = 'rollback_complete'
d['rollback_reason'] = 'manual_recover'
with open('$SENTINEL', 'w') as f:
    json.dump(d, f, indent=2)
"

  printf '\n\033[1m=== Recovery complete ===\033[0m\n'
  printf 'Platform restored to version: %s\n' "$PREV_VERSION"
  printf 'Access the platform at your configured URL.\n'
}

if [ "${1:-}" = "--recover" ]; then
  recover_from_upgrade
  exit 0
fi

REPO_URL="https://github.com/youbetyourballs/nexplane"
REPO_ZIP="https://github.com/youbetyourballs/nexplane/archive/refs/heads/master.zip"
INSTALL_DIR="${NEXPLANE_DIR:-nexplane}"
MIN_DOCKER_VERSION="24"
MIN_COMPOSE_VERSION="2"

# ── Colours ──────────────────────────────────────────────────────────────────

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }

# ── Helpers ───────────────────────────────────────────────────────────────────

die() { red "✗ $*"; exit 1; }
info() { printf '  %s\n' "$*"; }
step() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }

command_exists() { command -v "$1" >/dev/null 2>&1; }

version_major() {
    # Extract major version number from a version string like "24.0.5" or "v2.21.0"
    printf '%s' "$1" | sed 's/[^0-9]*//' | cut -d. -f1
}

# ── Banner ────────────────────────────────────────────────────────────────────

printf '\n'
bold '  ███╗   ██╗███████╗██╗  ██╗██████╗ ██╗      █████╗ ███╗   ██╗███████╗'
bold '  ████╗  ██║██╔════╝╚██╗██╔╝██╔══██╗██║     ██╔══██╗████╗  ██║██╔════╝'
bold '  ██╔██╗ ██║█████╗   ╚███╔╝ ██████╔╝██║     ███████║██╔██╗ ██║█████╗  '
bold '  ██║╚██╗██║██╔══╝   ██╔██╗ ██╔═══╝ ██║     ██╔══██║██║╚██╗██║██╔══╝  '
bold '  ██║ ╚████║███████╗██╔╝ ██╗██║     ███████╗██║  ██║██║ ╚████║███████╗'
bold '  ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝'
printf '\n'
info "Infrastructure change control — https://nexplane.ai"
printf '\n'

# ── OS detection ──────────────────────────────────────────────────────────────

step "Detecting environment"

OS="$(uname -s 2>/dev/null || echo unknown)"
ARCH="$(uname -m 2>/dev/null || echo unknown)"

case "$OS" in
    Linux)   info "OS: Linux ($ARCH)" ;;
    Darwin)  info "OS: macOS ($ARCH)" ;;
    *)       die "Unsupported OS: $OS. Nexplane requires Linux or macOS." ;;
esac

# Detect package manager (Linux only)
PKG_MANAGER=""
if [ "$OS" = "Linux" ]; then
    if command_exists apt-get; then
        PKG_MANAGER="apt"
    elif command_exists yum; then
        PKG_MANAGER="yum"
    elif command_exists dnf; then
        PKG_MANAGER="dnf"
    elif command_exists brew; then
        PKG_MANAGER="brew"
    fi
fi
if [ "$OS" = "Darwin" ]; then
    PKG_MANAGER="brew"
fi

# ── Dependency checks ─────────────────────────────────────────────────────────

step "Checking dependencies"

MISSING=""

# curl or wget (need at least one for downloads)
if ! command_exists curl && ! command_exists wget; then
    MISSING="$MISSING curl"
fi

# git (preferred for clone; fall back to zip download if absent)
HAS_GIT=true
if ! command_exists git; then
    HAS_GIT=false
    yellow "  git not found — will download zip archive instead"
fi

# unzip (needed only if no git)
if [ "$HAS_GIT" = "false" ] && ! command_exists unzip; then
    MISSING="$MISSING unzip"
fi

# Docker
if ! command_exists docker; then
    MISSING="$MISSING docker"
else
    DOCKER_VER="$(docker version --format '{{.Server.Version}}' 2>/dev/null || docker --version | grep -oE '[0-9]+\.[0-9]+' | head -1)"
    DOCKER_MAJOR="$(version_major "$DOCKER_VER")"
    if [ -n "$DOCKER_MAJOR" ] && [ "$DOCKER_MAJOR" -lt "$MIN_DOCKER_VERSION" ] 2>/dev/null; then
        yellow "  Docker $DOCKER_VER found — version $MIN_DOCKER_VERSION+ recommended"
    else
        info "Docker: $DOCKER_VER ✓"
    fi
fi

# Docker Compose (plugin form: docker compose; or standalone: docker-compose)
HAS_COMPOSE=false
if docker compose version >/dev/null 2>&1; then
    COMPOSE_VER="$(docker compose version --short 2>/dev/null || docker compose version | grep -oE '[0-9]+\.[0-9]+' | head -1)"
    COMPOSE_MAJOR="$(version_major "$COMPOSE_VER")"
    if [ -n "$COMPOSE_MAJOR" ] && [ "$COMPOSE_MAJOR" -ge "$MIN_COMPOSE_VERSION" ] 2>/dev/null; then
        HAS_COMPOSE=true
        info "Docker Compose: $COMPOSE_VER ✓"
    fi
elif command_exists docker-compose; then
    COMPOSE_VER="$(docker-compose version --short 2>/dev/null || docker-compose --version | grep -oE '[0-9]+\.[0-9]+' | head -1)"
    COMPOSE_MAJOR="$(version_major "$COMPOSE_VER")"
    if [ -n "$COMPOSE_MAJOR" ] && [ "$COMPOSE_MAJOR" -ge "$MIN_COMPOSE_VERSION" ] 2>/dev/null; then
        HAS_COMPOSE=true
        info "Docker Compose (standalone): $COMPOSE_VER ✓"
    fi
fi

if [ "$HAS_COMPOSE" = "false" ]; then
    MISSING="$MISSING docker-compose-plugin"
fi

# ── Install missing deps ───────────────────────────────────────────────────────

if [ -n "$MISSING" ]; then
    step "Installing missing dependencies: $MISSING"

    if [ "$(id -u)" -ne 0 ] && ! command_exists sudo; then
        die "Missing dependencies ($MISSING) and no sudo available. Please install them manually and re-run."
    fi

    SUDO=""
    [ "$(id -u)" -ne 0 ] && SUDO="sudo"

    for dep in $MISSING; do
        case "$dep" in
            curl)
                info "Installing curl..."
                case "$PKG_MANAGER" in
                    apt) $SUDO apt-get install -y -qq curl ;;
                    yum) $SUDO yum install -y -q curl ;;
                    dnf) $SUDO dnf install -y -q curl ;;
                    brew) brew install curl ;;
                    *) die "Cannot install curl automatically. Please install it and re-run." ;;
                esac
                ;;
            unzip)
                info "Installing unzip..."
                case "$PKG_MANAGER" in
                    apt) $SUDO apt-get install -y -qq unzip ;;
                    yum) $SUDO yum install -y -q unzip ;;
                    dnf) $SUDO dnf install -y -q unzip ;;
                    brew) brew install unzip ;;
                    *) die "Cannot install unzip automatically. Please install it and re-run." ;;
                esac
                ;;
            docker)
                info "Installing Docker..."
                if [ "$OS" = "Linux" ]; then
                    if command_exists curl; then
                        curl -fsSL https://get.docker.com | $SUDO sh
                    elif command_exists wget; then
                        wget -qO- https://get.docker.com | $SUDO sh
                    else
                        die "Cannot install Docker automatically. Visit https://docs.docker.com/get-docker/"
                    fi
                    # Add current user to docker group so they don't need sudo
                    if [ "$(id -u)" -ne 0 ]; then
                        $SUDO usermod -aG docker "$(id -un)" 2>/dev/null || true
                        yellow "  Added $(id -un) to docker group — you may need to log out and back in"
                    fi
                elif [ "$OS" = "Darwin" ]; then
                    die "Please install Docker Desktop for Mac from https://docs.docker.com/desktop/mac/install/ and re-run."
                fi
                ;;
            docker-compose-plugin)
                info "Installing Docker Compose plugin..."
                if [ "$OS" = "Linux" ]; then
                    case "$PKG_MANAGER" in
                        apt)
                            $SUDO apt-get install -y -qq docker-compose-plugin 2>/dev/null || \
                            $SUDO apt-get install -y -qq docker-compose 2>/dev/null || \
                            die "Could not install Docker Compose. Visit https://docs.docker.com/compose/install/"
                            ;;
                        yum|dnf)
                            $SUDO "$PKG_MANAGER" install -y -q docker-compose-plugin 2>/dev/null || \
                            die "Could not install Docker Compose. Visit https://docs.docker.com/compose/install/"
                            ;;
                        *) die "Please install Docker Compose manually: https://docs.docker.com/compose/install/" ;;
                    esac
                else
                    die "Please install Docker Compose manually: https://docs.docker.com/compose/install/"
                fi
                ;;
        esac
    done

    green "  Dependencies installed ✓"
fi

# ── Fetch Nexplane ─────────────────────────────────────────────────────────────

step "Fetching Nexplane"

if [ -d "$INSTALL_DIR" ]; then
    yellow "  Directory '$INSTALL_DIR' already exists"
    printf '  Use existing directory? [Y/n] '
    read -r REPLY
    case "$REPLY" in
        [nN]*) die "Aborted. Set NEXPLANE_DIR to a different path and re-run." ;;
    esac
else
    if [ "$HAS_GIT" = "true" ]; then
        info "Cloning from $REPO_URL..."
        git clone --depth=1 "$REPO_URL" "$INSTALL_DIR"
    else
        info "Downloading archive..."
        TMPZIP="/tmp/nexplane-master.zip"
        if command_exists curl; then
            curl -fsSL "$REPO_ZIP" -o "$TMPZIP"
        else
            wget -qO "$TMPZIP" "$REPO_ZIP"
        fi
        info "Extracting..."
        unzip -q "$TMPZIP" -d /tmp/nexplane-extract
        mv /tmp/nexplane-extract/nexplane-master "$INSTALL_DIR"
        rm -f "$TMPZIP"
        rm -rf /tmp/nexplane-extract
    fi
    info "Nexplane source ready in ./$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ── Configure environment ──────────────────────────────────────────────────────

step "Configuring environment"

if [ ! -f ".env" ]; then
    cp .env.example .env

    # Generate a real secret key
    if command_exists python3; then
        SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    elif command_exists openssl; then
        SECRET="$(openssl rand -hex 32)"
    else
        SECRET="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    fi

    # Inline-edit the placeholders
    sed -i.bak \
        -e "s|change-me-generate-a-random-32-char-hex-string|$SECRET|" \
        -e "s|change-me-strong-password|nexplane_$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')|" \
        .env && rm -f .env.bak

    green "  Generated .env with random credentials ✓"
    yellow "  Review .env before production use — defaults bind to localhost only"
else
    info ".env already exists — skipping generation"
fi

# ── Start Nexplane ─────────────────────────────────────────────────────────────

step "Starting Nexplane"

# Use 'docker compose' if available, fall back to 'docker-compose'
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
else
    COMPOSE="docker-compose"
fi

$COMPOSE pull --quiet 2>/dev/null || true
$COMPOSE up -d --build

# ── Wait for backend to be ready ───────────────────────────────────────────────

step "Waiting for services to be ready"

MAX_WAIT=120
WAITED=0
printf '  '
while [ "$WAITED" -lt "$MAX_WAIT" ]; do
    if $COMPOSE exec -T backend wget -qO- http://localhost:8000/health >/dev/null 2>&1 || \
       curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        printf ' ✓\n'
        break
    fi
    printf '.'
    sleep 3
    WAITED=$((WAITED + 3))
done

if [ "$WAITED" -ge "$MAX_WAIT" ]; then
    printf '\n'
    yellow "  Services are taking longer than expected. Check logs with:"
    yellow "    cd $INSTALL_DIR && $COMPOSE logs -f"
fi

# ── Done ───────────────────────────────────────────────────────────────────────

printf '\n'
green "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
green "  Nexplane is running!"
green "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf '\n'
info "  UI:       http://localhost:3000"
info "  API:      http://localhost:8000"
info "  API docs: http://localhost:8000/docs"
info "  MCP:      http://localhost:8000/mcp"
printf '\n'
info "  Default login:  admin@acme.example / admin123"
printf '\n'
yellow "  ⚠  Never expose Nexplane to the public internet."
yellow "     Use Tailscale or a VPN for remote access."
printf '\n'
info "  Manage:   cd $INSTALL_DIR && $COMPOSE logs -f"
info "  Stop:     cd $INSTALL_DIR && $COMPOSE down"
info "  Docs:     https://docs.nexplane.ai"
printf '\n'
