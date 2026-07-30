"""Side-effect-free Shopify OAuth query canonicalization."""
import urllib.parse


def canonical_hmac_message(params):
    """Match Shopify's current ProcessedQuery serializer byte-for-byte."""
    def encode(value):
        # WHATWG form encoding leaves `*`, encodes `~`, and Shopify then
        # canonicalizes form `+` spaces to `%20`.
        return (
            urllib.parse.quote_plus(str(value), safe="*-._")
            .replace("+", "%20")
            .replace("~", "%7E")
        )

    return "&".join(
        f"{encode(key)}={encode(value)}"
        for key, value in sorted(params.items())
        if key not in ("hmac", "signature")
    )
