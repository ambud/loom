import asyncio
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, status, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
import uvicorn

from .config import load, get_model_cfg, make_client, load_system_prompt, load_review_system_prompt, save_config
from .llm import TOOLS, async_count_tokens
from .agent import run_turns, compact_messages, _total_message_tokens
from .tools import create_registry
from .utils import set_print_redirect, pt_print, print_panel, Markdown, RichText
from .session import (
    TokenTracker, SessionLogger, BackgroundManager, 
    run_bash, run_bash_bg, _log_dir,
    slash_help, slash_config, slash_session, run_review
)

# Auth Setup
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
security = HTTPBasic()
ALGORITHM = "HS256"

def create_access_token(data: dict, secret_key: str, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, secret_key, algorithm=ALGORITHM)

async def verify_token(token: str, secret_key: str):
    try:
        payload = jwt.decode(token, secret_key, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

app = FastAPI(title="Loom Web UI")

# Path to static files
STATIC_DIR = Path(__file__).parent / "web"
STATIC_DIR.mkdir(exist_ok=True)

class WebSession:

    def __init__(self, websocket: WebSocket, session_id: str | None = None):
        self.websocket = websocket
        self.task: Optional[asyncio.Task] = None
        self.input_queue = asyncio.Queue()
        self.cfg = load()
        self.client = make_client(self.cfg)
        self.system_prompt = load_system_prompt(self.cfg)
        self.registry = create_registry()
        
        self.logger = SessionLogger(self.cfg, session_id=session_id)
        existing_messages = self.logger.load_messages()
        
        if existing_messages:
            self.messages = existing_messages
        else:
            self.messages = [{"role": "system", "content": self.system_prompt}]
            self.logger.log("system", self.system_prompt)
        
        self.tracker = TokenTracker(
            session_id=self.logger._meta.get("id"),
            log_dir=_log_dir(self.cfg)
        )
        self.bg_mgr = BackgroundManager()
        self.client_cache = {"default": self.client}
        self.active_profile = "default"

    async def send_output(self, text: str):
        await self.websocket.send_json({"type": "output", "content": text})

    async def send_cwd(self):
        await self.websocket.send_json({"type": "cwd", "content": os.getcwd(), "session_id": self.logger._meta.get("id")})

    async def get_history(self):
        """Send entire message history to client."""
        history = []
        for msg in self.messages:
            if msg["role"] == "system": continue
            history.append(msg)
        await self.websocket.send_json({"type": "history", "content": history})

    async def get_input(self, prompt: str = "") -> str:
        await self.websocket.send_json({"type": "input_request", "prompt": prompt})
        return await self.input_queue.get()

    async def handle_input(self, text: str):
        text = text.strip()
        if not text:
            return

        # Interrupt current task if any
        if self.task and not self.task.done():
            self.task.cancel()
            await self.send_output(">>> Interrupted. Redirecting Loom with new instruction...\r\n")
            self.logger.log("user_interruption", text)

        set_print_redirect(lambda t: asyncio.create_task(self.send_output(t)))

        try:
            # Shell execution
            if text.startswith("!"):
                if not self.cfg.get("allow_shell_commands", True):
                    await self.send_output("\x1b[1;31mError: Shell commands are disabled in the current configuration.\x1b[0m\r\n")
                    return

                if text.startswith("!!"):
                    self.bg_mgr.add("shell", run_bash_bg(text[2:]))
                    await self.send_output(f"Background shell started: {text[2:]}\r\n")
                    return
                
                await run_bash(text[1:])
                return

            # Slash commands
            if text.startswith("/"):
                parts = text[1:].split(None, 1)
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""

                if cmd == "help":
                    slash_help()
                    return
                if cmd == "config":
                    slash_config(self.cfg)
                    return
                if cmd == "session":
                    slash_session(self.logger)
                    return
                if cmd == "system":
                    await self.send_output(self.system_prompt + "\r\n")
                    return
                if cmd == "review":
                    await run_review(
                        self.client_cache, self.messages, self.cfg, self.registry, self.tracker, input_fn=self.get_input
                    )
                    return
                if cmd == "remember":
                    if not arg:
                        await self.send_output('Usage: /remember "text to remember"\r\n')
                        return
                    from .tools.memory import MemoryTool
                    mem = MemoryTool()
                    result = await mem.run(action="store", text=arg)
                    await self.send_output(f"Memory: {result}\r\n")
                    # Also notify agent of the new fact in the current context
                    self.messages.append({"role": "user", "content": f"[SYSTEM] Memory stored: {arg}"})
                    return
                if cmd == "search":
                    if not arg:
                        await self.send_output("Usage: /search keyword\r\n")
                        return
                    from .tools.memory import MemoryTool
                    mem = MemoryTool()
                    result = await mem.run(action="search", keyword=arg)
                    await self.send_output(result + "\r\n")
                    return
                if cmd == "memory":
                    from .tools.memory import MemoryTool
                    mem = MemoryTool()
                    if arg:
                        result = await mem.run(action="read", topic=arg)
                    else:
                        result = await mem.run(action="list")
                    await self.send_output(result + "\r\n")
                    return
                if cmd == "stats":
                    await self.send_output(f"Session: {self.tracker.session_input:,} input | {self.tracker.session_output:,} output | {self.tracker.session_total:,} total\r\n")
                    await self.send_output(f"Global:  {self.tracker.total_input:,} input | {self.tracker.total_output:,} output | {self.tracker.total_tokens:,} total\r\n")
                    return
                if cmd == "plan":
                    self.cfg["plan"] = not self.cfg.get("plan", False)
                    status_str = "ENABLED" if self.cfg["plan"] else "DISABLED"
                    await self.send_output(f"Plan mode {status_str}.\r\n")
                    if self.cfg["plan"]:
                        await self.send_output("Agent will now use the `plan` tool to propose steps before execution.\r\n")
                        self.messages.append({
                            "role": "user", 
                            "content": "[SYSTEM] Plan mode enabled. Please use the `plan` tool to propose your approach for the current or next task."
                        })
                    return
                if cmd == "model":
                    models = self.cfg.get("models", {})
                    if not arg:
                        await self.send_output(f"Active: {self.active_profile} ({self.cfg.get('model', '?')})\r\n")
                        if models:
                            await self.send_output("Profiles:\r\n")
                            for name in sorted(models):
                                profile = models[name]
                                marker = " *" if name == self.active_profile else ""
                                await self.send_output(f"  {name}: {profile.get('model', self.cfg['model'])} ({profile.get('base_url', self.cfg['base_url'])}){marker}\r\n")
                        return
                    if arg in models:
                        self.active_profile = arg
                        profile = models[arg]
                        if "model" in profile:
                            self.cfg["model"] = profile["model"]
                        if arg not in self.client_cache:
                            profile_cfg = get_model_cfg(self.cfg, arg)
                            self.client_cache[arg] = make_client(profile_cfg)
                        self.client = self.client_cache[arg]
                        await self.send_output(f"Switched to profile: {arg} (model={self.cfg['model']})\r\n")
                        return
                    await self.send_output(f"Unknown profile: {arg}\r\n")
                    return
                if cmd == "background" or cmd == "bg":
                    if arg:
                        # Start background agent task
                        asyncio.create_task(self.run_background(arg))
                    else:
                        await self.send_output(self.bg_mgr.status_panel() + "\r\n")
                    return
                if cmd == "compact":
                    await compact_messages(self.client, self.messages, self.cfg, force=True)
                    self.tracker.session_input = await _total_message_tokens(self.messages, self.cfg)
                    await self.send_output("History compacted.\r\n")
                    return
                
                await self.send_output(f"Unknown command: {cmd}\r\n")
                return

            # Normal prompt
            self.task = asyncio.create_task(self.run(text))
        except Exception as e:
            await self.send_output(f"Command error: {e}\r\n")
    async def run(self, user_prompt: str):
        self.messages.append({"role": "user", "content": user_prompt})
        self.logger.log("user", user_prompt)
        
        set_print_redirect(lambda t: asyncio.create_task(self.send_output(t)))
        
        try:
            await run_turns(
                self.client, 
                self.messages, 
                TOOLS, 
                self.registry, 
                self.cfg, 
                input_fn=self.get_input,
                tracker=self.tracker,
                logger=self.logger
            )
        except asyncio.CancelledError:
            await self.send_output("\r\n\x1b[1;31mTask cancelled.\x1b[0m\r\n")
        except Exception as e:
            await self.send_output(f"\r\n\x1b[1;31mError: {e}\x1b[0m\r\n")
        finally:
            self.tracker.flush()
            await self.websocket.send_json({"type": "done"})

    async def run_background(self, user_prompt: str):
        bg_messages = list(self.messages)
        bg_messages.append({"role": "user", "content": user_prompt})
        self.logger.log("user_bg", user_prompt)
        
        set_print_redirect(lambda t: asyncio.create_task(self.send_output(t)))
        
        # Note: output for background tasks currently goes to the same websocket
        # but we don't block the main loop
        await self.send_output(f"Background task started: {user_prompt[:60]}...\r\n")
        
        try:
            await run_turns(
                self.client,
                bg_messages,
                TOOLS,
                self.registry,
                self.cfg,
                tracker=self.tracker,
                logger=self.logger
            )
            await self.send_output(f"\r\nBackground task completed: {user_prompt[:30]}...\r\n")
        except Exception as e:
            await self.send_output(f"\r\nBackground task failed: {e}\r\n")

@app.post("/login")
async def login(credentials: HTTPBasicCredentials = Depends(security)):
    cfg = load()
    expected_user = cfg.get("web_user", "admin")
    expected_password = cfg.get("web_password", "")
    
    # If password is set in config, we verify it. 
    # If it's empty, we allow any login (local lab use case) or we can force it.
    if expected_password:
        # Check if the config password is a hash or plain
        is_match = False
        try:
            is_match = pwd_context.verify(credentials.password, expected_password)
        except Exception:
            # Fallback to plain text if not a valid hash (convenience for simple config)
            is_match = credentials.password == expected_password
        
        if credentials.username != expected_user or not is_match:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Basic"},
            )
    
    access_token = create_access_token(
        data={"sub": credentials.username},
        secret_key=cfg.get("secret_key", "loom-default-secret-change-me"),
        expires_delta=timedelta(days=7)
    )
    return {
        "access_token": access_token, 
        "token_type": "bearer",
        "must_change_password": cfg.get("web_first_login", True) and expected_password != ""
    }

@app.post("/change-password")
async def change_password(data: dict):
    token = data.get("token")
    new_password = data.get("password")
    
    cfg = load()
    if not await verify_token(token, cfg.get("secret_key", "loom-default-secret-change-me")):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    if not new_password:
        raise HTTPException(status_code=400, detail="Password cannot be empty")
    
    # Hash and save
    hashed = pwd_context.hash(new_password)
    cfg["web_password"] = hashed
    cfg["web_first_login"] = False
    save_config(cfg)
    
    return {"status": "success"}

@app.get("/files")
async def list_files(path: str = Query(...), token: str = Query(...)):
    cfg = load()
    if not await verify_token(token, cfg.get("secret_key", "loom-default-secret-change-me")):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        current_cwd = os.getcwd()
    except Exception:
        current_cwd = "."
    root = Path(cfg.get("workspace_root", current_cwd)).expanduser().resolve()
    p = Path(path).expanduser().resolve()
    
    # Path Traversal Protection: Ensure path is within workspace_root
    try:
        p.relative_to(root)
    except ValueError:
        # If not under root, force it back to root
        p = root
    
    if not p.exists():
        return JSONResponse(content={"error": "Path does not exist"}, status_code=404)
    
    try:
        items = []
        for entry in sorted(os.scandir(p), key=lambda e: (not e.is_dir(), e.name.lower())):
            # Hide sensitive files from the browser for a cleaner/safer UI
            if entry.name in (".env", ".git", ".loom"):
                continue
            items.append({
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "path": str(Path(entry.path).resolve())
            })
        return {"items": items, "current": str(p), "parent": str(p.parent) if p.parent != p else None}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/")
async def get():
    return HTMLResponse((STATIC_DIR / "index.html").read_text())

@app.get("/sessions")
async def list_sessions(token: str = Query(...)):
    cfg = load()
    if not await verify_token(token, cfg.get("secret_key", "loom-default-secret-change-me")):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    d = _log_dir(cfg)
    sessions = []
    for f in sorted(d.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
        sid = f.stem
        try:
            with open(f, "r") as log:
                first_line = log.readline()
                if not first_line: continue
                first = json.loads(first_line)
                meta = first.get("content", {}) if first.get("role") == "meta" else {}
                created = meta.get("created", datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat())
                cwd = meta.get("cwd", "?")
                
                # Simple preview from first user prompt
                preview = sid
                for line in log:
                    entry = json.loads(line)
                    if entry.get("role") == "user":
                        preview = entry.get("content", "")[:50]
                        break
                
                sessions.append({
                    "id": sid,
                    "created": created,
                    "cwd": cwd,
                    "preview": preview
                })
        except Exception:
            continue
            
    return {"sessions": sessions}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = Query(None), session_id: Optional[str] = Query(None)):
    await websocket.accept()
    cfg = load()
    
    # Verify JWT for WebSocket if password is set
    if cfg.get("web_password") and not await verify_token(token, cfg.get("secret_key", "loom-default-secret-change-me")):
        await websocket.send_json({"type": "output", "content": "\x1b[1;31mUnauthorized connection.\x1b[0m\r\n"})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    session = WebSession(websocket, session_id=session_id)
    await session.send_cwd()
    if session_id:
        await session.get_history()
    
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            
            if msg_type == "prompt":
                content = data.get("content")
                await session.handle_input(content)
            
            elif msg_type == "input_response":
                await session.input_queue.put(data.get("content"))
            
            elif msg_type == "stop":
                if session.task and not session.task.done():
                    session.task.cancel()
                    await session.send_output("\x1b[1;33mStopping...\x1b[0m\n")
            
            elif msg_type == "set_cwd":
                new_path = data.get("content")
                try:
                    try:
                        current_cwd = os.getcwd()
                    except Exception:
                        current_cwd = "."
                    root = Path(session.cfg.get("workspace_root", current_cwd)).expanduser().resolve()
                    p = Path(new_path).expanduser().resolve()
                    # Verify target is within root
                    p.relative_to(root)
                    
                    os.chdir(p)
                    await session.send_cwd()
                    await session.send_output(f"\x1b[1;32mDirectory changed to: {os.getcwd()}\x1b[0m\r\n")
                    session.registry = create_registry()
                except ValueError:
                    await session.send_output(f"\x1b[1;31mError: Path must be within workspace root: {root}\x1b[0m\r\n")
                except Exception as e:
                    await session.send_output(f"\x1b[1;31mError changing directory: {e}\x1b[0m\r\n")
            
    except WebSocketDisconnect:
        if session.task:
            session.task.cancel()

def start_web_ui(host="127.0.0.1", port=8000):
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    start_web_ui()
