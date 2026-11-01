#!/usr/bin/env python3
"""火星文 dict app: 分析 JSON、編碼、寫入字典。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from extract import DICT_DEFAULT, extract_missing, load_dict_words

HOST = "127.0.0.1"
PORT = 8765
NAME_RE = re.compile(r"[^\w\u3400-\u9fff\-]+", re.UNICODE)
WINDOW = None


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    if frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    if frozen():
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local"))
        path = base / "HuoxingwenDict"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path(__file__).resolve().parent


STATIC = resource_dir() / "static"
SETTINGS_PATH = data_dir() / "settings.json"
SKIPPED_PATH = data_dir() / "skipped.json"


def default_settings() -> dict:
    return {
        "dict_path": str(DICT_DEFAULT),
        "work_dir": str(data_dir() / "files"),
        "auto_implement": True,
        "auto_redeploy": True,
    }


def load_settings() -> dict:
    data = default_settings()
    if SETTINGS_PATH.is_file():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                data.update(saved)
        except json.JSONDecodeError:
            pass
    data["auto_implement"] = bool(data.get("auto_implement", True))
    data["auto_redeploy"] = bool(data.get("auto_redeploy", True))
    return data


def save_settings(data: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def current_dict() -> Path:
    return Path(str(load_settings().get("dict_path") or DICT_DEFAULT)).expanduser()


def _dict_key(dpath: Path) -> str:
    try:
        return str(dpath.expanduser().resolve())
    except OSError:
        return str(dpath)


def load_skipped(dpath: Path | None = None) -> set[str]:
    dpath = dpath or current_dict()
    if not SKIPPED_PATH.is_file():
        return set()
    try:
        data = json.loads(SKIPPED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    raw = None
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        raw = data.get(_dict_key(dpath))
        if raw is None:
            for key, val in data.items():
                try:
                    if Path(str(key)).expanduser().resolve() == dpath.expanduser().resolve():
                        raw = val
                        break
                except OSError:
                    continue
    if not isinstance(raw, list):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


def save_skipped(words: set[str], dpath: Path | None = None) -> None:
    dpath = dpath or current_dict()
    data: dict = {}
    if SKIPPED_PATH.is_file():
        try:
            loaded = json.loads(SKIPPED_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
            elif isinstance(loaded, list):
                data = {_dict_key(dpath): loaded}
        except (OSError, json.JSONDecodeError):
            data = {}
    data[_dict_key(dpath)] = sorted(words)
    SKIPPED_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def remember_implement_skips(meta: dict, dpath: Path) -> None:
    skipped = load_skipped(dpath)
    codes = meta.get("codes") if isinstance(meta.get("codes"), dict) else {}
    for word in meta.get("extracted_text") or []:
        word = str(word)
        if not word:
            continue
        if str(codes.get(word, "")).strip():
            skipped.discard(word)
        else:
            skipped.add(word)
    skipped -= load_dict_words(dpath)
    save_skipped(skipped, dpath)


def work_dir() -> Path:
    path = Path(str(load_settings().get("work_dir") or (data_dir() / "files"))).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def auto_implement() -> bool:
    return bool(load_settings().get("auto_implement", True))


def auto_redeploy() -> bool:
    return bool(load_settings().get("auto_redeploy", True))


def find_weasel_deployer() -> Path | None:
    raw = str(load_settings().get("weasel_deployer") or "").strip().strip('"')
    if raw:
        p = Path(raw).expanduser()
        if p.is_file():
            return p
    try:
        import winreg

        for hive, key in (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Rime\Weasel"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Rime\Weasel"),
        ):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    root, _ = winreg.QueryValueEx(handle, "WeaselRoot")
            except OSError:
                continue
            cand = Path(str(root)) / "WeaselDeployer.exe"
            if cand.is_file():
                return cand
    except Exception:
        pass
    for base in (Path(r"C:\Program Files (x86)\Rime"), Path(r"C:\Program Files\Rime")):
        if not base.is_dir():
            continue
        found = sorted(base.glob("weasel-*/WeaselDeployer.exe"))
        if found:
            return found[-1]
    return None


def weasel_deploy() -> tuple[bool, str]:
    exe = find_weasel_deployer()
    if not exe:
        return False, "找不到 WeaselDeployer.exe"
    kwargs: dict = {
        "args": [str(exe), "/deploy"],
        "timeout": 120,
        "capture_output": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(**kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"重新部署失敗：{exc}"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or b"").decode("utf-8", "ignore").strip()
        return False, err or f"重新部署失敗（{proc.returncode}）"
    return True, "已重新部署"


def maybe_redeploy(added: int) -> dict:
    if added <= 0 or not auto_redeploy():
        return {"redeployed": False}
    ok, msg = weasel_deploy()
    if ok:
        return {"redeployed": True}
    return {"redeployed": False, "redeploy_error": msg}


def set_dict_path(raw: str) -> tuple[Path | None, str | None]:
    text = (raw or "").strip().strip('"')
    if not text:
        return None, "請指定字典檔路徑"
    path = Path(text).expanduser()
    if not path.is_file():
        return None, f"找不到檔案：{path}"
    settings = load_settings()
    settings["dict_path"] = str(path)
    save_settings(settings)
    return path, None


def set_work_dir(raw: str) -> tuple[Path | None, str | None]:
    text = (raw or "").strip().strip('"')
    if not text:
        return None, "請指定工作資料夾"
    path = Path(text).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        return None, f"不是資料夾：{path}"
    settings = load_settings()
    settings["work_dir"] = str(path)
    save_settings(settings)
    return path, None


def info_payload() -> dict:
    p = current_dict()
    exists = p.is_file()
    known = 0
    pairs = 0
    if exists:
        try:
            known = len(load_dict_words(p))
            pairs = len(load_dict_entries(p))
        except OSError:
            exists = False
    wd = work_dir()
    return {
        "dict_path": str(p),
        "dict_exists": exists,
        "known": known,
        "pairs": pairs,
        "default_path": str(DICT_DEFAULT),
        "work_dir": str(wd),
        "default_work_dir": str(data_dir() / "files"),
        "auto_implement": auto_implement(),
        "auto_redeploy": auto_redeploy(),
        "weasel_deployer": str(find_weasel_deployer() or ""),
    }


def pick_dialog(kind: str) -> str:
    window = WINDOW
    if window is not None:
        import webview

        if kind == "dir":
            initial = work_dir()
            chosen = window.create_file_dialog(
                webview.FileDialog.FOLDER,
                directory=str(initial) if initial.is_dir() else str(Path.home()),
            )
        else:
            initial = current_dict().parent
            if not initial.is_dir():
                initial = DICT_DEFAULT.parent
            chosen = window.create_file_dialog(
                webview.FileDialog.OPEN,
                directory=str(initial) if initial.is_dir() else str(Path.home()),
                file_types=("Rime 字典 (*.yaml;*.yml)", "文字 (*.txt)", "所有檔案 (*.*)"),
            )
        if not chosen:
            return ""
        return str(chosen[0]) if isinstance(chosen, (list, tuple)) else str(chosen)

    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    if kind == "dir":
        initial = work_dir()
        picked = filedialog.askdirectory(
            parent=root,
            title="工作資料夾",
            initialdir=str(initial) if initial.is_dir() else str(Path.home()),
        )
    else:
        initial = current_dict().parent
        if not initial.is_dir():
            initial = DICT_DEFAULT.parent
        picked = filedialog.askopenfilename(
            parent=root,
            title="字典檔",
            initialdir=str(initial) if initial.is_dir() else str(Path.home()),
            filetypes=[("Rime 字典", "*.yaml"), ("YAML", "*.yml"), ("文字", "*.txt"), ("所有檔案", "*.*")],
        )
    root.destroy()
    return picked or ""


def load_dict_entries(path: Path) -> list[dict]:
    entries: list[dict] = []
    in_body = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "...":
            in_body = True
            continue
        if not in_body or not line.strip() or line.startswith("#") or "\t" not in line:
            continue
        word, code = line.split("\t", 1)
        word, code = word.strip(), code.strip()
        if word and code:
            entries.append({"word": word, "code": code})
    return entries


def dict_pairs(path: Path) -> set[tuple[str, str]]:
    return {(e["word"], e["code"]) for e in load_dict_entries(path)}


def append_dict_entries(entries: list[tuple[str, str]], path: Path) -> list[str]:
    existing = dict_pairs(path)
    added: list[str] = []
    lines = []
    for word, code in entries:
        word = word.strip()
        code = code.strip()
        if not word or not code:
            continue
        if (word, code) in existing:
            continue
        lines.append(f"{word}\t{code}\n")
        existing.add((word, code))
        added.append(f"{word}\t{code}")
    if not lines:
        return added
    data = path.read_text(encoding="utf-8")
    if data and not data.endswith("\n"):
        data += "\n"
    path.write_text(data + "".join(lines), encoding="utf-8")
    return added


def normalize_doc(doc: dict) -> dict:
    raw = doc.get("raw_text") if isinstance(doc.get("raw_text"), str) else ""
    meta_in = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    extracted_text = meta_in.get("extracted_text") if isinstance(meta_in.get("extracted_text"), list) else []
    extracted_text = [str(x) for x in extracted_text if str(x)]
    codes_in = meta_in.get("codes") if isinstance(meta_in.get("codes"), dict) else {}
    codes = {}
    if isinstance(codes_in, dict):
        for key, val in codes_in.items():
            word = str(key).strip()
            if not word:
                continue
            codes[word] = str(val).strip()
    extracted = bool(meta_in.get("extracted"))
    implemented = bool(meta_in.get("implemented")) and coding_state(
        {"extracted": extracted, "extracted_text": extracted_text, "codes": codes, "implemented": False}
    ) == "implement"
    meta = {
        **meta_in,
        "source": str(meta_in.get("source") or ""),
        "date": str(meta_in.get("date") or date.today().isoformat()),
        "extracted": extracted,
        "extracted_text": extracted_text,
        "codes": codes,
        "implemented": implemented,
    }
    meta.pop("skipped", None)
    meta.pop("processed", None)
    return {"raw_text": raw, "metadata": meta}


def coding_state(meta: dict) -> str:
    """process | implement | invalid | skip — no separate processed flag."""
    if not meta.get("extracted") or meta.get("implemented"):
        return "skip"
    texts = meta.get("extracted_text") or []
    codes = meta.get("codes") if isinstance(meta.get("codes"), dict) else {}
    n_text = len(texts)
    n_keys = len(codes)
    if n_keys > n_text:
        return "invalid"
    if n_text == 0:
        return "implement"
    if n_keys == n_text and all(item in codes for item in texts):
        return "implement"
    return "process"


def is_process_queue(meta: dict) -> bool:
    return coding_state(meta) == "process"


def is_implement_queue(meta: dict) -> bool:
    return coding_state(meta) == "implement"


def is_invalid_queue(meta: dict) -> bool:
    return coding_state(meta) == "invalid"


def safe_file(name: str) -> Path | None:
    base = Path(unquote(name or "")).name
    if not base.lower().endswith(".json") or base.startswith("."):
        return None
    folder = work_dir().resolve()
    path = (folder / base).resolve()
    if path.parent != folder:
        return None
    return path


def unique_name(source: str, day: str) -> str:
    slug = NAME_RE.sub("-", source or "untitled").strip("-") or "untitled"
    base = f"{slug}-{day}.json"
    folder = work_dir()
    if not (folder / base).exists():
        return base
    n = 2
    while (folder / f"{slug}-{day}-{n}.json").exists():
        n += 1
    return f"{slug}-{day}-{n}.json"


def read_work_file(name: str) -> tuple[str, dict] | tuple[None, None]:
    path = safe_file(name)
    if not path or not path.is_file():
        return None, None
    try:
        doc = normalize_doc(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None, None
    return path.name, doc


def write_work_file(name: str, doc: dict) -> dict:
    path = safe_file(name)
    if not path:
        raise ValueError("檔名無效")
    doc = normalize_doc(doc)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return doc


def iter_work_files() -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for path in sorted(work_dir().glob("*.json")):
        try:
            doc = normalize_doc(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        out.append((path.name, doc))
    return out


def file_summary(name: str, doc: dict) -> dict:
    meta = doc["metadata"]
    return {
        "name": name,
        "source": meta.get("source") or "",
        "date": meta.get("date") or "",
        "extracted": bool(meta.get("extracted")),
        "implemented": bool(meta.get("implemented")),
        "status": coding_state(meta),
        "count": len(meta.get("extracted_text") or []),
        "coded": len(meta.get("codes") or {}),
        "chars": len(doc.get("raw_text") or ""),
    }


def analyze_document(doc: dict) -> tuple[dict, int, int]:
    doc = normalize_doc(doc)
    raw = doc["raw_text"]
    meta = doc["metadata"]
    dpath = current_dict()
    if not dpath.is_file():
        raise FileNotFoundError(f"找不到字典檔：{dpath}")
    known = load_dict_words(dpath)
    extracted = extract_missing(raw, known, load_skipped(dpath))
    codes = {k: v for k, v in (meta.get("codes") or {}).items() if k in extracted}
    meta.update(
        {
            "extracted": True,
            "extracted_text": extracted,
            "codes": codes,
            "implemented": False,
            "date": meta.get("date") or date.today().isoformat(),
            "source": meta.get("source") or "",
        }
    )
    out = normalize_doc({"raw_text": raw, "metadata": meta})
    return out, len(known), len(extracted)


def coded_entries(meta: dict) -> list[tuple[str, str]]:
    items = meta.get("extracted_text") or []
    codes = meta.get("codes") if isinstance(meta.get("codes"), dict) else {}
    entries: list[tuple[str, str]] = []
    for word in items:
        code = str(codes.get(word, "")).strip()
        if code:
            entries.append((word, code))
    return entries


def implement_doc(name: str, doc: dict) -> tuple[dict, list[str]]:
    doc = normalize_doc(doc)
    meta = doc["metadata"]
    if not meta.get("extracted"):
        raise ValueError("尚未擷取")
    if coding_state(meta) != "implement":
        raise ValueError("編碼數量與擷取項不符")
    dpath = current_dict()
    if not dpath.is_file():
        raise ValueError(f"找不到字典檔：{dpath}")
    added = append_dict_entries(coded_entries(meta), dpath)
    remember_implement_skips(meta, dpath)
    meta["implemented"] = True
    doc = write_work_file(name, doc)
    return doc, added


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: object) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path == "/api/info" or path == "/api/settings":
            self._json(200, {**load_settings(), **info_payload()})
            return
        if path == "/api/dict":
            dpath = current_dict()
            if not dpath.is_file():
                self._json(500, {"error": f"找不到字典檔：{dpath}"})
                return
            entries = load_dict_entries(dpath)
            self._json(
                200,
                {
                    "entries": entries,
                    "count": len(entries),
                    "words": len(load_dict_words(dpath)),
                    "dict_path": str(dpath),
                },
            )
            return
        if path == "/api/files":
            kind = (qs.get("kind") or [""])[0]
            rows = []
            for name, doc in iter_work_files():
                meta = doc["metadata"]
                if kind == "process" and not is_process_queue(meta):
                    continue
                if kind == "implement" and not is_implement_queue(meta):
                    continue
                if kind == "invalid" and not is_invalid_queue(meta):
                    continue
                rows.append(file_summary(name, doc))
            self._json(200, {"files": rows, "auto_implement": auto_implement()})
            return
        if path == "/api/file":
            name = (qs.get("name") or [""])[0]
            got, doc = read_work_file(name)
            if not doc:
                self._json(404, {"error": "找不到檔案"})
                return
            self._json(200, {"name": got, "document": doc})
            return
        if path == "/" or path == "/index.html":
            target = STATIC / "index.html"
        else:
            target = (STATIC / path.lstrip("/")).resolve()
            if STATIC not in target.parents and target != STATIC:
                self._json(403, {"error": "禁止存取"})
                return
        if not target.is_file():
            self._json(404, {"error": "找不到"})
            return
        ctype = "text/html; charset=utf-8"
        if target.suffix == ".js":
            ctype = "text/javascript; charset=utf-8"
        elif target.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif target.suffix == ".json":
            ctype = "application/json; charset=utf-8"
        elif target.suffix == ".xml":
            ctype = "application/xml; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._json(400, {"error": "JSON 無效"})
            return
        routes = {
            "/api/analyze": self._analyze,
            "/api/settings": self._save_settings,
            "/api/pick-dict": lambda _b: self._pick("file"),
            "/api/pick-dir": lambda _b: self._pick("dir"),
            "/api/dict/add": self._dict_add,
            "/api/files/save": self._files_save,
            "/api/files/analyze": self._files_analyze,
            "/api/files/delete": self._files_delete,
            "/api/files/implement": self._files_implement,
            "/api/files/clear-codes": self._files_clear_codes,
        }
        fn = routes.get(path)
        if not fn:
            self._json(404, {"error": "找不到"})
            return
        fn(body)

    def _save_settings(self, body: dict) -> None:
        if "dict_path" in body and str(body.get("dict_path") or "").strip():
            _path, err = set_dict_path(str(body.get("dict_path") or ""))
            if err:
                self._json(400, {"error": err})
                return
        if "work_dir" in body and str(body.get("work_dir") or "").strip():
            _path, err = set_work_dir(str(body.get("work_dir") or ""))
            if err:
                self._json(400, {"error": err})
                return
        if "auto_implement" in body:
            settings = load_settings()
            settings["auto_implement"] = bool(body.get("auto_implement"))
            save_settings(settings)
        if "auto_redeploy" in body:
            settings = load_settings()
            settings["auto_redeploy"] = bool(body.get("auto_redeploy"))
            save_settings(settings)
        self._json(200, {**info_payload(), "ok": True})

    def _pick(self, kind: str) -> None:
        try:
            chosen = pick_dialog(kind)
        except Exception as exc:
            self._json(500, {"error": f"選檔失敗：{exc}"})
            return
        if not chosen:
            self._json(200, {**info_payload(), "cancelled": True})
            return
        if kind == "dir":
            _path, err = set_work_dir(chosen)
        else:
            _path, err = set_dict_path(chosen)
        if err:
            self._json(400, {"error": err})
            return
        self._json(200, {**info_payload(), "ok": True})

    def _dict_add(self, body: dict) -> None:
        word = str(body.get("word") or "").strip()
        code = str(body.get("code") or "").strip()
        if not word or not code:
            self._json(400, {"error": "請填詞與編碼"})
            return
        dpath = current_dict()
        if not dpath.is_file():
            self._json(500, {"error": f"找不到字典檔：{dpath}"})
            return
        added = append_dict_entries([(word, code)], dpath)
        extra = maybe_redeploy(len(added))
        self._json(200, {"added": added, "count": len(added), "word": word, "code": code, **extra})

    def _analyze(self, body: dict) -> None:
        doc = body.get("document") if isinstance(body.get("document"), dict) else body
        try:
            out, known, missing = analyze_document(doc)
        except FileNotFoundError as exc:
            self._json(500, {"error": str(exc)})
            return
        self._json(200, {"document": out, "known": known, "missing": missing})

    def _files_analyze(self, body: dict) -> None:
        name = str(body.get("name") or "")
        got, doc = read_work_file(name)
        if not doc or not got:
            self._json(404, {"error": "找不到檔案"})
            return
        try:
            out, known, missing = analyze_document(doc)
        except FileNotFoundError as exc:
            self._json(500, {"error": str(exc)})
            return
        out = write_work_file(got, out)
        self._json(
            200,
            {
                "name": got,
                "document": out,
                "known": known,
                "missing": missing,
                "summary": file_summary(got, out),
            },
        )

    def _files_save(self, body: dict) -> None:
        doc = body.get("document") if isinstance(body.get("document"), dict) else None
        if not isinstance(doc, dict):
            self._json(400, {"error": "缺少文件"})
            return
        doc = normalize_doc(doc)
        name = str(body.get("name") or "").strip()
        if name:
            if not safe_file(name):
                self._json(400, {"error": "檔名無效"})
                return
        else:
            meta = doc["metadata"]
            name = unique_name(str(meta.get("source") or "untitled"), str(meta.get("date") or date.today().isoformat()))
        try:
            doc = write_work_file(name, doc)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, {"name": Path(name).name, "document": doc, "summary": file_summary(Path(name).name, doc)})

    def _files_delete(self, body: dict) -> None:
        names = body.get("names") if isinstance(body.get("names"), list) else []
        deleted = []
        for name in names:
            path = safe_file(str(name))
            if path and path.is_file():
                path.unlink()
                deleted.append(path.name)
        self._json(200, {"deleted": deleted, "count": len(deleted)})

    def _files_clear_codes(self, body: dict) -> None:
        name = str(body.get("name") or "")
        got, doc = read_work_file(name)
        if not doc or not got:
            self._json(404, {"error": "找不到檔案"})
            return
        trim = str(body.get("trim") or "").strip()
        if not trim:
            trim = "extra" if body.get("drop_extra") else "all"
        meta = doc["metadata"]
        codes = dict(meta.get("codes") or {})
        texts = meta.get("extracted_text") or []
        n_text = len(texts)
        if trim == "all":
            codes = {}
        elif trim == "extra":
            keep = set(texts)
            codes = {k: v for k, v in codes.items() if k in keep}
        elif trim in ("begin", "end") and len(codes) > n_text:
            extra = len(codes) - n_text
            keys = list(codes.keys())
            drop = keys[:extra] if trim == "begin" else keys[-extra:]
            for key in drop:
                codes.pop(key, None)
        else:
            self._json(400, {"error": "修正方式無效"})
            return
        meta["codes"] = codes
        doc = write_work_file(got, doc)
        self._json(200, {"name": got, "document": doc, "summary": file_summary(got, doc)})

    def _files_implement(self, body: dict) -> None:
        names = body.get("names") if isinstance(body.get("names"), list) else []
        results = []
        for name in names:
            got, doc = read_work_file(str(name))
            if not doc or not got:
                results.append({"name": str(name), "error": "找不到檔案"})
                continue
            try:
                doc, added = implement_doc(got, doc)
            except ValueError as exc:
                results.append({"name": got, "error": str(exc)})
                continue
            results.append({"name": got, "added": added, "count": len(added), "document": doc})
        extra = maybe_redeploy(sum(r.get("count") or 0 for r in results if "error" not in r))
        self._json(200, {"results": results, **extra})


def start_server() -> ThreadingHTTPServer:
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        httpd = ThreadingHTTPServer((HOST, 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def main() -> None:
    global WINDOW
    work_dir()
    try:
        import webview
    except ImportError:
        sys.exit("需要 pywebview：py -3 -m pip install pywebview")
    httpd = start_server()
    host, port = httpd.server_address[:2]
    url = f"http://{host}:{port}/"
    WINDOW = webview.create_window(
        "火星文",
        url,
        width=1100,
        height=760,
        min_size=(800, 560),
        background_color="#141416",
        text_select=True,
    )
    try:
        webview.start()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
