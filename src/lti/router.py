"""FastAPI router for LTI 1.3 endpoints.

Implements the LTI 1.3 launch flow:
1. Canvas calls POST /lti/login (OIDC initiation)
2. We redirect Canvas to its auth endpoint with state/nonce
3. Canvas redirects user back to POST /lti/launch with id_token
4. We validate the JWT and process the launch
5. User is redirected to the viewer with their job

Canvas also fetches GET /lti/jwks to get our public keys for JWT verification.
"""

import logging
from typing import Any

import requests as req_lib
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from redis.asyncio import Redis

from ..config import settings
from ..dependencies import get_converter_client, get_document_processing_service, get_job_service, get_redis_client, get_storage_service
from ..services.converter_client import ConverterClient
from ..services.document_processing_service import DocumentProcessingService
from ..services.job_service import JobService
from ..services.storage_service import StorageService
from .adapters import (
    FastAPICookieService,
    FastAPIMessageLaunch,
    FastAPIOIDCLogin,
    FastAPIRequest,
    FastAPISessionService,
    RedisLaunchDataStorage,
    parse_form_data,
)
from .config import get_canvas_config_json, get_tool_config
from .keys import get_jwks
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
    """Handle OIDC login initiation from Canvas using pylti1p3.

    This is step 1 of the LTI 1.3 launch flow. Canvas sends:
    - iss: Issuer (Canvas URL)
    - login_hint: Opaque user identifier
    - target_link_uri: Where to redirect after auth
    - lti_message_hint: Optional message context

    We use pylti1p3's OIDCLogin class which properly sets up the
    session state that MessageLaunch expects during the launch phase.

    Returns:
        Redirect to Canvas authorization endpoint
    """
    # Parse form data
    form_data = await parse_form_data(request)

    # Log what Canvas sends for debugging
    logger.debug(f"OIDC login form_data keys: {list(form_data.keys())}")
    if form_data.get("target_link_uri"):
        logger.info(f"Canvas target_link_uri: {form_data.get('target_link_uri')}")
    if form_data.get("lti_message_hint"):
        hint = form_data.get("lti_message_hint")
        # Decode the JWT payload to see file context
        try:
            import base64
            import json as json_mod
            jwt_parts = hint.split(".")
            if len(jwt_parts) >= 2:
                payload_b64 = jwt_parts[1]
                padding = 4 - len(payload_b64) % 4
                if padding != 4:
                    payload_b64 += "=" * padding
                payload_bytes = base64.urlsafe_b64decode(payload_b64)
                hint_payload = json_mod.loads(payload_bytes)
                logger.info(f"Canvas lti_message_hint payload: {json_mod.dumps(hint_payload, indent=2)}")
        except Exception as e:
            logger.info(f"Canvas lti_message_hint (raw): {hint[:100]}... (could not decode: {e})")

    # Create storage and services
    storage = RedisLaunchDataStorage(redis)

    # Create request adapter
    lti_request = FastAPIRequest(request, form_data)

    # Create session service for state management
    session_service = FastAPISessionService(lti_request, storage)
    lti_request.set_session_service(session_service)

    # Create cookie service for session management
    cookie_service = FastAPICookieService(storage)
    lti_request.set_cookie_service(cookie_service)

    # Get tool config
    tool_config = get_tool_config()

    try:
        # Use our FastAPI OIDC Login implementation
        oidc_login = FastAPIOIDCLogin(
            lti_request,
            tool_config,
            session_service=session_service,
            cookie_service=cookie_service,
            launch_data_storage=storage,
        )

        # The redirect_uri in the OIDC request must match a URI registered in the
        # Canvas Developer Key. We always redirect back to /lti/launch (our single
        # launch endpoint). Canvas preserves the original target_link_uri in the JWT
        # so the launch handler can route to the correct page (viewer vs dashboard).
        launch_url = str(request.base_url).rstrip("/") + "/lti/launch"
        target_link_uri = form_data.get("target_link_uri") or launch_url

        # Get redirect URL - disable cookie checks since we use Redis for state
        redirect_url = oidc_login.disable_check_cookies().redirect(launch_url)

        # Extract state and nonce from the generated redirect URL and store them
        # pylti1p3 generates these but stores them with session prefixes we can't easily retrieve
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(redirect_url)
        params = parse_qs(parsed.query)
        state = params.get("state", [None])[0]
        nonce = params.get("nonce", [None])[0]

        if state and nonce:
            # Store state -> nonce mapping directly in Redis for launch validation
            # Also store lti_message_hint which may contain file context
            state_data = {
                "nonce": nonce,
                "target_link_uri": target_link_uri,
                "lti_message_hint": form_data.get("lti_message_hint", ""),
            }
            await storage.set_value_async(state, state_data)
            logger.info(f"Stored state {state[:20]}... with nonce {nonce[:20]}...")

        logger.info(f"OIDC login initiated, redirecting to: {redirect_url[:100]}...")
        logger.debug(f"Full auth URL: {redirect_url}")

        return RedirectResponse(url=redirect_url, status_code=302)

    except Exception as e:
        logger.error(f"OIDC login failed: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"OIDC login failed: {str(e)}")


@router.post("/launch")
async def lti_launch(
    request: Request,
    background_tasks: BackgroundTasks,
    redis: Redis = Depends(get_redis_client),
    converter: ConverterClient = Depends(get_converter_client),
    job_service: JobService = Depends(get_job_service),
    processing_service: DocumentProcessingService = Depends(get_document_processing_service),
) -> Response:
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
    7. Redirect to the viewer or dashboard

    Returns:
        RedirectResponse for dashboard launches, HTMLResponse for file viewer launches
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
        # Note: We use FastAPIMessageLaunch instead of base MessageLaunch
        # because it implements the required _get_request_param() method
        import base64
        import json as json_module

        # Debug: Decode and log JWT claims before validation
        try:
            jwt_parts = id_token.split(".")
            if len(jwt_parts) >= 2:
                # Decode the body (second part)
                body_b64 = jwt_parts[1]
                # Add padding if needed
                padding = 4 - len(body_b64) % 4
                if padding != 4:
                    body_b64 += "=" * padding
                body_bytes = base64.urlsafe_b64decode(body_b64)
                jwt_body = json_module.loads(body_bytes)
                logger.info(f"JWT issuer (iss): {jwt_body.get('iss')}")
                logger.info(f"JWT audience (aud): {jwt_body.get('aud')}")
                logger.info(f"JWT deployment_id: {jwt_body.get('https://purl.imsglobal.org/spec/lti/claim/deployment_id')}")
        except Exception as jwt_debug_err:
            logger.warning(f"Could not decode JWT for debugging: {jwt_debug_err}")

        # Get tool config
        tool_config = get_tool_config()

        # Create request adapter first (session/cookie services added after)
        lti_request = FastAPIRequest(request, form_data)

        # Create session service and link it to the request
        session_service = FastAPISessionService(lti_request, storage)
        lti_request.set_session_service(session_service)

        # Create cookie service and link it to the request
        cookie_service = FastAPICookieService(storage, state)
        lti_request.set_cookie_service(cookie_service)

        # Validate the launch using our FastAPI-specific MessageLaunch
        # Note: Cookie checks are bypassed by:
        # 1. RedisLaunchDataStorage.get_session_cookie_name() returns None
        # 2. FastAPICookieService.get_cookie(state) returns state (for state validation fallback)
        #
        # When using Docker container networking to reach Canvas (e.g.,
        # http://canvas-lms-web-1/api/lti/security/jwks), Canvas validates
        # the Host header. Pass a custom requests session so pylti1p3 sends
        # the correct Host header when fetching the JWKS key set.
        lti_session = req_lib.Session()
        if settings.canvas_host_header:
            lti_session.headers["Host"] = settings.canvas_host_header

        message_launch: Any = FastAPIMessageLaunch(
            lti_request,
            tool_config,
            session_service=session_service,
            cookie_service=cookie_service,
            launch_data_storage=storage,
            requests_session=lti_session,
        )

        # Get validated claims
        launch_data: dict[str, Any] = message_launch.get_launch_data()

        # Debug: Log all claims to diagnose file menu data
        import json as json_debug
        logger.info(f"Full LTI claims: {json_debug.dumps(launch_data, indent=2, default=str)}")

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

        # Check if this is a course_navigation launch (dashboard)
        # Course navigation launches have context but no file menu data
        target_link_uri = parsed_launch.target_link_uri or ""
        is_dashboard_launch = (
            "dashboard" in target_link_uri
            or (parsed_launch.context and not parsed_launch.file_menu)
            or (parsed_launch.context and parsed_launch.file_menu and not parsed_launch.file_menu.file_id)
        )

        if is_dashboard_launch and parsed_launch.context:
            # Use numeric Canvas course ID (from custom claims or Canvas-specific
            # claim) rather than the LTI context hash. The file worker and config
            # service store data keyed by numeric course ID.
            course_id = (
                parsed_launch.canvas_course_id
                or (parsed_launch.file_menu and parsed_launch.file_menu.course_id)
                or parsed_launch.context.id
            )
            logger.info(f"Course navigation launch detected for course {course_id}")

            # Create LTI session in Redis
            session_data = {
                "course_id": course_id,
                "user_sub": parsed_launch.user.sub,
                "user_name": parsed_launch.user.name,
                "deployment_id": parsed_launch.deployment_id,
            }
            lti_session_token = await storage.create_session_async(session_data)

            # Render the dashboard directly instead of redirecting.
            # GET redirects trigger ngrok's browser warning page inside iframes
            # (Chrome blocks third-party cookies so the ngrok cookie doesn't
            # persist in the Canvas iframe context). Rendering inline avoids
            # the redirect chain entirely.
            return await _render_dashboard_inline(
                request, redis, job_service, course_id, lti_session_token,
            )

        # Try to extract file_id from various sources (fallback for local Canvas)
        file_id_from_url = None

        # Source 1: Try to decode lti_message_hint JWT (Canvas stores file context here)
        lti_message_hint = stored_state.get("lti_message_hint", "")
        if lti_message_hint and not file_id_from_url:
            try:
                # Decode JWT payload without verification (we just need to read it)
                import base64
                jwt_parts = lti_message_hint.split(".")
                if len(jwt_parts) >= 2:
                    payload_b64 = jwt_parts[1]
                    # Add padding if needed
                    padding = 4 - len(payload_b64) % 4
                    if padding != 4:
                        payload_b64 += "=" * padding
                    payload_bytes = base64.urlsafe_b64decode(payload_b64)
                    hint_data = json_module.loads(payload_bytes)
                    logger.debug(f"lti_message_hint payload: {hint_data}")

                    # Canvas may include file ID in various fields
                    if "file_id" in hint_data:
                        file_id_from_url = str(hint_data["file_id"])
                        logger.info(f"Extracted file_id from lti_message_hint: {file_id_from_url}")
                    elif "content_item" in hint_data:
                        # Some Canvas versions use content_item
                        content = hint_data.get("content_item", {})
                        if isinstance(content, dict) and "file_id" in content:
                            file_id_from_url = str(content["file_id"])
                            logger.info(f"Extracted file_id from content_item: {file_id_from_url}")
            except Exception as e:
                logger.debug(f"Could not decode lti_message_hint: {e}")

        # Source 2: Check stored target_link_uri for files[] parameter
        if not file_id_from_url:
            stored_target_uri = stored_state.get("target_link_uri", "")
            if stored_target_uri:
                from urllib.parse import parse_qs, urlparse
                parsed_uri = urlparse(stored_target_uri)
                query_params = parse_qs(parsed_uri.query)
                files_param = query_params.get("files[]") or query_params.get("files")
                if files_param:
                    file_id_from_url = files_param[0]
                    logger.info(f"Extracted file_id from target_link_uri: {file_id_from_url}")

        # Source 3: Check referrer header
        if not file_id_from_url:
            referer = request.headers.get("referer", "")
            if "files" in referer:
                from urllib.parse import parse_qs, urlparse
                parsed_ref = urlparse(referer)
                query_params = parse_qs(parsed_ref.query)
                files_param = query_params.get("files[]") or query_params.get("files")
                if files_param:
                    file_id_from_url = files_param[0]
                    logger.info(f"Extracted file_id from referer: {file_id_from_url}")

        # Source 4: Check launch_presentation return_url (may contain file context)
        if not file_id_from_url:
            return_url = launch_data.get("https://purl.imsglobal.org/spec/lti/claim/launch_presentation", {}).get("return_url", "")
            if return_url and "files" in return_url:
                from urllib.parse import parse_qs, urlparse
                parsed_return = urlparse(return_url)
                query_params = parse_qs(parsed_return.query)
                files_param = query_params.get("files[]") or query_params.get("files")
                if files_param:
                    file_id_from_url = files_param[0]
                    logger.info(f"Extracted file_id from return_url: {file_id_from_url}")

        # Process the launch (download file, create job)
        lti_service = LTIService(converter)

        try:
            response, s3_key = await lti_service.process_file_menu_launch(
                parsed_launch,
                file_id_from_url=file_id_from_url,
            )
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
        import traceback
        tb_str = traceback.format_exc()
        logger.error(f"LTI launch validation failed: {e}\n{tb_str}")
        # Include more detail in error for debugging
        error_detail = str(e) if str(e) else type(e).__name__
        return _error_response(f"Launch validation failed: {error_detail}")


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


async def _render_dashboard_inline(
    request: Request,
    redis: Redis,
    job_service: JobService,
    course_id: str,
    lti_session_token: str,
) -> HTMLResponse:
    """Render the dashboard index directly without redirecting.

    Avoids GET redirects that trigger ngrok's browser warning page
    inside Canvas iframes (third-party cookie blocking prevents the
    ngrok cookie from persisting in the iframe context).

    Args:
        request: FastAPI request object (used by Jinja2 template context)
        redis: Redis client for config service
        job_service: Job service for confidence scores
        course_id: Numeric Canvas course ID
        lti_session_token: LTI session token for Redis

    Returns:
        HTMLResponse with rendered dashboard
    """
    from pathlib import Path

    from ..canvas.course_config import CourseConfigService
    from ..canvas.dashboard import templates as dashboard_templates

    # Read CSS file content so we can inline it in the HTML.
    # External <link> tags fail inside Canvas iframes when served through
    # ngrok (the browser warning page returns text/html instead of CSS).
    css_path = Path(__file__).parent.parent / "canvas" / "static" / "dist" / "dashboard.css"
    inline_css = css_path.read_text() if css_path.exists() else ""

    config_service = CourseConfigService(redis)
    config = await config_service.get_config(course_id)
    processed_files = await config_service.list_processed_files(course_id)

    documents = []
    for file_id, pf in processed_files.items():
        publish_result = await config_service.get_publish_result(course_id, file_id)
        effective_status = pf.status
        canvas_page_url: str | None = None
        confidence_score: float | None = None

        if publish_result:
            effective_status = "published"
            canvas_page_url = publish_result.get("canvas_page_url")

        if pf.job_id:
            job = await job_service.get_job(pf.job_id)
            if job:
                score = job.get("confidence_score")
                if score is not None:
                    try:
                        confidence_score = float(score)
                    except (ValueError, TypeError):
                        pass

        documents.append(
            {
                "file_id": file_id,
                "filename": pf.original_filename,
                "status": effective_status,
                "confidence": confidence_score,
                "canvas_page_url": canvas_page_url,
                "processed_at": pf.processed_at,
                "job_id": pf.job_id,
            }
        )

    return dashboard_templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "title": "Document Dashboard",
            "course_id": course_id,
            "config": config,
            "documents": documents,
            "lti_session": lti_session_token,
            "inline_css": inline_css,
        },
    )


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


@router.get("/dashboard", response_model=None)
async def dashboard_launch(
    request: Request,
    redis: Redis = Depends(get_redis_client),
):
    """Handle LTI course_navigation launch for the instructor dashboard.

    Validates the LTI session token against Redis, extracts course context,
    and renders the dashboard index view directly.

    The course_navigation placement launches via the standard OIDC flow
    (login -> Canvas auth -> launch). After OIDC validation at /lti/launch,
    the user is redirected here with a session token that maps to their
    validated LTI session data in Redis.

    Query params:
        lti_session: Session token from the LTI launch
        course_id: Canvas course ID

    Returns:
        Dashboard index HTML if session is valid, error page otherwise.
    """
    from ..canvas.dashboard import templates as dashboard_templates

    lti_session = request.query_params.get("lti_session")
    course_id = request.query_params.get("course_id")

    if not lti_session or not course_id:
        return dashboard_templates.TemplateResponse(
            request,
            "base.html",
            {
                "title": "Session Error",
                "error_message": "Please launch this tool from Canvas.",
            },
            status_code=403,
        )

    # Validate the LTI session token against Redis
    storage = RedisLaunchDataStorage(redis)
    session_data = await storage.validate_session_async(lti_session)

    if session_data is None:
        logger.warning(f"Invalid or expired LTI session for dashboard launch: {lti_session[:8]}...")
        return dashboard_templates.TemplateResponse(
            request,
            "base.html",
            {
                "title": "Session Error",
                "error_message": "Your session has expired. Please relaunch from Canvas.",
            },
            status_code=403,
        )

    # Validate course_id matches the session (prevent course_id tampering)
    session_course_id = session_data.get("course_id")
    if session_course_id and session_course_id != course_id:
        logger.warning(
            f"Course ID mismatch: session={session_course_id}, requested={course_id}"
        )
        return dashboard_templates.TemplateResponse(
            request,
            "base.html",
            {
                "title": "Session Error",
                "error_message": "Course ID does not match your LTI session. Please relaunch from Canvas.",
            },
            status_code=403,
        )

    # Redirect to the dashboard index view
    dashboard_url = f"/lti/dashboard/{course_id}?lti_session={lti_session}"
    return RedirectResponse(url=dashboard_url, status_code=302)


@router.get("/health")
async def lti_health() -> dict[str, str]:
    """Health check for LTI endpoints.

    Returns:
        Health status
    """
    return {"status": "ok", "lti_enabled": str(settings.lti_enabled)}
