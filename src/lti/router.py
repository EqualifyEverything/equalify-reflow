"""FastAPI router for LTI 1.3 endpoints.

Implements the LTI 1.3 launch flow:
1. Canvas calls POST /lti/login (OIDC initiation)
2. We redirect Canvas to its auth endpoint with state/nonce
3. Canvas redirects user back to POST /lti/launch with id_token
4. We validate the JWT and process the launch
5. User is redirected to the viewer with their job

Canvas also fetches GET /lti/jwks to get our public keys for JWT verification.
"""

import asyncio
import logging
import secrets
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from redis.asyncio import Redis

from ..config import settings
from ..dependencies import get_document_processing_service, get_job_service, get_redis_client, get_storage_service
from ..services.document_processing_service import DocumentProcessingService
from ..services.job_service import JobService
from ..services.storage_service import StorageService
from .adapters import FastAPIRequest, RedisLaunchDataStorage, parse_form_data
from .config import get_canvas_config_json, get_tool_config
from .keys import get_jwks
from .models import LTIConfigResponse, LTIErrorResponse
from .service import LTIService, LTIServiceError, parse_launch_claims

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lti", tags=["LTI"])


@router.get("/jwks")
async def get_jwks_endpoint() -> dict[str, list[dict[str, str]]]:
    """Serve the tool's JWKS (JSON Web Key Set).

    Canvas fetches this endpoint to get our public keys for verifying
    JWTs that we sign. This endpoint is public and doesn't require
    authentication.

    Returns:
        JWKS JSON with public key(s)
    """
    try:
        return get_jwks()
    except Exception as e:
        logger.error(f"Failed to generate JWKS: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate JWKS")


@router.get("/config")
async def get_config_endpoint(request: Request) -> dict[str, Any]:
    """Serve LTI tool configuration for Canvas admin setup.

    This endpoint provides the JSON configuration needed to set up
    the Developer Key in Canvas. Admins can reference this URL or
    copy the JSON to configure the tool.

    Returns:
        Canvas Developer Key configuration JSON
    """
    # Build base URL from request
    base_url = str(request.base_url).rstrip("/")

    return get_canvas_config_json(base_url)


@router.post("/login")
async def oidc_login(
    request: Request,
    redis: Redis = Depends(get_redis_client),
) -> RedirectResponse:
    """Handle OIDC login initiation from Canvas.

    This is step 1 of the LTI 1.3 launch flow. Canvas sends:
    - iss: Issuer (Canvas URL)
    - login_hint: Opaque user identifier
    - target_link_uri: Where to redirect after auth
    - lti_message_hint: Optional message context

    We respond by redirecting to Canvas's auth endpoint with:
    - state: CSRF protection token (stored in Redis)
    - nonce: Replay protection token (stored in Redis)
    - Other OIDC parameters

    Returns:
        Redirect to Canvas authorization endpoint
    """
    # Parse form data
    form_data = await parse_form_data(request)

    # Extract required parameters
    iss = form_data.get("iss")
    login_hint = form_data.get("login_hint")
    target_link_uri = form_data.get("target_link_uri")
    lti_message_hint = form_data.get("lti_message_hint")
    client_id = form_data.get("client_id")
    lti_deployment_id = form_data.get("lti_deployment_id")

    # Validate required parameters
    if not iss:
        logger.warning("OIDC login missing issuer")
        raise HTTPException(status_code=400, detail="Missing issuer (iss) parameter")

    if not login_hint:
        logger.warning("OIDC login missing login_hint")
        raise HTTPException(status_code=400, detail="Missing login_hint parameter")

    if not target_link_uri:
        # Default to our launch endpoint
        target_link_uri = str(request.base_url).rstrip("/") + "/lti/launch"

    # Validate issuer matches configured platform
    if iss != settings.lti_issuer:
        logger.warning(f"OIDC login from unknown issuer: {iss}")
        raise HTTPException(
            status_code=400,
            detail=f"Unknown issuer. Expected: {settings.lti_issuer}",
        )

    # Generate state and nonce for security
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    # Store state in Redis for validation during launch
    storage = RedisLaunchDataStorage(redis)
    await storage.set_value_async(
        state,
        {
            "nonce": nonce,
            "target_link_uri": target_link_uri,
            "client_id": client_id or settings.lti_client_id,
            "deployment_id": lti_deployment_id or settings.lti_deployment_id,
        },
    )

    # Build redirect URL to Canvas auth endpoint
    auth_params = {
        "scope": "openid",
        "response_type": "id_token",
        "response_mode": "form_post",
        "prompt": "none",
        "client_id": settings.lti_client_id,
        "redirect_uri": str(request.base_url).rstrip("/") + "/lti/launch",
        "state": state,
        "nonce": nonce,
        "login_hint": login_hint,
    }

    if lti_message_hint:
        auth_params["lti_message_hint"] = lti_message_hint

    auth_url = f"{settings.lti_auth_login_url}?{urlencode(auth_params)}"

    logger.info(f"OIDC login initiated, redirecting to Canvas auth")
    logger.debug(f"Auth URL: {auth_url[:100]}...")

    return RedirectResponse(url=auth_url, status_code=302)


@router.post("/launch")
async def lti_launch(
    request: Request,
    background_tasks: BackgroundTasks,
    redis: Redis = Depends(get_redis_client),
    storage_service: StorageService = Depends(get_storage_service),
    job_service: JobService = Depends(get_job_service),
    processing_service: DocumentProcessingService = Depends(get_document_processing_service),
) -> HTMLResponse:
    """Handle LTI launch after OIDC authentication.

    This is step 3 of the LTI 1.3 launch flow. Canvas POSTs:
    - id_token: JWT containing launch claims
    - state: Our state token from step 1

    We:
    1. Validate the state token (CSRF protection)
    2. Validate the JWT signature (using Canvas's JWKS)
    3. Validate the nonce (replay protection)
    4. Extract file menu claims
    5. Download the file from Canvas
    6. Create a conversion job
    7. Redirect to the viewer

    Returns:
        HTML page that redirects to the viewer (iframe-safe)
    """
    # Parse form data
    form_data = await parse_form_data(request)

    id_token = form_data.get("id_token")
    state = form_data.get("state")

    if not id_token:
        logger.warning("LTI launch missing id_token")
        return _error_response("Missing id_token in launch request")

    if not state:
        logger.warning("LTI launch missing state")
        return _error_response("Missing state parameter")

    # Retrieve stored state from Redis
    storage = RedisLaunchDataStorage(redis)
    stored_state = await storage.get_value_async(state)

    if not stored_state:
        logger.warning(f"LTI launch with invalid/expired state: {state[:8]}...")
        return _error_response("Invalid or expired state. Please try launching again.")

    # Clean up state after retrieval
    await storage.cleanup_state_async(state)

    try:
        # Validate JWT using pylti1p3
        from pylti1p3.message_launch import MessageLaunch

        # Get tool config
        tool_config = get_tool_config()

        # Create request adapter
        lti_request = FastAPIRequest(request, form_data)

        # Validate the launch
        message_launch: Any = MessageLaunch(
            lti_request,
            tool_config,
            launch_data_storage=storage,
        )

        # Get validated claims
        launch_data: dict[str, Any] = message_launch.get_launch_data()

        if not launch_data:
            logger.error("LTI launch validation succeeded but no launch data")
            return _error_response("Failed to retrieve launch data")

        # Validate nonce (replay protection)
        token_nonce: str | None = launch_data.get("nonce")
        expected_nonce = stored_state.get("nonce")

        if token_nonce != expected_nonce:
            logger.warning("LTI launch nonce mismatch")
            return _error_response("Invalid nonce. Possible replay attack.")

        if token_nonce is None:
            logger.warning("LTI launch missing nonce")
            return _error_response("Missing nonce in token")

        # Check nonce hasn't been used before
        nonce_valid = await storage.check_nonce_async(
            token_nonce,
            launch_data.get("iss", settings.lti_issuer),
        )
        if not nonce_valid:
            logger.warning("LTI launch nonce already used")
            return _error_response("Nonce already used. Possible replay attack.")

        # Parse launch claims into our model
        parsed_launch = parse_launch_claims(launch_data)

        # Process the launch (download file, create job)
        lti_service = LTIService(storage_service, job_service)

        try:
            response, s3_key = await lti_service.process_file_menu_launch(parsed_launch)
        except LTIServiceError as e:
            logger.error(f"LTI launch processing failed: {e}")
            return _error_response(f"Failed to process launch: {str(e)}")

        # Trigger document processing in background
        filename = response.file_name or "canvas_file.pdf"
        background_tasks.add_task(
            _process_document_async,
            processing_service,
            response.job_id,
            s3_key,
            filename,
        )

        # Return HTML that redirects to viewer (iframe-safe)
        return _redirect_response(response.viewer_url, response.file_name)

    except Exception as e:
        logger.error(f"LTI launch validation failed: {e}", exc_info=True)
        return _error_response(f"Launch validation failed: {str(e)}")


async def _process_document_async(
    processing_service: DocumentProcessingService,
    job_id: str,
    s3_key: str,
    filename: str,
) -> None:
    """Process document in background.

    Args:
        processing_service: Document processing service
        job_id: Job ID to process
        s3_key: S3 key where PDF is stored
        filename: Original filename
    """
    try:
        logger.info(f"Starting background processing for LTI job {job_id}")
        await processing_service.process_document(
            job_id=job_id,
            s3_key=s3_key,
            filename=filename,
            review_mode="auto",  # LTI launches use auto mode
        )
        logger.info(f"Background processing completed for LTI job {job_id}")
    except Exception as e:
        logger.error(f"Background processing failed for LTI job {job_id}: {e}")


def _error_response(message: str) -> HTMLResponse:
    """Generate an HTML error response for LTI launches.

    LTI launches happen in iframes, so we can't use standard HTTP
    error codes. Instead, we return an HTML page with the error.

    Args:
        message: Error message to display

    Returns:
        HTMLResponse with error page
    """
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Launch Error</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                background: #f5f5f5;
            }}
            .error-container {{
                background: white;
                padding: 2rem;
                border-radius: 8px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                max-width: 500px;
                text-align: center;
            }}
            h1 {{ color: #dc3545; margin-bottom: 1rem; }}
            p {{ color: #666; line-height: 1.5; }}
            .retry {{ margin-top: 1rem; }}
            a {{ color: #007bff; text-decoration: none; }}
        </style>
    </head>
    <body>
        <div class="error-container">
            <h1>Launch Error</h1>
            <p>{message}</p>
            <p class="retry">
                Please close this window and try again from Canvas.
            </p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)


def _redirect_response(viewer_url: str, file_name: str | None = None) -> HTMLResponse:
    """Generate an HTML response that redirects to the viewer.

    We use an HTML page with JavaScript redirect instead of HTTP redirect
    because the launch happens in an iframe and we want to break out
    of the iframe to show the viewer full-screen.

    Args:
        viewer_url: URL to redirect to
        file_name: Optional filename for display

    Returns:
        HTMLResponse with redirect page
    """
    display_name = file_name or "your document"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Opening Equalify Reflow</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                background: #f5f5f5;
            }}
            .loading-container {{
                background: white;
                padding: 2rem;
                border-radius: 8px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                text-align: center;
            }}
            .spinner {{
                width: 40px;
                height: 40px;
                border: 4px solid #f3f3f3;
                border-top: 4px solid #007bff;
                border-radius: 50%;
                animation: spin 1s linear infinite;
                margin: 0 auto 1rem;
            }}
            @keyframes spin {{
                0% {{ transform: rotate(0deg); }}
                100% {{ transform: rotate(360deg); }}
            }}
            h2 {{ color: #333; margin-bottom: 0.5rem; }}
            p {{ color: #666; }}
        </style>
        <script>
            // Redirect to viewer - break out of iframe if needed
            function redirect() {{
                var url = "{viewer_url}";
                if (window.top !== window.self) {{
                    // We're in an iframe, try to redirect the parent
                    try {{
                        window.top.location.href = url;
                    }} catch (e) {{
                        // Cross-origin restriction, open in new tab
                        window.open(url, '_blank');
                    }}
                }} else {{
                    window.location.href = url;
                }}
            }}
            // Auto-redirect after brief delay
            setTimeout(redirect, 500);
        </script>
    </head>
    <body>
        <div class="loading-container">
            <div class="spinner"></div>
            <h2>Opening Equalify Reflow</h2>
            <p>Processing: {display_name}</p>
            <p><small>If you're not redirected automatically, <a href="{viewer_url}" target="_top">click here</a>.</small></p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)


@router.get("/health")
async def lti_health() -> dict[str, str]:
    """Health check for LTI endpoints.

    Returns:
        Health status
    """
    return {"status": "ok", "lti_enabled": str(settings.lti_enabled)}
