import os
from dotenv import load_dotenv

load_dotenv()
# -*- coding: utf-8 -*-
import io, os
from dotenv import load_dotenv
from typing import List, Tuple
import numpy as np
from dotenv import load_dotenv
from PIL import Image

ICON_PATH    = os.getenv("ICON_PATH", os.path.join("data", "badge.png"))
PADDLE_LANG  = os.getenv("PADDLE_LANG", "ch")
TESS_CMD     = os.getenv("TESSERACT_CMD", "").strip()
TESS_LANGS   = os.getenv("TESSERACT_LANGS", "chi_tra+chi_sim+eng")
OCR_STRICT   = int(os.getenv("OCR_STRICT", "1") or "1")  # 1=嚴格

_USE_PADDLE = False
try:
    from paddleocr import PaddleOCR
    _paddle = PaddleOCR(lang=PADDLE_LANG, use_angle_cls=True, show_log=False)
    _USE_PADDLE = True
except Exception:
    _paddle = None

try:
    import cv2
    _cv2 = cv2
except Exception:
    _cv2 = None

try:
    import pytesseract
    if TESS_CMD:
        pytesseract.pytesseract.tesseract_cmd = TESS_CMD
    _pyt = pytesseract
except Exception:
    _pyt = None


# ---------- 基礎 ----------
def _to_bgr(image_bytes: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    arr = np.array(img)
    return arr[:, :, ::-1].copy() if _cv2 is not None else arr

def _clean_text(s: str) -> str:
    t = s.strip()
    # 常見噪音符號去除 / 正規化空白
    bad = ".,:;|/\\[]{}()<>~=*_`^'\""
    for ch in bad:
        t = t.replace(ch, " ")
    t = " ".join(t.split())
    return t

def _looks_like_name(s: str) -> bool:
    t = _clean_text(s)
    if not (2 <= len(t) <= 14):
        return False

    # 統計類型
    cjk = sum(0x4e00 <= ord(ch) <= 0x9fff for ch in t)
    alnum = sum(ch.isalnum() for ch in t)
    sym = len(t) - (cjk + alnum + t.count(" "))
    if sym > max(1, len(t) // 4):  # 符號比例太高
        return False

    # 必須要有 CJK 或英數連續段
    has_core = (cjk >= 1) or (alnum >= 2)
    if not has_core:
        return False

    # 太像整句話也砍（有空白但無法當名字的長句）
    if t.count(" ") >= 3 and len(t) >= 10:
        return False

    return True

def _nms_rects(rects: List[Tuple[int,int,int,int]], iou_th=0.35):
    if not rects: return []
    rects = sorted(rects, key=lambda r: r[2]*r[3], reverse=True)
    keep = []
    def iou(a,b):
        ax1,ay1,aw,ah = a; ax2,ay2 = ax1+aw, ay1+ah
        bx1,by1,bw,bh = b; bx2,by2 = bx1+bw, by1+bh
        ix1,iy1 = max(ax1,bx1), max(ay1,by1)
        ix2,iy2 = min(ax2,bx2), min(ay2,by2)
        iw, ih = max(0, ix2-ix1), max(0, iy2-iy1)
        inter = iw*ih
        u = aw*ah + bw*bh - inter
        return inter / u if u>0 else 0.0
    while rects:
        cur = rects.pop(0)
        keep.append(cur)
        rects = [r for r in rects if iou(cur,r) < iou_th]
    return keep


# ---------- 徽章定位（與顏色無關的邊緣模板） ----------
def _edges(img_bgr):
    g = _cv2.cvtColor(img_bgr, _cv2.COLOR_BGR2GRAY)
    g = _cv2.GaussianBlur(g, (3,3), 0)
    return _cv2.Canny(g, 50, 120)

def _find_badges(frame_bgr, tpl_bgr):
    frame_edge = _edges(frame_bgr)
    th, tw = tpl_bgr.shape[:2]
    tpl_edge = _edges(tpl_bgr)
    rects = []
    for s in (0.9, 1.0, 1.1, 1.2):
        h = max(12, int(th*s)); w = max(12, int(tw*s))
        tpl_s = _cv2.resize(tpl_edge, (w, h), interpolation=_cv2.INTER_AREA)
        if frame_edge.shape[0] < h or frame_edge.shape[1] < w:
            continue
        res = _cv2.matchTemplate(frame_edge, tpl_s, _cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= 0.56)
        for (yy, xx) in zip(ys, xs):
            rects.append((int(xx), int(yy), int(w), int(h)))
    return _nms_rects(rects, 0.35)


# ---------- OCR 單一區塊 ----------
def _ocr_roi(roi_bgr) -> str:
    text = ""

    # 去特效 → 強化文字
    g = _cv2.cvtColor(roi_bgr, _cv2.COLOR_BGR2GRAY)
    g = _cv2.bilateralFilter(g, 7, 60, 60)
    bw = _cv2.adaptiveThreshold(g, 255, _cv2.ADAPTIVE_THRESH_MEAN_C, _cv2.THRESH_BINARY, 31, 5)

    # 先 Paddle（置信度高一點）
    if _USE_PADDLE:
        try:
            res = _paddle.ocr(roi_bgr, cls=True)
            cand = []
            for line in res:
                for _, (txt, score) in line:
                    if score >= (0.75 if OCR_STRICT else 0.6):
                        cand.append(txt)
            if cand:
                text = max(cand, key=len)
        except Exception:
            pass

    # 再 Tesseract（限定語言）
    if not text and _pyt is not None:
        try:
            pil = Image.fromarray(bw)
            cfg = f'--oem 3 --psm 7 -l {TESS_LANGS} -c preserve_interword_spaces=0'
            t = _pyt.image_to_string(pil, config=cfg)
            text = t.strip()
        except Exception:
            text = ""

    text = _clean_text(text)
    return text if _looks_like_name(text) else ""


# ---------- 對外主函式 ----------
def extract_names(image_bytes: bytes) -> List[str]:
    """只回傳「像名字」的字串；已去雜訊、去重。"""
    if _cv2 is None:
        # 無 OpenCV 時，退回整張 OCR，但套嚴格過濾
        out = []
        try:
            pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            arr = np.array(pil)
            if _USE_PADDLE:
                res = _paddle.ocr(arr[:, :, ::-1], cls=True)
                for line in res:
                    for _, (txt, score) in line:
                        if score >= (0.78 if OCR_STRICT else 0.65):
                            t = _clean_text(txt)
                            if _looks_like_name(t):
                                out.append(t)
            elif _pyt is not None:
                t = _pyt.image_to_string(pil, config=f'--oem 3 --psm 6 -l {TESS_LANGS}')
                for ln in t.splitlines():
                    ln = _clean_text(ln)
                    if _looks_like_name(ln):
                        out.append(ln)
        except Exception:
            pass
        return _dedupe(out)[:120]

    frame = _to_bgr(image_bytes)

    # 若有徽章樣板 → 只掃右側名條；否則整張當保底
    if os.path.exists(ICON_PATH):
        tpl = _cv2.imread(ICON_PATH)
        if tpl is not None:
            rects = _find_badges(frame, tpl)
            names: List[str] = []
            for (x, y, w, h) in rects:
                cx = x + w
                cy = y + h // 2
                RIGHT = 220
                UP = int(h * 0.8)
                DOWN = int(h * 0.8)
                rx1 = max(0, cx + 4)
                rx2 = min(frame.shape[1], rx1 + RIGHT)
                ry1 = max(0, cy - UP)
                ry2 = min(frame.shape[0], cy + DOWN)
                if rx2 - rx1 < 15 or ry2 - ry1 < 10:
                    continue
                roi = frame[ry1:ry2, rx1:rx2]
                t = _ocr_roi(roi)
                if t:
                    names.append(t)
            if names:
                return _dedupe(names)[:120]

    # 沒抓到 → 整張做一次（保底）
    return _fallback_full(frame)[:120]


def _fallback_full(frame_bgr) -> List[str]:
    out = []
    if _USE_PADDLE:
        try:
            res = _paddle.ocr(frame_bgr, cls=True)
            for line in res:
                for _, (txt, score) in line:
                    if score >= (0.80 if OCR_STRICT else 0.65):
                        t = _clean_text(txt)
                        if _looks_like_name(t):
                            out.append(t)
        except Exception:
            pass
    if not out and _pyt is not None:
        try:
            g = _cv2.cvtColor(frame_bgr, _cv2.COLOR_BGR2GRAY)
            g = _cv2.bilateralFilter(g, 7, 60, 60)
            bw = _cv2.adaptiveThreshold(g, 255, _cv2.ADAPTIVE_THRESH_MEAN_C,
                                        _cv2.THRESH_BINARY, 31, 5)
            pil = Image.fromarray(bw)
            t = _pyt.image_to_string(pil, config=f'--oem 3 --psm 6 -l {TESS_LANGS}')
            for ln in t.splitlines():
                ln = _clean_text(ln)
                if _looks_like_name(ln):
                    out.append(ln)
        except Exception:
            pass
    return _dedupe(out)


def _dedupe(names: List[str]) -> List[str]:
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n); out.append(n)
    return out


