import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from printguard.printer import PrinterClient


class FakeWebSocket:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.close_code = 1000
        self.close_reason = ""
        self.recv_called = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed:
            raise StopAsyncIteration
        return await self.messages.get()

    async def recv(self):
        self.recv_called = True
        raise AssertionError("Der PrinterClient darf recv() nicht direkt verwenden.")

    async def send(self, raw):
        self.sent.append(raw)
        if raw == "pong":
            return
        payload = json.loads(raw)
        request_id = payload["Data"]["RequestID"]
        if payload["Data"]["Cmd"] == 0:
            await self.messages.put(json.dumps({
                "Topic": "sdcp/response/test",
                "Data": {
                    "RequestID": request_id,
                    "Cmd": 0,
                    "Data": {"Ack": 0},
                },
            }))
            await self.messages.put(json.dumps({
                "Topic": "sdcp/status/test",
                "Status": {
                    "CurrentStatus": [1],
                    "PrintInfo": {"Status": 13},
                },
            }))

    async def close(self):
        self.closed = True
        await self.messages.put("")

    async def drop(self):
        self.closed = True
        await self.messages.put("")


class PrinterClientTests(unittest.TestCase):
    def test_connect_uses_reader_for_initial_refresh(self):
        async def scenario():
            websocket = FakeWebSocket()
            await websocket.messages.put(json.dumps({
                "Topic": "sdcp/notify/test",
                "MainboardID": "board-1",
            }))
            client = PrinterClient("10.0.0.63")
            with patch(
                "printguard.printer.websockets.connect",
                new_callable=AsyncMock,
                return_value=websocket,
            ):
                await client.connect(timeout=1)
            self.assertIs(client.ws, websocket)
            self.assertEqual(client.mainboard_id, "board-1")
            self.assertEqual(client.print_status, 13)
            self.assertFalse(websocket.recv_called)
            self.assertTrue(any(
                json.loads(message)["Data"]["Cmd"] == 0
                for message in websocket.sent
                if message != "pong"
            ))
            await client.close()

        asyncio.run(scenario())

    def test_connect_logs_missing_mainboard_id(self):
        async def scenario():
            client = PrinterClient("10.0.0.63")
            websocket = FakeWebSocket()
            with patch(
                "printguard.printer.websockets.connect",
                new_callable=AsyncMock,
                return_value=websocket,
            ), patch("printguard.printer.log.error") as error_log:
                with self.assertRaisesRegex(TimeoutError, "Keine MainboardID"):
                    await client.connect(timeout=0.01)

            error_log.assert_any_call(
                "❌ Drucker-Handshake fehlgeschlagen: "
                "Keine MainboardID innerhalb des Zeitlimits empfangen."
            )

        asyncio.run(scenario())

    def test_pending_ack_is_released_when_connection_fails(self):
        async def scenario():
            client = PrinterClient("10.0.0.63")
            request_id = "request-1"
            client.command_events[request_id] = asyncio.Event()
            client.command_acks[request_id] = 0

            client._fail_pending_commands()
            ack = await client.wait_for_ack(request_id, timeout=0.1)

            self.assertIsNone(ack)
            self.assertNotIn(request_id, client.command_events)
            self.assertNotIn(request_id, client.command_acks)

        asyncio.run(scenario())

    def test_wait_for_ack_timeout_cleans_up_request(self):
        async def scenario():
            client = PrinterClient("10.0.0.63")
            request_id = "request-timeout"
            client.command_events[request_id] = asyncio.Event()

            ack = await client.wait_for_ack(request_id, timeout=0.01)

            self.assertIsNone(ack)
            self.assertNotIn(request_id, client.command_events)

        asyncio.run(scenario())

    def test_reader_reconnects_and_refreshes_on_connection_drop(self):
        async def scenario():
            first = FakeWebSocket()
            second = FakeWebSocket()
            await first.messages.put(json.dumps({
                "Topic": "sdcp/notify/test",
                "MainboardID": "board-1",
            }))
            await second.messages.put(json.dumps({
                "Topic": "sdcp/notify/test",
                "MainboardID": "board-2",
            }))
            client = PrinterClient("10.0.0.63")
            with patch(
                "printguard.printer.websockets.connect",
                new_callable=AsyncMock,
                side_effect=[first, second],
            ) as connect_mock:
                await client.connect(timeout=1)
                await first.drop()
                deadline = asyncio.get_running_loop().time() + 5
                while client.reconnect_count < 1:
                    if asyncio.get_running_loop().time() >= deadline:
                        self.fail("Der automatische Reconnect wurde nicht abgeschlossen.")
                    await asyncio.sleep(0.05)

            self.assertEqual(client.mainboard_id, "board-2")
            self.assertEqual(client.print_status, 13)
            self.assertEqual(connect_mock.await_count, 2)
            self.assertIs(client.ws, second)
            await client.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()