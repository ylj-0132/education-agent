"""One transport policy shared by the client and durable request accounting."""
import json
import ssl
from urllib.error import HTTPError, URLError


def retryable_transport(exc):
    if isinstance(exc,HTTPError):
        return exc.code in (502,503,504)
    if isinstance(exc,URLError):
        return isinstance(exc.reason,(TimeoutError,ConnectionError,ssl.SSLEOFError))
    return isinstance(exc,(TimeoutError,ConnectionError,ssl.SSLEOFError,json.JSONDecodeError))
