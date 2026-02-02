"""RSA key management for LTI 1.3 tool authentication.

This module handles:
- Loading RSA keys from PEM files
- Generating JWKS (JSON Web Key Set) for the /lti/jwks endpoint
- Optionally generating new RSA key pairs for initial setup
"""

import logging
from functools import lru_cache
from pathlib import Path

from jwcrypto import jwk

from ..config import settings

logger = logging.getLogger(__name__)


class LTIKeyError(Exception):
    """Raised when LTI key operations fail."""

    pass


@lru_cache(maxsize=1)
def load_private_key() -> jwk.JWK:
    """Load the tool's private RSA key from PEM file.

    The private key is used to sign JWTs sent to Canvas during the
    OIDC authentication flow.

    Returns:
        JWK object containing the private key

    Raises:
        LTIKeyError: If key file doesn't exist or is invalid
    """
    key_path = Path(settings.lti_private_key_path)

    if not key_path.exists():
        raise LTIKeyError(
            f"LTI private key not found at {key_path}. "
            "Run 'python -m src.lti.keys generate' to create keys."
        )

    try:
        with open(key_path, "rb") as f:
            pem_data = f.read()

        key = jwk.JWK.from_pem(pem_data)
        logger.debug(f"Loaded LTI private key from {key_path}")
        return key

    except Exception as e:
        raise LTIKeyError(f"Failed to load private key from {key_path}: {e}") from e


@lru_cache(maxsize=1)
def load_public_key() -> jwk.JWK:
    """Load the tool's public RSA key from PEM file.

    The public key is exposed via the /lti/jwks endpoint so Canvas
    can verify JWTs signed by our tool.

    Returns:
        JWK object containing the public key

    Raises:
        LTIKeyError: If key file doesn't exist or is invalid
    """
    key_path = Path(settings.lti_public_key_path)

    if not key_path.exists():
        raise LTIKeyError(
            f"LTI public key not found at {key_path}. "
            "Run 'python -m src.lti.keys generate' to create keys."
        )

    try:
        with open(key_path, "rb") as f:
            pem_data = f.read()

        key = jwk.JWK.from_pem(pem_data)
        logger.debug(f"Loaded LTI public key from {key_path}")
        return key

    except Exception as e:
        raise LTIKeyError(f"Failed to load public key from {key_path}: {e}") from e


def get_jwks() -> dict[str, list[dict[str, str]]]:
    """Generate JWKS JSON from the tool's public key.

    This is served at /lti/jwks for Canvas to fetch and use when
    verifying JWTs signed by our tool.

    Returns:
        JWKS dictionary with 'keys' array containing public key(s)

    Example response:
        {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": "key-1",
                    "n": "...",
                    "e": "AQAB"
                }
            ]
        }
    """
    public_key = load_public_key()

    # Export public key as JWK dict
    key_dict = public_key.export(private_key=False, as_dict=True)

    # Add required fields for LTI
    key_dict["use"] = "sig"  # Signature use
    key_dict["alg"] = "RS256"  # Algorithm
    key_dict["kid"] = "equalify-lti-key-1"  # Key ID for rotation support

    return {"keys": [key_dict]}


def get_private_key_pem() -> str:
    """Get the private key as PEM string for pylti1p3.

    Returns:
        Private key in PEM format
    """
    key = load_private_key()
    pem_bytes: bytes = key.export_to_pem(private_key=True, password=None)
    return pem_bytes.decode("utf-8")


def get_public_key_pem() -> str:
    """Get the public key as PEM string.

    Returns:
        Public key in PEM format
    """
    key = load_public_key()
    pem_bytes: bytes = key.export_to_pem(private_key=False, password=None)
    return pem_bytes.decode("utf-8")


def generate_rsa_keypair(
    private_key_path: str | None = None,
    public_key_path: str | None = None,
    key_size: int = 2048,
) -> tuple[str, str]:
    """Generate a new RSA key pair for LTI tool authentication.

    Use this during initial setup to create the RSA keys needed
    for LTI 1.3 authentication.

    Args:
        private_key_path: Path to save private key (default from settings)
        public_key_path: Path to save public key (default from settings)
        key_size: RSA key size in bits (default 2048)

    Returns:
        Tuple of (private_key_path, public_key_path)

    Raises:
        LTIKeyError: If key generation or file writing fails
    """
    private_path = Path(private_key_path or settings.lti_private_key_path)
    public_path = Path(public_key_path or settings.lti_public_key_path)

    try:
        # Generate RSA key pair
        key = jwk.JWK.generate(kty="RSA", size=key_size)

        # Ensure directories exist
        private_path.parent.mkdir(parents=True, exist_ok=True)
        public_path.parent.mkdir(parents=True, exist_ok=True)

        # Export and save private key
        private_pem = key.export_to_pem(private_key=True, password=None)
        with open(private_path, "wb") as f:
            f.write(private_pem)
        private_path.chmod(0o600)  # Restrict permissions

        # Export and save public key
        public_pem = key.export_to_pem(private_key=False, password=None)
        with open(public_path, "wb") as f:
            f.write(public_pem)

        logger.info(f"Generated LTI RSA key pair: {private_path}, {public_path}")

        # Clear the cache so new keys are loaded
        load_private_key.cache_clear()
        load_public_key.cache_clear()

        return str(private_path), str(public_path)

    except Exception as e:
        raise LTIKeyError(f"Failed to generate RSA key pair: {e}") from e


# CLI interface for key generation
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "generate":
        print("Generating LTI RSA key pair...")
        private_path, public_path = generate_rsa_keypair()
        print(f"Private key saved to: {private_path}")
        print(f"Public key saved to: {public_path}")
        print("\nAdd these to your LTI configuration in Canvas.")
    else:
        print("Usage: python -m src.lti.keys generate")
        print("  Generates RSA key pair for LTI 1.3 authentication")
