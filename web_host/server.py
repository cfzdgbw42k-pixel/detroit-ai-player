from __future__ import annotations

import json
import mimetypes
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine import CampaignEngine


HOST = "127.0.0.1"
PORT = 8765
PUBLIC = Path(__file__).resolve().parent / "public"
SESSION_LOCKS: dict[str, threading.Lock] = {}
LOCKS_GUARD = threading.Lock()
INSTANCE_LOCK_HANDLE = None
INSTANCE_LOCK_PATH = Path(__file__).resolve().parent / ".server.lock"
INSTANCE_PORT_PATH = Path(__file__).resolve().parent / ".server.port"


def _session_lock(session_id: str) -> threading.Lock:
    with LOCKS_GUARD:
        return SESSION_LOCKS.setdefault(session_id, threading.Lock())


def _acquire_instance_lock():
    handle = INSTANCE_LOCK_PATH.open("a+b")
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except (OSError, IOError):
        handle.close()
        return None


def _existing_instance_url() -> str | None:
    for _ in range(10):
        try:
            value = INSTANCE_PORT_PATH.read_text(encoding="ascii").strip()
            port = int(value)
            if PORT <= port < PORT + 20:
                return f"http://{HOST}:{port}"
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "DetroitHost/1.0"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, payload: object, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        self.connection.settimeout(10)
        if self.headers.get_content_type() != "application/json":
            raise ValueError("只接受本機頁面送出的 JSON 請求")
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > 10_000_000:
            raise ValueError("資料太大")
        raw = self.rfile.read(size)
        value = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(value, dict):
            raise ValueError("請求格式不正確")
        return value

    def _validate_local_request(self) -> None:
        port = self.server.server_address[1]
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "") not in allowed_hosts:
            raise PermissionError("拒絕非本機來源")
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
            raise PermissionError("拒絕其他網站操作本機存檔")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            self._validate_local_request()
            if parsed.path == "/api/sessions":
                return self._json({
                    "sessions": CampaignEngine.list_sessions(),
                    "ending_progress": CampaignEngine.ending_progress(),
                })
            if parsed.path == "/api/session":
                session_id = parse_qs(parsed.query).get("id", [""])[0]
                return self._json(CampaignEngine.load(session_id).view())
            if parsed.path == "/api/history":
                session_id = parse_qs(parsed.query).get("id", [""])[0]
                return self._json(CampaignEngine.load(session_id).history_view())
            if parsed.path == "/api/export":
                session_id = parse_qs(parsed.query).get("id", [""])[0]
                data = json.dumps(CampaignEngine.load(session_id).export_safe(), ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{session_id}-handoff.json"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            if parsed.path == "/api/backup":
                session_id = parse_qs(parsed.query).get("id", [""])[0]
                data = json.dumps(CampaignEngine.load(session_id).export_backup(), ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{session_id}-full-save.json"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            return self._static(parsed.path)
        except PermissionError as error:
            return self._json({"error": str(error)}, 403)
        except FileNotFoundError as error:
            return self._json({"error": str(error)}, 404)
        except Exception as error:
            return self._json({"error": str(error)}, 400)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            self._validate_local_request()
            body = self._body()
            if parsed.path == "/api/sessions":
                engine = CampaignEngine.create(body.get("name", "玩家・容容線"), body.get("difficulty", "casual"))
                return self._json(engine.view(), 201)
            if parsed.path == "/api/import":
                engine = CampaignEngine.import_backup(body.get("backup"))
                return self._json(engine.view(), 201)
            if parsed.path == "/api/action":
                session_id = body.get("id", "")
                if not isinstance(session_id, str):
                    raise ValueError("存檔識別格式不正確")
                with _session_lock(session_id):
                    engine = CampaignEngine.load(session_id)
                    engine.assert_precondition(body.get("revision"), body.get("node_id") if body.get("action") in {"choose", "continue"} else None)
                    action = body.get("action")
                    if action == "choose":
                        return self._json(engine.act(body.get("choice_id"), body.get("reason", "")))
                    if action == "continue":
                        return self._json(engine.act())
                    if action == "next_chapter":
                        return self._json(engine.next_chapter())
                    if action == "save_reflection":
                        return self._json(engine.save_reflection(body.get("reflection", "")))
                    raise ValueError("未知操作")
            if parsed.path == "/api/delete":
                session_id = body.get("id", "")
                expected_name = body.get("name", "")
                if not isinstance(session_id, str) or not isinstance(expected_name, str):
                    raise ValueError("存檔識別格式不正確")
                if body.get("confirmation") != "刪除":
                    raise ValueError("未完成刪除確認")
                with _session_lock(session_id):
                    deleted_name = CampaignEngine.delete_session(session_id, expected_name)
                return self._json({"deleted": True, "name": deleted_name})
            return self._json({"error": "找不到功能"}, 404)
        except RuntimeError as error:
            if str(error) in {"STALE_SAVE", "STALE_SCENE"}:
                return self._json({"error": "存檔已在另一個視窗更新，請重新載入"}, 409)
            return self._json({"error": str(error)}, 400)
        except PermissionError as error:
            return self._json({"error": str(error)}, 403)
        except FileNotFoundError as error:
            return self._json({"error": str(error)}, 404)
        except Exception as error:
            return self._json({"error": str(error)}, 400)

    def _static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (PUBLIC / relative).resolve()
        if PUBLIC.resolve() not in target.parents and target != PUBLIC.resolve():
            return self._json({"error": "不允許的路徑"}, 403)
        if not target.is_file():
            target = PUBLIC / "index.html"
        data = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    global INSTANCE_LOCK_HANDLE
    INSTANCE_LOCK_HANDLE = _acquire_instance_lock()
    if INSTANCE_LOCK_HANDLE is None:
        existing_url = _existing_instance_url()
        if existing_url:
            webbrowser.open(existing_url)
            print("主持台已經開啟，已切回原本的頁面。")
            return
        raise SystemExit("已有另一個主持台正在啟動，請稍後再雙擊一次。")
    server = None
    for port in range(PORT, PORT + 20):
        try:
            server = ThreadingHTTPServer((HOST, port), Handler)
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit("無法啟動主持台：本機連接埠都被占用。")
    server.daemon_threads = True
    server.timeout = 30
    INSTANCE_PORT_PATH.write_text(str(server.server_address[1]), encoding="ascii")
    url = f"http://{HOST}:{server.server_address[1]}"
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print("底特律主持台已啟動。請保留這個小視窗，遊戲結束後可直接關閉。")
    print(url)
    server.serve_forever()


if __name__ == "__main__":
    main()
