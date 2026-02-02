"""LTI 1.3 integration for Canvas LMS.

This package provides LTI 1.3 endpoints for launching the PDF converter
directly from the Canvas file menu.

Endpoints:
- POST /lti/login - OIDC login initiation (Canvas calls first)
- POST /lti/launch - Handle launch after OIDC flow
- GET /lti/jwks - Tool's public keys for Canvas
- GET /lti/config - Configuration JSON for Canvas admin setup

Usage:
    # In main.py
    if settings.lti_enabled:
        from .lti import router as lti_router
        app.include_router(lti_router)
"""

from .router import router
from .service import LTIService, LTIServiceError

__all__ = ["router", "LTIService", "LTIServiceError"]
