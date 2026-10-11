"""Windows-path regressions; runnable on Windows and Linux without a router."""
import hashlib
import ipaddress
import json
import os
import socket
import tempfile
import threading
import unittest
from unittest import mock

import chain
import hostnet
import install
import winssh


class InstallerWindowsTests(unittest.TestCase):
    def setUp(self):
        self.windows = mock.patch.object(hostnet, "is_windows", return_value=True)
        self.windows.start()
        self.addCleanup(self.windows.stop)

    def test_route_delegation(self):
        with mock.patch.object(hostnet, "route", return_value=("192.168.31.2", "Ethernet")):
            self.assertEqual(chain.local_ip("192.168.31.1"), "192.168.31.2")
            self.assertEqual(chain.local_interface("192.168.31.1"), "Ethernet")

    def test_route_error_not_silenced(self):
        with mock.patch.object(hostnet, "route", side_effect=hostnet.NetworkError("no route")):
            with self.assertRaisesRegex(chain.ChainError, "no route"):
                chain.local_interface("192.168.31.1")

    def test_transport_and_ipv4(self):
        with mock.patch.object(hostnet, "adapter", return_value={"wireless": True, "ipv4": ["192.168.31.2/24"]}):
            self.assertEqual(install.detect_transport("Wi-Fi"), "wifi")
            self.assertEqual(install.interface_ipv4_networks("Wi-Fi"),
                             [ipaddress.ip_interface("192.168.31.2/24")])

    def test_callback_keeps_wrong_route_guard(self):
        with mock.patch.object(hostnet, "route", return_value=("192.168.31.2", "VPN")):
            with self.assertRaisesRegex(install.Abort, "does not match"):
                install.callback_address("192.168.31.1", "Ethernet")

    def test_callback_keeps_ownership_guard(self):
        with mock.patch.object(hostnet, "route", return_value=("192.168.31.2", "Ethernet")), \
             mock.patch.object(hostnet, "adapter", return_value={"ipv4": ["192.168.31.2/24"]}):
            self.assertEqual(install.callback_address("192.168.31.1", "Ethernet"), "192.168.31.2")
            with self.assertRaisesRegex(install.Abort, "not assigned"):
                install.callback_address("192.168.31.1", "Ethernet", "192.168.31.3")

    def test_manifest_without_posix_calls(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(os, "fchmod", create=True, side_effect=AssertionError("POSIX fchmod")):
            path = os.path.join(directory, "resume.json")
            install._write_resume_data(path, {"phase": "prepared"})
            install._write_resume_data(path, {"phase": "pivot-complete"})
            with open(path) as source:
                self.assertEqual(json.load(source)["phase"], "pivot-complete")
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_router_scripts_are_utf8_with_unix_newlines(self):
        with tempfile.TemporaryDirectory() as directory:
            script = install.write_tmp(directory, "test.sh", "#!/bin/sh\necho 'Привет'\n")
            with open(script, "rb") as source:
                data = source.read()
            self.assertNotIn(b"\r", data)
            self.assertEqual(data.decode("utf-8"), "#!/bin/sh\necho 'Привет'\n")

    def test_linklocal_requires_unique_dropbear(self):
        with mock.patch.object(hostnet, "discover_candidates", return_value=["fe80::1%12", "fe80::2%12"]), \
             mock.patch.object(install, "ssh_banner", side_effect=[("SSH-2.0-OpenSSH", None), ("SSH-2.0-dropbear", None)]):
            self.assertEqual(install.discover_linklocal("Wi-Fi"), "fe80::2%12")
        with mock.patch.object(hostnet, "discover_candidates", return_value=["fe80::1%12", "fe80::2%12"]), \
             mock.patch.object(install, "ssh_banner", return_value=("SSH-2.0-dropbear", None)):
            with self.assertRaises(install.Abort):
                install.discover_linklocal("Wi-Fi")

    def test_default_gateway_collision_still_blocked(self):
        with mock.patch.object(hostnet, "default_gateways", return_value={"192.168.1.1": "Ethernet"}), \
             mock.patch.object(install, "ssh_banner") as banner:
            with self.assertRaisesRegex(install.Abort, "own default gateway"):
                install.check_is_openwrt_ram("192.168.1.1")
            banner.assert_not_called()

    def test_list_interfaces_read_only(self):
        with mock.patch.object(hostnet, "adapters", return_value=[{"index": 12, "name": "Wi-Fi", "wireless": True, "ipv4": []}]), \
             mock.patch.object(chain, "init_info") as contact, \
             mock.patch.object(install, "log"), \
             mock.patch.object(install.os, "makedirs") as output:
            self.assertEqual(install.main(["--list-interfaces"]), 0)
            contact.assert_not_called()
            output.assert_not_called()

    def test_password_hash_without_openssl(self):
        from passlib.hash import sha512_crypt
        with mock.patch.object(install.subprocess, "run", side_effect=AssertionError("openssl called")):
            result = install.password_hash("test-password")
            self.assertTrue(sha512_crypt.verify("test-password", result))
            self.assertFalse(sha512_crypt.verify("wrong", result))


class SSHCleanupTests(unittest.TestCase):
    """Force the disconnect race; real loopback timing is nondeterministic."""

    def session(self, error):
        channel = mock.Mock()
        channel.recv_ready.return_value = False
        channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        channel.close.side_effect = error
        transport = mock.Mock()
        transport.open_session.return_value = channel
        return transport, channel

    def test_run_preserves_result_and_exit_status(self):
        for error in (EOFError(), ConnectionResetError("peer closed")):
            with self.subTest(error=type(error).__name__):
                transport, channel = self.session(error)
                channel.recv_ready.side_effect = [True, False, False]
                channel.recv.return_value = b"command output\n"
                channel.recv_exit_status.return_value = 7
                with mock.patch.object(winssh, "_authenticated", return_value=transport):
                    self.assertEqual(winssh.run("router.test", "test", [""]),
                                     (7, "command output"))
                channel.close.assert_called_once_with()
                transport.close.assert_called_once_with()

    def test_run_preserves_command_error(self):
        transport, channel = self.session(EOFError())
        channel.exec_command.side_effect = OSError("command connection lost")
        with mock.patch.object(winssh, "_authenticated", return_value=transport):
            with self.assertRaisesRegex(winssh.SSHError, "command connection lost"):
                winssh.run("router.test", "test", [""])
        transport.close.assert_called_once_with()

    def test_upload_preserves_success(self):
        for error in (EOFError(), BrokenPipeError("peer closed")):
            with self.subTest(error=type(error).__name__):
                transport, channel = self.session(error)
                channel.recv.return_value = b"\x00"
                with tempfile.TemporaryDirectory() as directory:
                    local = os.path.join(directory, "image.bin")
                    with open(local, "wb") as output:
                        output.write(b"firmware")
                    with mock.patch.object(winssh, "_authenticated", return_value=transport):
                        self.assertIsNone(winssh.upload("router.test", local, "/tmp/image.bin", [""]))
                channel.close.assert_called_once_with()
                transport.close.assert_called_once_with()

    def test_upload_preserves_receiver_error(self):
        transport, channel = self.session(EOFError())
        channel.recv.side_effect = [b"\x01", b"n", b"o", b"\n"]
        with mock.patch.object(winssh, "_authenticated", return_value=transport):
            with self.assertRaisesRegex(winssh.SSHError, "receiver rejected transfer: no"):
                winssh.upload("router.test", "unused", "/tmp/image.bin", [""])
        transport.close.assert_called_once_with()

    def test_open_session_error_still_closes_transport(self):
        transport, _ = self.session(None)
        transport.open_session.side_effect = EOFError("session connection lost")
        transport.close.side_effect = EOFError("already closed")
        with mock.patch.object(winssh, "_authenticated", return_value=transport):
            with self.assertRaisesRegex(winssh.SSHError, "session connection lost"):
                winssh.run("router.test", "test", [""])
        transport.close.assert_called_once_with()

    def test_unexpected_cleanup_error_is_not_silenced(self):
        transport, channel = self.session(ValueError("cleanup bug"))
        with self.assertRaisesRegex(ValueError, "cleanup bug"):
            winssh._close_session(transport, channel)
        transport.close.assert_called_once_with()


class SSHTransportTests(unittest.TestCase):
    """Real loopback SSH/SCP exchange, not just mocked transport calls."""
    @classmethod
    def setUpClass(cls):
        cls.paramiko = winssh.dependencies()
        cls.key = cls.paramiko.RSAKey.generate(2048)

    def server(self, handler, none_auth=False):
        paramiko = self.paramiko
        command_ready = threading.Event()
        commands, errors = [], []

        class Server(paramiko.ServerInterface):
            def check_auth_none(self, username):
                return paramiko.AUTH_SUCCESSFUL if none_auth else paramiko.AUTH_FAILED

            def get_allowed_auths(self, username):
                return "none" if none_auth else "password"

            def check_auth_password(self, username, password):
                return paramiko.AUTH_SUCCESSFUL if username == "root" and password == "secret" else paramiko.AUTH_FAILED

            def check_channel_request(self, kind, chanid):
                return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

            def check_channel_exec_request(self, channel, command):
                commands.append(command.decode())
                command_ready.set()
                return True

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        listener.settimeout(10)
        port = listener.getsockname()[1]

        def serve():
            transport = None
            try:
                # Wrong-password clients close without opening a channel.
                for _ in range(3):
                    connection, _ = listener.accept()
                    transport = paramiko.Transport(connection)
                    transport.add_server_key(self.key)
                    transport.start_server(server=Server())
                    channel = transport.accept(5)
                    if channel is None:
                        transport.close()
                        continue
                    channel.settimeout(10)
                    if not command_ready.wait(5):
                        raise AssertionError("no exec request")
                    handler(channel, commands[-1])
                    channel.close()
                    break
            except Exception as exc:
                errors.append(exc)
            finally:
                if transport:
                    transport.close()
                listener.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        original_connect = socket.create_connection

        def connect(address, **kwargs):
            self.assertEqual(address, ("router.test", 22))
            return original_connect(("127.0.0.1", port), **kwargs)

        return thread, errors, mock.patch.object(winssh.socket, "create_connection", side_effect=connect)

    def finish(self, thread, errors):
        thread.join(15)
        self.assertFalse(thread.is_alive(), "SSH server did not finish")
        self.assertEqual(errors, [])

    def test_password_retry_and_large_streams(self):
        def handler(channel, command):
            self.assertEqual(command, "read-only-test")
            channel.sendall(b"A" * 180000)
            channel.sendall_stderr(b"B" * 180000)
            channel.send_exit_status(7)
        thread, errors, connection = self.server(handler)
        with connection:
            code, output = winssh.run("router.test", "read-only-test", ["wrong", "secret"], timeout=15)
        self.assertEqual(code, 7)
        self.assertEqual(output, "A" * 180000 + "B" * 180000)
        self.finish(thread, errors)

    def test_passwordless_ram_auth(self):
        def handler(channel, command):
            channel.sendall(b"up\n")
            channel.send_exit_status(0)
        thread, errors, connection = self.server(handler, none_auth=True)
        with connection:
            self.assertEqual(winssh.run("router.test", "echo up", [""]), (0, "up"))
        self.finish(thread, errors)

    def test_legacy_scp_binary_transfer(self):
        payload = os.urandom(200001)
        received = []

        def exact(channel, count):
            data = bytearray()
            while len(data) < count:
                chunk = channel.recv(count - len(data))
                if not chunk:
                    raise AssertionError("unexpected EOF")
                data.extend(chunk)
            return bytes(data)

        def handler(channel, command):
            self.assertEqual(command, "scp -t '/tmp/image with spaces.bin'")
            channel.sendall(b"\x00")
            header = bytearray()
            while not header.endswith(b"\n"):
                header.extend(exact(channel, 1))
            self.assertEqual(header, f"C0600 {len(payload)} upload.bin\n".encode())
            channel.sendall(b"\x00")
            received.append(exact(channel, len(payload)))
            self.assertEqual(exact(channel, 1), b"\x00")
            channel.sendall(b"\x00")
            self.assertEqual(channel.recv(1), b"")
            channel.send_exit_status(0)

        thread, errors, connection = self.server(handler)
        with tempfile.TemporaryDirectory() as directory:
            local = os.path.join(directory, "firmware.bin")
            with open(local, "wb") as output:
                output.write(payload)
            with connection:
                winssh.upload("router.test", local, "/tmp/image with spaces.bin", ["secret"])
        self.finish(thread, errors)
        self.assertEqual(hashlib.sha256(received[0]).digest(), hashlib.sha256(payload).digest())

    def test_receiver_error(self):
        channel = mock.Mock()
        channel.recv.side_effect = [b"\x01", b"n", b"o", b"\n"]
        with self.assertRaisesRegex(winssh.SSHError, "no"):
            winssh._ack(channel)

    def test_invalid_destination_before_connect(self):
        with mock.patch.object(winssh, "_authenticated") as connect:
            for remote in ("relative", "/tmp/evil\nname", "/tmp/evil\x00name"):
                with self.assertRaises(winssh.SSHError):
                    winssh.upload("router.test", "unused", remote, [""])
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
