"""Secure token generation for approval workflows."""

import secrets


def generate_secure_token() -> str:
    """Generate cryptographically secure approval token.

    Uses 256-bit entropy for secure approval URL tokens.
    Token is URL-safe and suitable for use in approval links.

    Returns:
        str: URL-safe token string (43 characters)

    Example:
        >>> token = generate_secure_token()
        >>> len(token)
        43
        >>> # Example token: 'k8zR5qN3p7sT2wY9mX4jV6hL1nB0cF8aG5dK3eM2'
    """
    return secrets.token_urlsafe(32)  # 32 bytes = 256 bits


