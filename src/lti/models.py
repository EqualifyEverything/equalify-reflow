"""Pydantic models for LTI 1.3 claims and responses.

These models represent the data structures used in LTI 1.3 communication,
including launch claims, file menu context, and API responses.
"""

from pydantic import BaseModel, Field


class LTIUser(BaseModel):
    """LTI user information from launch claims."""

    sub: str = Field(..., description="User identifier (Canvas user ID)")
    name: str | None = Field(None, description="Full name")
    given_name: str | None = Field(None, description="First name")
    family_name: str | None = Field(None, description="Last name")
    email: str | None = Field(None, description="Email address")


class LTIContext(BaseModel):
    """LTI context (course) information from launch claims."""

    id: str = Field(..., description="Context identifier (Canvas course ID)")
    label: str | None = Field(None, description="Course code/label")
    title: str | None = Field(None, description="Course title")
    type: list[str] | None = Field(None, description="Context types (e.g., CourseSection)")


class LTIResourceLink(BaseModel):
    """LTI resource link information."""

    id: str = Field(..., description="Resource link identifier")
    title: str | None = Field(None, description="Resource link title")
    description: str | None = Field(None, description="Resource link description")


class FileMenuContent(BaseModel):
    """Content from Canvas file_menu placement.

    When launched from the file menu, Canvas provides information about
    the selected file through the custom LTI claims.
    """

    file_id: str | None = Field(None, description="Canvas file ID")
    file_name: str | None = Field(None, description="Original filename")
    file_content_type: str | None = Field(None, description="MIME type of file")
    file_download_url: str | None = Field(None, description="URL to download the file")
    course_id: str | None = Field(None, description="Canvas course ID")


class LTILaunchData(BaseModel):
    """Parsed LTI 1.3 launch data.

    Contains all relevant information extracted from an LTI launch,
    including user, context, and placement-specific data.
    """

    # Standard LTI claims
    deployment_id: str = Field(..., description="LTI deployment ID")
    target_link_uri: str = Field(..., description="Target launch URL")
    message_type: str = Field(..., description="LTI message type (e.g., LtiResourceLinkRequest)")

    # User info
    user: LTIUser = Field(..., description="User information")

    # Context info
    context: LTIContext | None = Field(None, description="Course/context information")

    # Resource link
    resource_link: LTIResourceLink | None = Field(None, description="Resource link information")

    # Placement-specific data
    file_menu: FileMenuContent | None = Field(None, description="File menu content (if launched from file menu)")

    # Canvas-specific
    canvas_user_id: str | None = Field(None, description="Canvas-specific user ID")
    canvas_course_id: str | None = Field(None, description="Canvas-specific course ID")

    # Raw claims for debugging
    raw_claims: dict | None = Field(None, description="Raw JWT claims for debugging")

    class Config:
        """Pydantic config."""

        extra = "ignore"


class LTILaunchResponse(BaseModel):
    """Response after successful LTI launch processing."""

    job_id: str = Field(..., description="Created job ID")
    viewer_url: str = Field(..., description="URL to redirect to for viewing")
    file_name: str | None = Field(None, description="Original filename from Canvas")
    message: str = Field("Launch successful", description="Status message")


class LTIConfigResponse(BaseModel):
    """LTI tool configuration for Canvas admin setup.

    This is served at /lti/config to help Canvas admins configure
    the Developer Key with correct endpoints.
    """

    title: str = Field("Equalify PDF Reflow", description="Tool title")
    description: str = Field(
        "Convert PDF documents into accessible, reflowable HTML",
        description="Tool description",
    )
    target_link_uri: str = Field(..., description="Launch URL")
    oidc_initiation_url: str = Field(..., description="OIDC login initiation URL")
    public_jwk_url: str = Field(..., description="Tool's JWKS URL")
    scopes: list[str] = Field(
        default_factory=lambda: [
            "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
            "https://purl.imsglobal.org/spec/lti-ags/scope/result.readonly",
        ],
        description="Required LTI scopes",
    )
    extensions: list[dict] = Field(
        default_factory=list,
        description="Canvas-specific extensions",
    )


class LTIErrorResponse(BaseModel):
    """Error response for LTI operations."""

    error: str = Field(..., description="Error code")
    error_description: str = Field(..., description="Human-readable error description")
    details: dict | None = Field(None, description="Additional error details")


class OIDCLoginRequest(BaseModel):
    """OIDC login initiation request from Canvas.

    Canvas sends this data when initiating the LTI launch flow.
    """

    iss: str = Field(..., description="Issuer (Canvas URL)")
    login_hint: str = Field(..., description="Opaque login hint from Canvas")
    target_link_uri: str = Field(..., description="Where to redirect after auth")
    lti_message_hint: str | None = Field(None, description="Opaque message hint")
    client_id: str | None = Field(None, description="Client ID (for multi-tenancy)")
    lti_deployment_id: str | None = Field(None, description="Deployment ID")


class OIDCAuthResponse(BaseModel):
    """OIDC authentication response for redirect.

    After OIDC login initiation, we redirect Canvas to this URL
    with these parameters.
    """

    url: str = Field(..., description="Canvas authorization URL")
    scope: str = Field("openid", description="OIDC scope")
    response_type: str = Field("id_token", description="Response type")
    response_mode: str = Field("form_post", description="Response mode")
    client_id: str = Field(..., description="Tool's client ID")
    redirect_uri: str = Field(..., description="Tool's launch URL")
    state: str = Field(..., description="State parameter for CSRF protection")
    nonce: str = Field(..., description="Nonce for replay protection")
    login_hint: str = Field(..., description="Login hint from initiation")
    lti_message_hint: str | None = Field(None, description="Message hint from initiation")
