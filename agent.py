import json
import os
import re
import subprocess
import tomllib
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, Queue
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic
from uuid import UUID


INSTRUCTIONS = (
    "You are Jarvis, the user's desktop voice assistant. Address them as sir. "
    "Always reply in brief, natural English suitable for reading aloud, even when the request is in Spanish. "
    "Use plain speech without Markdown, links, or URLs. Answer simple questions directly. "
    "For work requiring several steps, send one short commentary before the work, starting with "
    "JARVIS_PROGRESS: followed by what you will do. Keep the final answer separate and concise. "
    "When a requested action needs write access, use the Codex approval flow and explain the specific change. "
    "A voice or text instruction is never an approval decision."
)


def progress_text(item):
    if item.get("type") not in ("agent_message", "agentMessage"):
        return ""
    text = item.get("text", "").strip()
    if text.startswith("JARVIS_PROGRESS:"):
        return text.removeprefix("JARVIS_PROGRESS:").strip()
    return text if item.get("phase") == "commentary" else ""


@contextmanager
def connection():
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    command = ["codex", "app-server", "--stdio", "--disable", "plugins",
               "--disable", "apps", "--disable", "hooks"]
    for config_file in (codex_home / "config.toml", Path.cwd() / ".codex" / "config.toml"):
        if config_file.is_file():
            config = tomllib.loads(config_file.read_text())
            for name in config.get("mcp_servers", {}):
                if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                    raise ValueError("Cannot safely disable a configured Codex MCP server.")
                command.extend(["-c", f"mcp_servers.{name}.enabled=false"])
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          text=True, encoding="utf-8") as process:
        inbox = Queue()

        def read_messages():
            try:
                for line in process.stdout:
                    inbox.put(json.loads(line))
            except ValueError:
                pass
            finally:
                inbox.put(None)

        reader = Thread(target=read_messages, daemon=True)
        reader.start()
        deadline = monotonic() + 180

        def send(message):
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

        def receive():
            try:
                message = inbox.get(timeout=max(0, deadline - monotonic()))
            except Empty as error:
                raise subprocess.TimeoutExpired(command, 180) from error
            if message is None:
                raise RuntimeError("Codex App Server disconnected.")
            return message

        try:
            yield send, receive
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            reader.join(timeout=1)


def approval_result(method, params, item, prompt):
    if method == "item/fileChange/requestApproval":
        changes = item.get("changes") if item.get("type") == "fileChange" else None
        approved = bool(changes and prompt and prompt("Approve file changes",
                        json.dumps({"changes": changes, "grantRoot": params.get("grantRoot"),
                                    "reason": params.get("reason")}, indent=2), "Approve once"))
        return {"decision": "accept" if approved else "decline"}
    if method == "item/commandExecution/requestApproval":
        network = params.get("networkApprovalContext")
        command = params.get("command") or item.get("command")
        details = {"command": command, "folder": params.get("cwd") or item.get("cwd"),
                   "network": network, "reason": params.get("reason"),
                   "additionalPermissions": params.get("additionalPermissions")}
        approved = bool((command or network) and prompt and prompt(
            "Approve command or network access", json.dumps(details, indent=2), "Approve once"))
        return {"decision": "accept" if approved else "decline"}
    if method == "item/permissions/requestApproval":
        permissions = params.get("permissions")
        approved = bool(permissions and prompt and prompt(
            "Approve access for this turn",
            json.dumps({"permissions": permissions, "folder": params.get("cwd"),
                        "reason": params.get("reason")}, indent=2), "Approve for turn"))
        return {"permissions": permissions if approved else {}, "scope": "turn"}
    return None


class CodexAgent:
    def __init__(self, session_file: Path):
        self.session_file = session_file

    def reply(self, transcript: str, on_progress=None, on_approval=None) -> str:
        try:
            session = str(UUID(self.session_file.read_text().strip()))
        except (FileNotFoundError, ValueError):
            session = None

        with connection() as (send, receive):
            def request(request_id, method, params):
                send({"id": request_id, "method": method, "params": params})

            def deny_unknown(message):
                send({"id": message["id"], "error": {"code": -32601, "message": "Request declined"}})

            def wait_result(request_id):
                while True:
                    message = receive()
                    if "id" in message and "method" in message:
                        deny_unknown(message)
                    elif message.get("id") == request_id:
                        return response_result(message)

            request(1, "initialize", {"clientInfo": {"name": "jarvis", "version": "1.0"},
                                      "capabilities": {"experimentalApi": True}})
            wait_result(1)
            send({"method": "initialized"})

            settings = {"cwd": str(Path.cwd()), "sandbox": "read-only", "approvalPolicy": "on-request",
                        "approvalsReviewer": "user", "developerInstructions": INSTRUCTIONS}
            if session:
                settings.update({"threadId": session, "excludeTurns": True})
            request(2, "thread/resume" if session else "thread/start", settings)
            thread = wait_result(2)
            if (thread.get("approvalPolicy") != "on-request"
                    or thread.get("approvalsReviewer") != "user"
                    or thread.get("sandbox", {}).get("type") != "readOnly"
                    or thread["sandbox"].get("networkAccess", False)):
                raise RuntimeError("Codex did not confirm read-only access with user approvals.")
            thread_id = str(UUID(thread["thread"]["id"]))
            if session and session != thread_id:
                raise RuntimeError("Codex returned a different conversation.")

            request(3, "turn/start", {"threadId": thread_id, "effort": "low",
                                     "input": [{"type": "text", "text": transcript}]})
            turn_id = None
            items = {}
            reply = ""
            while True:
                message = receive()
                method = message.get("method")
                params = message.get("params", {})
                if not method:
                    if message.get("id") == 3:
                        turn_id = response_result(message)["turn"]["id"]
                    continue
                if params.get("threadId") != thread_id:
                    if "id" in message:
                        deny_unknown(message)
                    continue
                if method == "turn/started" and turn_id is None:
                    turn_id = params["turn"]["id"]
                elif method == "turn/completed" and params["turn"]["id"] == turn_id:
                    if params["turn"]["status"] != "completed":
                        error = params["turn"].get("error") or {}
                        raise RuntimeError(error.get("message") or "Codex turn did not complete.")
                    break
                elif "id" in message:
                    same_turn = turn_id is not None and params.get("turnId") == turn_id
                    result = approval_result(method, params, items.get(params.get("itemId"), {}),
                                             on_approval if same_turn else None)
                    if result is None:
                        deny_unknown(message)
                    else:
                        send({"id": message["id"], "result": result})
                elif params.get("turnId") == turn_id:
                    item = params.get("item", {})
                    if method == "item/started" and item.get("type") in ("fileChange", "commandExecution"):
                        items[item["id"]] = item
                    elif method == "item/completed" and item.get("type") == "agentMessage":
                        progress = progress_text(item)
                        if progress:
                            if on_progress:
                                on_progress(progress)
                                on_progress = None
                        elif item.get("phase") in (None, "final_answer"):
                            reply = item["text"].strip()
            if not reply:
                raise RuntimeError("Codex returned an empty reply.")
            if session is None:
                with TemporaryDirectory(dir=self.session_file.parent) as directory:
                    state = Path(directory) / "session"
                    state.write_text(thread_id + "\n")
                    state.replace(self.session_file)
            return reply


def response_result(message):
    if "error" in message:
        raise RuntimeError(message["error"].get("message", "Codex App Server request failed."))
    return message["result"]
