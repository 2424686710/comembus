"""Unix Domain Socket transport for CoMemBus."""

from __future__ import annotations

import os
import socket
import struct
import threading
from typing import Any, Callable, Dict, Optional, Set

from ..metrics.recorder import MetricsRecorder
from ..protocol import (
    ConnectionClosedError,
    EmptyFrameError,
    FRAME_HEADER_SIZE,
    FrameTooLargeError,
    decode_frame_from_socket,
    decode_json,
    encode_frame,
)
from .. import protocol as protocol_module

RequestHandler = Callable[[Dict[str, Any]], Dict[str, Any]]


def send_frame(
    sock: socket.socket,
    message_dict: Dict[str, Any],
    metrics_recorder: Optional[MetricsRecorder] = None,
) -> None:
    """Send one protocol frame over a connected socket."""

    frame = encode_frame(message_dict)
    sock.sendall(frame)
    if metrics_recorder is not None:
        metrics_recorder.record_sent(len(frame))


def recv_frame(
    sock: socket.socket,
    metrics_recorder: Optional[MetricsRecorder] = None,
) -> Dict[str, Any]:
    """Receive one protocol frame from a connected socket."""

    if metrics_recorder is None:
        return decode_frame_from_socket(sock)

    header = _recv_exact(sock, FRAME_HEADER_SIZE)
    frame_length = struct.unpack(">I", header)[0]
    if frame_length == 0:
        raise EmptyFrameError("received empty frame")
    if frame_length > protocol_module.MAX_FRAME_SIZE:
        raise FrameTooLargeError(
            f"frame body exceeds limit: {frame_length} > {protocol_module.MAX_FRAME_SIZE}"
        )
    body = _recv_exact(sock, frame_length)
    metrics_recorder.record_received(FRAME_HEADER_SIZE + frame_length)
    return decode_json(body)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        try:
            chunk = sock.recv(size - len(chunks))
        except socket.timeout as exc:
            raise ConnectionClosedError("socket read timed out") from exc
        if not chunk:
            if not chunks:
                raise ConnectionClosedError("peer closed the connection")
            raise ConnectionClosedError("connection closed mid-frame")
        chunks.extend(chunk)
    return bytes(chunks)


def connect_unix_socket(socket_path: str, timeout: float = 5.0) -> socket.socket:
    """Connect to a Unix Domain Socket server."""

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    client.connect(socket_path)
    client.settimeout(None)
    return client


class UnixDomainSocketServer:
    """Threaded AF_UNIX frame server."""

    def __init__(
        self,
        socket_path: str,
        handler: RequestHandler,
        backlog: int = 16,
        accept_timeout: float = 0.5,
        metrics_recorder: Optional[MetricsRecorder] = None,
    ) -> None:
        self.socket_path = socket_path
        self._handler = handler
        self._backlog = backlog
        self._accept_timeout = accept_timeout
        self._metrics_recorder = metrics_recorder
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._server_socket: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._client_threads: Set[threading.Thread] = set()
        self._client_sockets: Set[socket.socket] = set()

    def start(self) -> None:
        if self._server_socket is not None:
            return
        socket_dir = os.path.dirname(self.socket_path)
        if socket_dir:
            os.makedirs(socket_dir, exist_ok=True)
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_socket.bind(self.socket_path)
        server_socket.listen(self._backlog)
        server_socket.settimeout(self._accept_timeout)
        self._server_socket = server_socket
        self._stop_event.clear()
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def stop(self) -> None:
        self._stop_event.set()

        server_socket = self._server_socket
        self._server_socket = None
        if server_socket is not None:
            try:
                server_socket.close()
            except OSError:
                pass

        with self._lock:
            client_sockets = list(self._client_sockets)
            client_threads = list(self._client_threads)

        for client_socket in client_sockets:
            try:
                client_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client_socket.close()
            except OSError:
                pass

        current_thread = threading.current_thread()
        if self._accept_thread is not None and self._accept_thread is not current_thread:
            self._accept_thread.join(timeout=2.0)
        self._accept_thread = None

        for thread in client_threads:
            if thread is not current_thread:
                thread.join(timeout=2.0)

        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            server_socket = self._server_socket
            if server_socket is None:
                break
            try:
                client_socket, _ = server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            with self._lock:
                self._client_sockets.add(client_socket)
            thread = threading.Thread(
                target=self._serve_client, args=(client_socket,), daemon=True
            )
            with self._lock:
                self._client_threads.add(thread)
            thread.start()

    def _serve_client(self, client_socket: socket.socket) -> None:
        current_thread = threading.current_thread()
        try:
            while not self._stop_event.is_set():
                try:
                    request = recv_frame(client_socket, self._metrics_recorder)
                except ConnectionClosedError:
                    break
                except Exception as exc:
                    self._safe_send(client_socket, {"ok": False, "error": str(exc)})
                    break

                try:
                    response = self._handler(request)
                except Exception as exc:
                    response = {"ok": False, "error": f"internal server error: {exc}"}
                if not self._safe_send(client_socket, response):
                    break
        finally:
            with self._lock:
                self._client_sockets.discard(client_socket)
                self._client_threads.discard(current_thread)
            try:
                client_socket.close()
            except OSError:
                pass

    def _safe_send(self, client_socket: socket.socket, response: Dict[str, Any]) -> bool:
        try:
            send_frame(client_socket, response, self._metrics_recorder)
            return True
        except OSError:
            return False
