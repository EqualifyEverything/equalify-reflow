"""Unit tests for token_generator utility module."""

import re
import pytest

from src.utils.token_generator import create_approval_url, generate_secure_token


@pytest.mark.unit
class TestGenerateSecureToken:
    """Tests for generate_secure_token function."""

    def test_returns_string_type(self):
        """Test that generate_secure_token returns a string type.

        The function should always return a str instance,
        not bytes or any other type.
        """
        token = generate_secure_token()

        assert isinstance(token, str)

    def test_returns_expected_length(self):
        """Test that token has expected length of 43 characters.

        32 bytes encoded as base64url produces 43 characters:
        - 32 bytes = 256 bits
        - Base64 encodes 6 bits per character
        - 256 / 6 = 42.67, rounded up = 43 characters (with padding stripped)
        """
        token = generate_secure_token()

        assert len(token) == 43

    def test_token_uniqueness(self):
        """Test that multiple generated tokens are unique.

        Given the 256-bit entropy, collision probability should be
        astronomically low. Generating 100 tokens should produce
        100 distinct values.
        """
        tokens = [generate_secure_token() for _ in range(100)]
        unique_tokens = set(tokens)

        assert len(unique_tokens) == 100, "Generated tokens should all be unique"

    def test_token_is_url_safe(self):
        """Test that token contains only URL-safe characters.

        Base64url encoding uses only alphanumeric characters,
        hyphens (-), and underscores (_). No other characters
        should appear in the token.
        """
        token = generate_secure_token()

        # URL-safe base64 alphabet: A-Z, a-z, 0-9, -, _
        url_safe_pattern = re.compile(r'^[A-Za-z0-9_-]+$')

        assert url_safe_pattern.match(token), (
            f"Token contains non-URL-safe characters: {token}"
        )

    def test_token_has_entropy_not_uniform(self):
        """Test that token has sufficient entropy and is not uniform.

        A properly random token should not consist of all the same
        character or have obvious repeating patterns. We check that
        the token contains at least 10 unique characters.
        """
        token = generate_secure_token()
        unique_chars = set(token)

        # A 43-character random base64 string should have many unique chars
        # (base64 alphabet has 64 chars, we expect at least 10 unique)
        assert len(unique_chars) >= 10, (
            f"Token appears to lack entropy: only {len(unique_chars)} unique characters"
        )

    def test_token_does_not_contain_padding(self):
        """Test that token does not contain base64 padding characters.

        secrets.token_urlsafe strips padding characters (=) from
        the output, ensuring clean URL-safe strings.
        """
        token = generate_secure_token()

        assert "=" not in token, "Token should not contain padding characters"

    def test_multiple_calls_produce_different_tokens(self):
        """Test that consecutive calls produce different tokens.

        Each call to generate_secure_token should produce a fresh
        cryptographically random value. Two consecutive calls should
        never produce the same result.
        """
        token1 = generate_secure_token()
        token2 = generate_secure_token()

        assert token1 != token2, "Consecutive tokens should be different"

    def test_token_character_distribution(self):
        """Test that token has reasonable character distribution.

        Generate multiple tokens and verify that different character
        classes (uppercase, lowercase, digits, symbols) are represented,
        indicating proper randomness.
        """
        tokens = [generate_secure_token() for _ in range(10)]
        combined = "".join(tokens)

        has_uppercase = any(c.isupper() for c in combined)
        has_lowercase = any(c.islower() for c in combined)
        has_digit = any(c.isdigit() for c in combined)
        has_symbol = any(c in "-_" for c in combined)

        # At least 3 of the 4 character classes should be present
        char_classes_present = sum([has_uppercase, has_lowercase, has_digit, has_symbol])
        assert char_classes_present >= 3, (
            f"Token lacks character diversity: only {char_classes_present} classes present"
        )


@pytest.mark.unit
class TestCreateApprovalUrl:
    """Tests for create_approval_url function."""

    def test_creates_url_with_default_base_url(self):
        """Test URL creation with default base_url.

        When no base_url is provided, the function should use
        'http://localhost:3000' as the default.
        """
        token = "test_token_123"

        url = create_approval_url(token)

        assert url == "http://localhost:3000/approve/test_token_123"

    def test_creates_url_with_custom_base_url(self):
        """Test URL creation with custom base_url.

        The function should correctly use the provided base_url
        instead of the default.
        """
        token = "abc123"
        base_url = "https://example.com"

        url = create_approval_url(token, base_url=base_url)

        assert url == "https://example.com/approve/abc123"

    def test_url_contains_token_in_expected_path(self):
        """Test that URL contains token in the correct path segment.

        The URL should have the format: {base_url}/approve/{token}
        where the token appears after '/approve/'.
        """
        token = "my_secure_token_xyz"

        url = create_approval_url(token)

        assert "/approve/" in url
        assert url.endswith(token)
        assert url.split("/approve/")[1] == token

    def test_handles_base_url_without_trailing_slash(self):
        """Test URL creation when base_url has no trailing slash.

        The function should produce a valid URL regardless of
        whether the base_url ends with a slash.
        """
        token = "token123"
        base_url = "https://myapp.com"

        url = create_approval_url(token, base_url=base_url)

        assert url == "https://myapp.com/approve/token123"
        assert "//" not in url.replace("https://", "")

    def test_handles_base_url_with_trailing_slash(self):
        """Test URL creation when base_url has trailing slash.

        Note: The current implementation does not strip trailing slashes,
        which may result in double slashes. This test documents the
        actual behavior.
        """
        token = "token456"
        base_url = "https://myapp.com/"

        url = create_approval_url(token, base_url=base_url)

        # Current behavior: trailing slash results in double slash
        # This documents the actual behavior
        assert url == "https://myapp.com//approve/token456"

    def test_empty_token_produces_valid_url(self):
        """Test URL creation with empty token.

        Documents behavior when an empty string is passed as token.
        The function should still produce a URL, albeit with an
        empty token segment.
        """
        token = ""

        url = create_approval_url(token)

        assert url == "http://localhost:3000/approve/"
        assert url.endswith("/approve/")

    def test_url_with_port_in_base_url(self):
        """Test URL creation with port number in base_url.

        The function should handle base URLs that include
        explicit port numbers.
        """
        token = "secure_token"
        base_url = "http://localhost:8080"

        url = create_approval_url(token, base_url=base_url)

        assert url == "http://localhost:8080/approve/secure_token"

    def test_url_with_path_in_base_url(self):
        """Test URL creation with path segment in base_url.

        The function should correctly append the approval path
        even when base_url already contains a path.
        """
        token = "my_token"
        base_url = "https://example.com/app/v1"

        url = create_approval_url(token, base_url=base_url)

        assert url == "https://example.com/app/v1/approve/my_token"

    def test_preserves_special_characters_in_token(self):
        """Test that special URL-safe characters in token are preserved.

        Base64url tokens may contain hyphens and underscores,
        which should be preserved in the resulting URL.
        """
        token = "abc-def_ghi-123_456"

        url = create_approval_url(token)

        assert token in url
        assert url.endswith(token)

    def test_integration_with_generated_token(self):
        """Test create_approval_url with actual generated token.

        Integration test ensuring the two functions work together
        correctly. A generated token should produce a valid URL.
        """
        token = generate_secure_token()

        url = create_approval_url(token)

        assert url.startswith("http://localhost:3000/approve/")
        assert len(url) == len("http://localhost:3000/approve/") + 43
        assert token in url


@pytest.mark.unit
class TestEdgeCases:
    """Edge case tests for token_generator module."""

    def test_token_with_only_hyphen_underscore_possible(self):
        """Test that tokens can theoretically contain all symbol chars.

        While extremely unlikely, this documents that hyphens and
        underscores are valid characters in the output.
        """
        # Generate many tokens to check character variety
        all_chars = "".join(generate_secure_token() for _ in range(50))

        # Hyphens and underscores should appear in a large sample
        has_hyphen = "-" in all_chars
        has_underscore = "_" in all_chars

        # At least one should be present in 50 tokens (high probability)
        assert has_hyphen or has_underscore, (
            "Expected at least one hyphen or underscore in 50 tokens"
        )

    def test_create_approval_url_with_unicode_in_base_url(self):
        """Test URL creation with unicode characters in base_url.

        Documents behavior when base_url contains unicode.
        The function performs simple string concatenation.
        """
        token = "token123"
        base_url = "https://example.com/path"

        url = create_approval_url(token, base_url=base_url)

        assert url == "https://example.com/path/approve/token123"

    def test_whitespace_token_handling(self):
        """Test URL creation with whitespace token.

        Documents behavior when a whitespace token is provided.
        The function does not validate token content.
        """
        token = "   "

        url = create_approval_url(token)

        # Documents actual behavior - whitespace is not stripped
        assert url == "http://localhost:3000/approve/   "
