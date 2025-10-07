import os
from dotenv import load_dotenv

load_dotenv()
# -*- coding: utf-8 -*-
from typing import Iterable, List, Tuple
from rapidfuzz import fuzz, process

def dedupe_fuzzy(names: Iterable[str], threshold: int = 92) -> List[str]:
    out: List[str] = []
    for n in names:
        n = n.strip()
        if not n:
            continue
        if not out:
            out.append(n)
            continue
        best = process.extractOne(n, out, scorer=fuzz.token_set_ratio)
        if not best or int(best[1]) < threshold:
            out.append(n)
        else:
            keep = max(n, best[0], key=len)
            out[out.index(best[0])] = keep
    return out

def fuzzy_in(name: str, whitelist: Iterable[str], threshold: int = 90) -> Tuple[bool, str, int]:
    wl = list(whitelist)
    if not wl:
        return False, name, 0
    best = process.extractOne(name, wl, scorer=fuzz.token_set_ratio)
    if best and int(best[1]) >= threshold:
        return True, best[0], int(best[1])
    return False, name, int(best[1] if best else 0)

def chunk_lines(lines: List[str], batch: int) -> List[str]:
    out, cur, count = [], [], 0
    for ln in lines:
        cur.append(ln)
        count += 1
        if count >= batch:
            out.append("\n".join(cur))
            cur = []
            count = 0
    if cur:
        out.append("\n".join(cur))
    return out


