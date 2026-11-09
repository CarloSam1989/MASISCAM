"""Helpers for exercising downloads without closing TestCase transactions."""


def consumir_respuesta(response):
    # TestClient already closes normal responses. Streaming responses must be
    # consumed through its wrapper, which disconnects close_old_connections
    # around request_finished. A second/manual close breaks PostgreSQL atomics.
    if getattr(response, "streaming", False):
        return b"".join(response.streaming_content)
    return response.content
