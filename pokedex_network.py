"""Network helpers for cached Pokedex assets and PokeAPI calls."""

import ssl
import sys
import urllib.request


def create_ssl_context():
    context = ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(certifi.where())
    except ImportError:
        sys.stderr.write(
            "[pokedex] aviso: certifi no instalado, verificacion SSL "
            "deshabilitada. Instala 'certifi' para conexiones seguras.\n")
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def get_bytes(url, context, timeout=10, user_agent="Pokedex/1.0"):
    try:
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(request, context=context, timeout=timeout) as f:
            return f.read()
    except Exception:
        return None
