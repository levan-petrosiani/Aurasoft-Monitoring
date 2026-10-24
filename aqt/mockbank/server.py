"""
MockBank's listeners. XS2A, discovery and the token endpoint use HTTPS with an optional client certificate:
any TPP certificate is accepted (as in a sandbox with test CAs) and checked by the XS2A middleware, so a missing
certificate gets a proper XS2A error instead of a failed handshake. Python's ssl module cannot accept unknown client
certificates, so the HTTPS side uses pyOpenSSL. Browser-facing pages (OAuth login, dashboard) use plain HTTP.
Both listen on 127.0.0.1 and ::1 like Kestrel's ListenLocalhost.
"""

import io
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from OpenSSL import SSL

from .. import certificates
from .app import MockBankApp, Request, Response
from .options import MockBankOptions


class _TlsIO(io.RawIOBase):
    def __init__(self, conn: SSL.Connection, readable: bool):
        super().__init__()
        self._conn = conn
        self._readable = readable

    def readable(self):
        return self._readable

    def writable(self):
        return not self._readable

    def readinto(self, b):
        try:
            return self._conn.recv_into(b)
        except (SSL.ZeroReturnError, SSL.SysCallError):
            return 0
        except SSL.Error:
            return 0

    def write(self, b):
        self._conn.sendall(bytes(b))
        return len(b)


class _TlsSocket:
    """The socket-like object http.server's handler needs, on top of a pyOpenSSL connection."""

    def __init__(self, conn: SSL.Connection, raw: socket.socket):
        self._conn = conn
        self._raw = raw

    def makefile(self, mode, buffering=-1):
        if "r" in mode:
            return io.BufferedReader(_TlsIO(self._conn, True))
        return io.BufferedWriter(_TlsIO(self._conn, False))

    def sendall(self, data):
        self._conn.sendall(bytes(data))

    def settimeout(self, timeout):
        pass  # pyOpenSSL treats sockets with timeouts as non-blocking

    def setsockopt(self, *args):
        self._raw.setsockopt(*args)

    def fileno(self):
        return self._raw.fileno()

    def peer_certificate(self):
        return self._conn.get_peer_certificate(as_cryptography=True)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockBank"
    sys_version = ""

    def _dispatch(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else b""
        parts = urlsplit(self.path)

        peer = getattr(self.connection, "peer_certificate", None)
        request = Request(method=self.command, path=parts.path or "/",
                          query=parse_qs(parts.query, keep_blank_values=True),
                          headers=list(self.headers.items()), body=body,
                          client_cert=peer() if peer else None)

        try:
            response = self.server.app.handle(request)
        except Exception as ex:  # noqa: BLE001 - a bug in the mock must not kill the connection silently
            print(f"MockBank: {request.method} {request.path} failed: {type(ex).__name__}: {ex}", file=sys.stderr)
            response = Response(500, [("Content-Type", "text/plain")], b"MockBank internal error")

        self.send_response(response.status)
        for name, value in response.headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

        if not self.server.quiet:
            tpp = f" [{request.tpp_id}]" if request.tpp_id else ""
            query = f"?{parts.query}" if parts.query else ""
            print(f"MockBank: {request.method} {request.path}{query} -> {response.status}{tpp}", flush=True)

    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = do_HEAD = _dispatch

    def log_message(self, format, *args):
        pass


class _HttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, family, app: MockBankApp, quiet: bool):
        self.address_family = family
        self.app = app
        self.quiet = quiet
        super().__init__(address, _Handler)

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        if self.address_family == socket.AF_INET6:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        pass  # clients that disconnect abruptly are normal


class _TlsServer(_HttpServer):
    def __init__(self, address, family, app: MockBankApp, quiet: bool, ssl_context: SSL.Context):
        self.ssl_context = ssl_context
        super().__init__(address, family, app, quiet)

    def finish_request(self, request, client_address):
        conn = SSL.Connection(self.ssl_context, request)
        conn.set_accept_state()
        try:
            conn.do_handshake()
        except (SSL.Error, OSError):
            return
        try:
            self.RequestHandlerClass(_TlsSocket(conn, request), client_address, self)
        finally:
            try:
                conn.shutdown()
            except (SSL.Error, OSError):
                pass


def _ssl_context(server_certificate: certificates.Certificate) -> SSL.Context:
    ctx = SSL.Context(SSL.TLS_SERVER_METHOD)
    ctx.set_min_proto_version(SSL.TLS1_2_VERSION)
    ctx.use_certificate(server_certificate.cert)
    ctx.use_privatekey(server_certificate.key)
    # Ask for a client certificate but accept any (or none); the middleware checks it.
    ctx.set_verify(SSL.VERIFY_PEER, lambda conn, cert, errno, depth, ok: True)
    ctx.set_session_id(b"aqt-mockbank")
    return ctx


class MockBankHost:
    """Runs MockBank in background threads. Used by `aqt mock`, `aqt run --with-mock` and the portal."""

    def __init__(self, options: MockBankOptions | None = None, quiet: bool = False):
        self.options = options or MockBankOptions.load()
        self.quiet = quiet
        o = self.options
        # The bank's own certificates: TLS server certificate and the QSealC for response signatures.
        server_certificate = certificates.create_localhost_server_certificate(o.bank_organization_id)
        qsealc = certificates.create_tpp_certificate("ApiTestTool MockBank seal", o.bank_organization_id)
        self.app = MockBankApp(o, server_certificate, qsealc)
        self._servers: list[_HttpServer] = []
        self._threads: list[threading.Thread] = []
        self._ssl = _ssl_context(server_certificate)

    def start(self):
        o = self.options
        try:
            for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
                try:
                    self._servers.append(_TlsServer((host, o.https_port), family, self.app, self.quiet, self._ssl))
                    self._servers.append(_HttpServer((host, o.http_port), family, self.app, self.quiet))
                except OSError:
                    if family == socket.AF_INET6:
                        continue  # No IPv6 loopback on this machine; IPv4 is enough.
                    raise
        except OSError:
            self.stop()
            raise

        for server in self._servers:
            t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True,
                                 name=f"mockbank-{server.server_address[1]}")
            t.start()
            self._threads.append(t)

        if not self.quiet:
            self.print_banner()

    def stop(self):
        # shutdown() waits for serve_forever(), so only call it for servers whose loop was started.
        running = len(self._threads)
        for i, server in enumerate(self._servers):
            if i < running:
                server.shutdown()
            server.server_close()
        self._servers.clear()
        self._threads.clear()

    def print_banner(self):
        o = self.options
        print()
        print("  ApiTestTool MockBank (Python) is running (in-memory, certificates generated for this run)")
        print(f"    XS2A + token (mTLS) : {o.https_base_url}/v2/...")
        print(f"    OAuth discovery     : {o.https_base_url}/.well-known/oauth-authorization-server")
        print(f"    Login page          : {o.http_base_url}/oauth/authorize   (auto-approve: {o.auto_approve})")
        print(f"    Dashboard           : {o.http_base_url}/")
        print()
        print("  Test it with:  python -m aqt run --bank MOCK --sca-auto")
        print(flush=True)
