# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Re-export shim so catalog_service finds this executor at container_registry.container_image_transfer."""
from app.connectors.executors.container_image_transfer import execute, rollback, ROLLBACK_CAPABILITY  # noqa: F401
