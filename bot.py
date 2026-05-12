import asyncio
import importlib
import itertools
import json
import logging
import websockets
import base64
import aiohttp
import time
import traceback
import os
import requests
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# TCP port-probe connections (e.g. from wait_port.ps1) trigger a harmless
# "opening handshake failed" warning — suppress it so logs stay clean.
logging.getLogger("websockets.server").setLevel(logging.ERROR)

from plugins import *
from config import HOST, PORT, PROXY_URL, SUPER_USERS

Host = HOST
Port = PORT
websocket_url = f"ws://{Host}:{Port}"

_ECHO_TIMEOUT = 10  # seconds


# ── BotRuntime: encapsulates all shared mutable state ──────────────────────────

@dataclass
class BotRuntime:
    """Container for bot-wide shared state, eliminating module-level globals.

    Created once in ``serve_forever`` and passed explicitly through closures,
    so concurrent coroutines never race on mutable module‑level variables.
    """
    bot_qq: int = 0
    super_users: List[int] = field(default_factory=lambda: list(SUPER_USERS))
    proxy_url: Optional[str] = PROXY_URL
    echo_dict: Dict[str, Any] = field(default_factory=dict)
    _echo_counter: itertools.count = field(default_factory=itertools.count)
    running_tasks: List[Dict[str, Any]] = field(default_factory=list)
    crash_signal: bool = False
    server_close_signal: bool = False

    # ── echo helpers ────────────────────────────────────────────────────────

    def _next_echo(self) -> str:
        return str(next(self._echo_counter))

    async def _wait_for_echo(self, self_echo: str):
        """Wait for a NapCat echo response with a timeout.

        Returns the response dict on success, or None if the wait timed out
        or the crash signal was set.  On timeout / crash the echo entry is
        cleaned from echo_dict so it cannot leak.
        """
        deadline = time.time() + _ECHO_TIMEOUT
        while self_echo not in self.echo_dict and not self.crash_signal:
            if time.time() >= deadline:
                print(f"[NapCat] Echo {self_echo} timed out after {_ECHO_TIMEOUT}s, cleaning up")
                self.echo_dict.pop(self_echo, None)
                return None
            await asyncio.sleep(0.1)

        # crash_signal may have been set
        if self_echo not in self.echo_dict:
            return None

        response = self.echo_dict[self_echo]
        del self.echo_dict[self_echo]
        return response


# ── Bot interface functions (receive runtime via closures) ─────────────────────

def _make_interfaces(runtime: BotRuntime):
    """Build the legacy ``interfaces`` dict with closures that close over *runtime*."""

    def test_if_super_user(user_id):
        result = user_id in runtime.super_users
        return result

    async def get_message_by_id(ws, message_id):
        self_echo = runtime._next_echo()
        json_data = {
            "action": "get_msg",
            "params": {
                "message_id": message_id
            },
            "echo": self_echo
        }
        await ws.send(json.dumps(json_data))

        response = await runtime._wait_for_echo(self_echo)
        if response is None:
            return None
        print("[NapCat]Response:", response)
        if "data" in response:
            return response["data"]
        return None

    async def get_stranger_info(ws, user_id):
        self_echo = runtime._next_echo()
        json_data = {
            "action": "get_stranger_info",
            "params": {
                "user_id": user_id
            },
            "echo": self_echo
        }
        await ws.send(json.dumps(json_data))

        response = await runtime._wait_for_echo(self_echo)
        if response is None:
            return None
        print("[NapCat]Response:", response)
        if "data" in response:
            return response["data"]
        return None

    async def send_group_message(ws, group_id, message, auto_escape=False):
        print("[NapCat]Sending message:", message)
        self_echo = runtime._next_echo()
        json_data = {
            "action": "send_group_msg",
            "params": {
                "group_id": group_id,
                "message": message,
                "auto_escape": auto_escape
            },
            "echo": self_echo
        }

        async with aiohttp.ClientSession() as session:  # noqa: F841 (kept for session lifecycle)
            await ws.send(json.dumps(json_data))
            response = await runtime._wait_for_echo(self_echo)
            if response is None:
                return None
            print("[NapCat]Response:", response)
            if "status" in response:
                if response["status"] == "ok":
                    print("[NapCat]Message sent successfully")
                else:
                    print("[NapCat]Failed to send message")
            if "data" in response and response["data"] is not None and "message_id" in response["data"]:
                return response["data"]["message_id"]
            return None

    async def send_private_message(ws, user_id, message, auto_escape=False):
        print("[NapCat]Sending message:", message)
        self_echo = runtime._next_echo()
        json_data = {
            "action": "send_private_msg",
            "params": {
                "user_id": user_id,
                "message": message,
                "auto_escape": auto_escape
            },
            "echo": self_echo
        }
        async with aiohttp.ClientSession() as session:  # noqa: F841
            await ws.send(json.dumps(json_data))
            response = await runtime._wait_for_echo(self_echo)
            if response is None:
                return None
            print("[NapCat]Response:", response)
            if "status" in response:
                if response["status"] == "ok":
                    print("[NapCat]Message sent successfully")
                else:
                    print("[NapCat]Failed to send message")
            if "data" in response and response["data"] is not None and "message_id" in response["data"]:
                return response["data"]["message_id"]
            return None

    async def upload_group_file(ws, group_id, file, name, folder):
        self_echo = runtime._next_echo()
        json_data = {
            "action": "upload_group_file",
            "params": {
                "group_id": group_id,
                "file": file,
                "name": name,
                "folder": folder
            },
            "echo": self_echo
        }

        await ws.send(json.dumps(json_data))

        response = await runtime._wait_for_echo(self_echo)
        if response is None:
            return None
        print("[NapCat]Response:", response)
        if "status" in response:
            if response["status"] == "ok":
                print("[NapCat]File uploaded successfully")
            else:
                print("[NapCat]Failed to upload file")
        return None

    async def upload_private_file(ws, user_id, file, name):
        self_echo = runtime._next_echo()
        json_data = {
            "action": "upload_private_file",
            "params": {
                "user_id": user_id,
                "file": file,
                "name": name
            },
            "echo": self_echo
        }

        await ws.send(json.dumps(json_data))

        response = await runtime._wait_for_echo(self_echo)
        if response is None:
            return None
        print("[NapCat]Response:", response)
        if "status" in response:
            if response["status"] == "ok":
                print("[NapCat]File uploaded successfully")
            else:
                print("[NapCat]Failed to upload file")
        return None

    async def withdraw_group_message(ws, message_id):
        if message_id is None:
            return None
        self_echo = runtime._next_echo()
        json_data = {
            "action": "delete_msg",
            "params": {
                "message_id": message_id
            },
            "echo": self_echo
        }
        await ws.send(json.dumps(json_data))

        response = await runtime._wait_for_echo(self_echo)
        if response is None:
            return None
        print("[NapCat]Response:", response)
        if "status" in response:
            if response["status"] == "ok":
                print("[NapCat]Message withdrawn successfully")
            else:
                print("[NapCat]Failed to withdraw message")
        return None

    # ── CQ codec ─────────────────────────────────────────────────────────

    async def encode_message_to_CQ(message):
        encoded_message = ""
        for x in message:
            if x["type"] == "text":
                encoded_message += x["data"]["text"]
            else:
                encoded_message += f"[CQ:{x['type']},"
                for key, value in x["data"].items():
                    if key != "type":
                        encoded_message += f"{key}={value},"
                encoded_message = encoded_message[:-1] + "]"
        return encoded_message

    async def encode_message_to_CQ_without_At_self_and_Image(message):
        encoded_message = ""
        for x in message:
            if x["type"] == "text":
                encoded_message += x["data"]["text"]
            else:
                if x["type"] == "at" and x["data"]["qq"] == str(runtime.bot_qq):
                    continue
                if x["type"] == "image":
                    if "base64" in x["data"]:
                        img_bytes = x["data"]["base64"]
                        img_bytes = base64.b64decode(img_bytes)
                        tag = await gemini.image_to_text(img_bytes)
                        encoded_message += f" <Image:prompt=\"{tag}\"> "
                    elif "url" in x["data"]:
                        url = x["data"]["url"]
                        async with aiohttp.ClientSession() as session:
                            async with session.get(url) as response:
                                img_bytes = await response.read()
                        tag = await gemini.image_to_text(img_bytes)
                        encoded_message += f" <Image:prompt=\"{tag}\"> "
                    continue
                encoded_message += f"[CQ:{x['type']},"
                for key, value in x["data"].items():
                    if key != "type":
                        encoded_message += f"{key}={value},"
                encoded_message = encoded_message[:-1] + "]"
        return encoded_message

    async def decode_CQ_to_message(message):
        decoded_message = []
        i = 0
        while i < len(message):
            if message[i] == "[":
                j = i + 1
                while j < len(message) and message[j] != "]":
                    j += 1
                if j < len(message):
                    cq_content = message[i + 1:j]
                    if cq_content.startswith("CQ:"):
                        cq_parts = cq_content.split(",")
                        cq_type = cq_parts[0][3:]
                        cq_data = {}
                        for x in cq_parts[1:]:
                            if "=" in x:
                                key, value = x.split("=", 1)
                            else:
                                key = x
                                value = ""
                            cq_data[key] = value
                        decoded_message.append({"type": cq_type, "data": cq_data})
                    else:
                        # Not a CQ code, treat as plain text
                        decoded_message.append({"type": "text", "data": {"text": message[i:j + 1]}})
                    i = j + 1
                else:
                    decoded_message.append({"type": "text", "data": {"text": str(message[i:])}})
                    break
            else:
                j = i + 1
                while j < len(message) and message[j] != "[":
                    j += 1
                decoded_message.append({"type": "text", "data": {"text": str(message[i:j])}})
                i = j
        return decoded_message

    return {
        "get_message_by_id": get_message_by_id,
        "send_group_message": send_group_message,
        "send_private_message": send_private_message,
        "withdraw_group_message": withdraw_group_message,
        "get_stranger_info": get_stranger_info,
        "encode_message_to_CQ": encode_message_to_CQ,
        "encode_message_to_CQ_without_At_self_and_Image": encode_message_to_CQ_without_At_self_and_Image,
        "decode_CQ_to_message": decode_CQ_to_message,
        "test_if_super_user": test_if_super_user,
        "bot_qq": runtime.bot_qq,
        "proxy_url": runtime.proxy_url,
        "upload_group_file": upload_group_file,
        "upload_private_file": upload_private_file,
    }


# ── Hot-reload & handler management ────────────────────────────────────────────

handlers = None


async def hot_reload(runtime: BotRuntime):
    global handlers
    if handlers is not None:
        if hasattr(handlers, "handler_release"):
            await handlers.handler_release()
        handlers = importlib.reload(handlers)
    else:
        import handlers as _h
        handlers = _h

    # Refresh interfaces with current runtime state
    interfaces = _make_interfaces(runtime)
    if hasattr(handlers, "handler_init"):
        await handlers.handler_init(interfaces)
    return handlers


async def release_handlers():
    global handlers
    if handlers is None:
        return
    if hasattr(handlers, "handler_release"):
        await handlers.handler_release()
    handlers = None


# ── Server ─────────────────────────────────────────────────────────────────────

async def serve(runtime: BotRuntime):
    async def serve_forever(ws, path=None):
        loop = asyncio.get_event_loop()
        global handlers
        unexcepted_error_happened = False  # noqa: F841
        retry = 0
        print(f"[NapCat]NapCat connected from path: {path}")
        if handlers is None:
            await hot_reload(runtime)
        async for message in ws:
            if runtime.server_close_signal:
                break
            try:
                response = json.loads(message)
                if "self_id" in response and response["self_id"] != runtime.bot_qq:
                    runtime.bot_qq = response["self_id"]
                    print("[NapCat]Bot QQ:", runtime.bot_qq)
                    await hot_reload(runtime)

                if "status" in response and "echo" in response:
                    runtime.echo_dict[response["echo"]] = response
                    retry = 0
                    continue

                task = loop.create_task(handlers.execute_function(ws, response))
                task_info = {"task": task, "start_time": time.time(), "param": response, "ws": ws}
                runtime.running_tasks.append(task_info)

                def remove_task(task):
                    for idx in range(len(runtime.running_tasks)):
                        if runtime.running_tasks[idx]["task"] == task:
                            runtime.running_tasks.pop(idx)
                            break

                task.add_done_callback(remove_task)
                retry = 0
            except Exception as e:
                traceback.print_exc()
                print("[NapCat]Failed to process message:", str(e))
                await asyncio.sleep(5)
                retry += 1
                if retry > 5:
                    raise RuntimeError("[NapCat]ERROR, reconnecting")

    print(f"[NapCat]Starting reverse websocket server: {websocket_url}/onebot/v11/ws")
    await hot_reload(runtime)
    async with websockets.serve(serve_forever, Host, int(Port)):
        await asyncio.Future()


bot_workpath = os.path.join(os.path.dirname(__file__), "bot_workpath")


async def server():
    print("[NapCat]Starting server")
    runtime = BotRuntime()
    while not runtime.server_close_signal:
        try:
            await serve(runtime)
        except Exception as e:
            print("[NapCat]", traceback.format_exc())
            print("[NapCat]Error:", e)
            await asyncio.sleep(5)
            print("[NapCat]Reconnecting...")


if __name__ == "__main__":
    asyncio.run(server())