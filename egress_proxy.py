"""
egress_proxy.py — minimal default-deny HTTP/HTTPS forward proxy.

Why this exists
---------------
Sandboxed containers (see sandbox.py, SANDBOX_NETWORK_MODE=egress-proxy) are
attached only to an *internal* Docker network with no route to the outside
world. This proxy is the single container on that network that is also
attached to the default bridge — i.e. the only thing that can reach the
internet at all. It tunnels traffic on to the real destination only when the
destination hostname matches EGRESS_ALLOWLIST; everything else gets a 403.

This gives a real default-deny egress policy enforced at the network boundary,
not by trying to parse what an LLM-generated command might do.

How it handles HTTPS
--------------------
No TLS interception (no MITM, no certificates to install in the sandbox). For
`CONNECT host:port` requests the proxy reads only the plaintext request line
— the hostname the client is asking to reach — checks it against the
allowlist, and if allowed opens a raw TCP tunnel and pipes encrypted bytes
through unmodified. The proxy never sees certificates or decrypted payloads.

Plain HTTP requests are matched on the Host header (or absolute-form URI) the
same way.

Configuration (env vars, set by sandbox.py when it starts this container)
--------------------------------------------------------------------------
  EGRESS_ALLOWLIST    comma-separated hostnames; "*.example.com" matches any
                      subdomain (and the bare domain itself)
  EGRESS_PROXY_PORT   listen port (default 3128)
"""

import asyncio
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s egress-proxy %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("egress-proxy")

LISTEN_PORT = int(os.getenv("EGRESS_PROXY_PORT", "3128"))
ALLOWLIST = [h.strip().lower() for h in os.getenv("EGRESS_ALLOWLIST", "").split(",") if h.strip()]

_BLANK_LINES = (b"\r\n", b"\n", b"")


def _host_allowed(host: str) -> bool:
    """True if `host` matches an allowlist entry. "*.example.com" matches
    "example.com" and any "<sub>.example.com"; anything else is an exact match."""
    host = (host or "").lower().rstrip(".")
    for pattern in ALLOWLIST:
        if pattern.startswith("*."):
            base = pattern[2:]
            if host == base or host.endswith("." + base):
                return True
        elif host == pattern:
            return True
    return False


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _respond(writer: asyncio.StreamWriter, status: str, body: bytes):
    resp = (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Content-Type: text/plain\r\n"
        "Connection: close\r\n\r\n"
    ).encode("latin1") + body
    try:
        writer.write(resp)
        await writer.drain()
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _deny(writer, host: str, kind: str):
    log.info(f"DENY {kind} host={host!r} — not in allowlist")
    await _respond(writer, "403 Forbidden", b"egress blocked: host not in allowlist\n")


async def _bad_request(writer, detail: bytes):
    await _respond(writer, "400 Bad Request", detail)


async def _bad_gateway(writer, detail: str):
    await _respond(writer, "502 Bad Gateway", f"upstream connection failed: {detail}\n".encode("latin1"))


async def _handle_connect(request_line: str, reader, writer):
    """HTTPS path: CONNECT host:port HTTP/1.1 — tunnel opaque bytes once allowed."""
    try:
        target = request_line.split()[1]
        host, _, port_s = target.partition(":")
        port = int(port_s) if port_s else 443
    except Exception:
        await _bad_request(writer, b"malformed CONNECT request\n")
        return

    # Drain the rest of the CONNECT request's headers (we don't need them).
    while True:
        line = await reader.readline()
        if line in _BLANK_LINES:
            break

    if not _host_allowed(host):
        await _deny(writer, f"{host}:{port}", "connect")
        return

    try:
        remote_reader, remote_writer = await asyncio.open_connection(host, port)
    except Exception as e:
        await _bad_gateway(writer, str(e))
        return

    log.info(f"ALLOW connect host={host}:{port}")
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()

    await asyncio.gather(
        _pipe(reader, remote_writer),
        _pipe(remote_reader, writer),
    )


async def _handle_plain_http(first_line: bytes, reader, writer):
    """HTTP path: forward the request verbatim once the Host is allowed."""
    header_lines = [first_line]
    host_header = None
    while True:
        line = await reader.readline()
        if line in _BLANK_LINES:
            header_lines.append(b"\r\n")
            break
        header_lines.append(line)
        if line.lower().startswith(b"host:"):
            host_header = line.split(b":", 1)[1].strip().decode("latin1")

    target = host_header
    if target is None:
        try:
            uri = first_line.split()[1].decode("latin1")
            if uri.lower().startswith("http://"):
                target = uri[len("http://"):].split("/", 1)[0]
        except Exception:
            target = None

    host_only = (target or "").split(":")[0]
    if not host_only or not _host_allowed(host_only):
        await _deny(writer, host_only or "(unknown)", "http")
        return

    port = 80
    if target and ":" in target:
        try:
            port = int(target.split(":", 1)[1])
        except ValueError:
            pass

    try:
        remote_reader, remote_writer = await asyncio.open_connection(host_only, port)
    except Exception as e:
        await _bad_gateway(writer, str(e))
        return

    log.info(f"ALLOW http host={host_only}:{port}")
    remote_writer.write(b"".join(header_lines))
    await remote_writer.drain()

    await asyncio.gather(
        _pipe(reader, remote_writer),
        _pipe(remote_reader, writer),
    )


async def _handle_client(reader, writer):
    try:
        first_line = await reader.readline()
        if not first_line:
            writer.close()
            return
        method = first_line.split()[0].decode("latin1", "replace").upper()
        if method == "CONNECT":
            await _handle_connect(first_line.decode("latin1", "replace"), reader, writer)
        else:
            await _handle_plain_http(first_line, reader, writer)
    except Exception as e:
        log.info(f"client error: {e}")
        try:
            writer.close()
        except Exception:
            pass


async def main():
    log.info(
        f"listening on 0.0.0.0:{LISTEN_PORT} — "
        f"allowlist: {', '.join(ALLOWLIST) if ALLOWLIST else '(empty — everything denied)'}"
    )
    server = await asyncio.start_server(_handle_client, "0.0.0.0", LISTEN_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
