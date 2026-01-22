"""LTI 1.3 tool configuration for pylti1p3.

This module provides the tool configuration in the format expected by
the pylti1p3 library, bridging our application settings to the library's
expected configuration structure.
"""

import logging
from functools import lru_cache
from typing import Any

from pylti1p3.tool_config import ToolConfDict

from ..config import settings
from .keys import get_private_key_pem

logger = logging.getLogger(__name__)


class LTIConfigError(Exception):
    """Raised when LTI configuration is invalid or incomplete."""

    pass


def validate_lti_config() -> None:
    """Validate that all required LTI settings are configured.

    Raises:
        LTIConfigError: If required settings are missing
    """
    required_settings = [
        ("lti_issuer", settings.lti_issuer),
        ("lti_client_id", settings.lti_client_id),
        ("lti_auth_login_url", settings.lti_auth_login_url),
        ("lti_auth_token_url", settings.lti_auth_token_url),
        ("lti_jwks_url", settings.lti_jwks_url),
    ]

    missing = [name for name, value in required_settings if not value]

    if missing:
        raise LTIConfigError(
            f"Missing required LTI settings: {', '.join(missing)}. "
            "Set these in environment variables or .env file."
        )


@lru_cache(maxsize=1)
def get_tool_config() -> ToolConfDict:
    """Get the LTI tool configuration for pylti1p3.

    This creates a ToolConfDict instance that pylti1p3 uses to:
    - Validate incoming LTI launches
    - Sign outgoing JWTs
    - Configure platform (Canvas) endpoints

    Returns:
        ToolConfDict configured for Canvas integration

    Raises:
        LTIConfigError: If configuration is invalid

    Example configuration structure:
        {
            "https://canvas.instructure.com": [{
                "client_id": "10000000000001",
                "auth_login_url": "https://canvas.instructure.com/api/lti/authorize_redirect",
                "auth_token_url": "https://canvas.instructure.com/login/oauth2/token",
                "key_set_url": "https://canvas.instructure.com/api/lti/security/jwks",
                "deployment_ids": ["10000000000001"],
                "private_key_file": "/path/to/private.pem"
            }]
        }
    """
    validate_lti_config()

    # Build deployment IDs list
    deployment_ids = [settings.lti_deployment_id] if settings.lti_deployment_id else [settings.lti_client_id]

    # Get private key for signing
    private_key = get_private_key_pem()

    # Build platform configuration
    # The key is the issuer (Canvas URL), value is list of tool configs
    platform_config: dict[str, list[dict[str, Any]]] = {
        settings.lti_issuer: [
            {
                "client_id": settings.lti_client_id,
                "auth_login_url": settings.lti_auth_login_url,
                "auth_token_url": settings.lti_auth_token_url,
                "key_set_url": settings.lti_jwks_url,
                "deployment_ids": deployment_ids,
                # Private key for signing JWTs
                "private_key": private_key,
                # Default scopes (can be overridden per-launch)
                "default_scopes": [
                    "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
                    "https://purl.imsglobal.org/spec/lti-ags/scope/result.readonly",
                ],
            }
        ]
    }

    logger.info(f"LTI tool configured for issuer: {settings.lti_issuer}")

    return ToolConfDict(platform_config)


def get_canvas_config_json(base_url: str) -> dict[str, Any]:
    """Generate Canvas Developer Key configuration JSON.

    This is served at /lti/config to help Canvas admins set up
    the Developer Key with the correct endpoints.

    Args:
        base_url: Base URL of the tool (e.g., https://pdf.equalify.app)

    Returns:
        Configuration dictionary for Canvas Developer Key
    """
    return {
        "title": "Equalify PDF Reflow",
        "description": "Convert PDF documents into accessible, reflowable HTML",
        "oidc_initiation_url": f"{base_url}/lti/login",
        "target_link_uri": f"{base_url}/lti/launch",
        "extensions": [
            {
                "platform": "canvas.instructure.com",
                "privacy_level": "public",
                "settings": {
                    "placements": [
                        {
                            "placement": "file_menu",
                            "text": "Open in Equalify Reflow",
                            "enabled": True,
                            "icon_url": f"{base_url}/static/icon.png",
                            "message_type": "LtiResourceLinkRequest",
                            "target_link_uri": f"{base_url}/lti/launch",
                            "selection_width": 800,
                            "selection_height": 600,
                            "accept_media_types": "application/pdf",
                            "custom_fields": {
                                "file_id": "$com.instructure.File.id",
                                "file_name": "$com.instructure.File.display_name",
                                "file_content_type": "$com.instructure.File.content_type",
                                "file_download_url": "$com.instructure.File.url",
                                "course_id": "$Canvas.course.id",
                            },
                        }
                    ],
                },
            }
        ],
        "public_jwk_url": f"{base_url}/lti/jwks",
        "scopes": [
            "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
            "https://purl.imsglobal.org/spec/lti-ags/scope/result.readonly",
            "https://purl.imsglobal.org/spec/lti-ags/scope/score",
        ],
    }
