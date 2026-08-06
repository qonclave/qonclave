"""
http.py -- HTTP/1.1 request-response transport.

The default for rich media: a multi-megabyte frame belongs here rather than through a broker,
where it would starve every other subscriber.

Origin: the requests-based posting in hub/framework/transport.py.
"""

from __future__ import annotations
