import os
from dotenv import load_dotenv

load_dotenv()
# -*- coding: utf-8 -*-
import json, os, threading
from dotenv import load_dotenv
from typing import Dict, List, Tuple

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DATA_PATH = os.path.join(DATA_DIR, "allies.json")
_lock = threading.Lock()

_DEFAULT = {"allies": [], "mapping": {}}

def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)

def load_data() -> dict:
    _ensure_dir()
    if not os.path.exists(DATA_PATH):
        save_data(_DEFAULT.copy())
        return _DEFAULT.copy()
    with _lock, open(DATA_PATH, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            data = _DEFAULT.copy()
    data.setdefault("allies", [])
    data.setdefault("mapping", {})
    return data

def save_data(data: dict):
    _ensure_dir()
    with _lock, open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_ally(game_id: str, discord_user_id: str | None = None):
    data = load_data()
    if game_id not in data["allies"]:
        data["allies"].append(game_id)
    if discord_user_id:
        data["mapping"][game_id] = str(discord_user_id)
    save_data(data)

def list_allies() -> tuple[list[str], dict]:
    d = load_data()
    return d["allies"], d["mapping"]

def bind(game_id: str, discord_user_id: str):
    d = load_data()
    if game_id not in d["allies"]:
        d["allies"].append(game_id)
    d["mapping"][game_id] = str(discord_user_id)
    save_data(d)


