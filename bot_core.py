"""
Shinra Typewriter core: finds the word boxes on screen, reads them with OCR and types them.
Used by app.py (the window). Detection is tuned for the FFXIV "Keyboard Square" typing event.
"""

import json
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict, fields

# One thread per Tesseract call: much faster when several reads run in parallel
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

import cv2
import numpy as np
import pytesseract

APP_NAME = "Shinra Typewriter"
DATA_DIR = os.path.join(os.path.expanduser("~"), "Documents", APP_NAME)
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
SCREENSHOT_DIR = os.path.join(DATA_DIR, "screenshots")

# ---- detection tuning (not exposed in the app) ----
SEARCH_TOP = 0.07           # word boxes appear between these fractions of screen height
SEARCH_BOTTOM = 0.64
BOX_MIN_H, BOX_MAX_H = 40, 100   # box size in pixels at 1440p (scaled for other heights)
BOX_MIN_W = 70
BOX_MAX_W_FRAC = 0.25
OCR_MIN_CONF = 60
CONFIRM_SCANS = 2           # less-confident readings must repeat this many scans...
INSTANT_CONF = 90           # ...readings at least this confident are typed right away
OCR_THREADS = max(2, min(8, (os.cpu_count() or 4)))
GREEN_MIN_FRAC = 0.3
RED_MIN_FRAC = 0.4
INPUT_BAR_AREA = (0.33, 0.62, 0.67, 0.72)
INPUT_DISABLED_RED_FRAC = 0.25
SCAN_DELAY = 0.02
AUTOSHOT_INTERVAL = 5.0

ALLOWED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '\-.,!?&:]*$")
# garbage patterns: punctuation glued to a letter, stray symbols, or a lone lowercase
# letter other than "a" (a sign that part of a word was lost)
JUNK = re.compile(r"[,.!?:&][A-Za-z0-9]|\s[^A-Za-z0-9\s\-]|\s-(?!\s)|[^A-Za-z0-9\s]{2,}|(?:^|\s)[b-z](?:\s|$)")
SHIFT_CHARS = {"!": "1", "?": "/", ":": ";", "(": "9", ")": "0", "&": "7", "_": "-"}


# ============================ SETTINGS ============================

@dataclass
class Settings:
    monitor: int = 1                # mss monitor number (1 = primary)
    key_delay_ms: int = 25          # gap between keystrokes
    key_hold_ms: int = 15           # how long each key is held
    enter_delay_ms: int = 100       # pause after the last letter before Enter
    after_word_ms: int = 60         # pause after Enter before the next word
    press_enter: bool = True
    match_case: bool = True         # type capitals with Shift
    green_first: bool = True        # type green boxes before anything else
    red_second: bool = True         # then red boxes
    repeat_block_s: float = 2.0     # don't retype the same word within this many seconds
    only_in_game: bool = True       # only type while the FFXIV window is active

    @classmethod
    def load(cls):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            known = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in data.items() if k in known})
        except Exception:
            return cls()

    def save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2)
        except Exception:
            pass


# ============================ TESSERACT ============================

def setup_tesseract():
    """Use the copy bundled inside the app if there is one, else an installed one.
    Returns the path used, or None if Tesseract can't be found."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(base, "tesseract", "tesseract.exe")
    if os.path.exists(bundled):
        pytesseract.pytesseract.tesseract_cmd = bundled
        os.environ["TESSDATA_PREFIX"] = os.path.join(base, "tesseract", "tessdata")
        return bundled
    for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
              shutil.which("tesseract")):
        if p and os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            return p
    return None


# ============================ DETECTION ============================

def edge_map(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 120)
    return cv2.dilate(edges, np.ones((3, 3), np.uint8))


def side_cov(edges, x, y, w, h):
    """Fraction of each side of the rectangle that lies on an edge."""
    m = max(4, int(h * 0.3))
    t = 4
    top = (edges[y:y + t, x + m:x + w - m].max(axis=0) > 0).mean()
    bot = (edges[y + h - t:y + h, x + m:x + w - m].max(axis=0) > 0).mean()
    lef = (edges[y + m:y + h - m, x:x + t].max(axis=1) > 0).mean()
    rig = (edges[y + m:y + h - m, x + w - t:x + w].max(axis=1) > 0).mean()
    return top, bot, lef, rig


def find_boxes(frame):
    H, W = frame.shape[:2]
    s = H / 1440.0
    y_lo, y_hi = SEARCH_TOP * H, SEARCH_BOTTOM * H
    # only look for edges in the band where boxes can be
    b0, b1 = max(0, int(y_lo) - 4), min(H, int(y_hi) + 4)
    edges = np.zeros((H, W), np.uint8)
    edges[b0:b1] = edge_map(frame[b0:b1])

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    pieces = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if BOX_MIN_H * s <= h <= BOX_MAX_H * s and 40 * s <= w <= BOX_MAX_W_FRAC * W \
                and y >= y_lo and y + h <= y_hi:
            pieces.append((x, y, w, h))

    # An outline broken up by effects (or by a see-through box behind it) shows up as
    # several side-by-side pieces with the same top and bottom: add their unions too.
    merged = list(pieces)
    frontier = list(pieces)
    for _ in range(3):
        new = []
        for p in frontier:
            for q in pieces:
                if q == p:
                    continue
                gap = 16 * s
                if abs(p[1] - q[1]) <= 5 * s and abs(p[3] - q[3]) <= 6 * s and \
                        p[0] <= q[0] + q[2] + gap and q[0] <= p[0] + p[2] + gap:
                    x0, y0 = min(p[0], q[0]), min(p[1], q[1])
                    x1, y1 = max(p[0] + p[2], q[0] + q[2]), max(p[1] + p[3], q[1] + q[3])
                    u = (x0, y0, x1 - x0, y1 - y0)
                    if u not in merged:
                        merged.append(u)
                        new.append(u)
        frontier = new
        if not new:
            break

    # Overlapping boxes at different heights can have their outlines joined into one
    # shape. Catch those by pairing each box's top border line with its bottom border line.
    hz = cv2.morphologyEx(edges, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, int(50 * s)), 1)))
    n, _, st, _ = cv2.connectedComponentsWithStats(hz)
    lines = [(x, y, w, h) for x, y, w, h, _ in st[1:]
             if h <= 10 * s and y_lo <= y <= y_hi and w <= BOX_MAX_W_FRAC * W]
    lines.sort(key=lambda l: l[1])
    tol = 10 * s
    for i, t in enumerate(lines):
        for b in lines[i + 1:]:
            dy = b[1] + b[3] - t[1]
            if dy > BOX_MAX_H * s:
                break
            if dy < BOX_MIN_H * s:
                continue
            left_ok = abs(t[0] - b[0]) <= tol
            right_ok = abs((t[0] + t[2]) - (b[0] + b[2])) <= tol
            if left_ok and right_ok:
                x0 = min(t[0], b[0])
                x1 = max(t[0] + t[2], b[0] + b[2])
                merged.append((x0, t[1], x1 - x0, dy))
            elif left_ok or right_ok:
                # one border line ran into background lines at the same height:
                # use the part both lines share
                x0 = max(t[0], b[0])
                x1 = min(t[0] + t[2], b[0] + b[2])
                if x1 - x0 >= 0.9 * min(t[2], b[2]):
                    merged.append((x0, t[1], x1 - x0, dy))

    boxes = []
    for (x, y, w, h) in merged:
        if not (BOX_MIN_H * s <= h <= BOX_MAX_H * s and BOX_MIN_W * s <= w <= BOX_MAX_W_FRAC * W
                and w / h >= 1.3):
            continue
        top, bot, lef, rig = side_cov(edges, x, y, w, h)
        if min(top, bot) < 0.75 or min(lef, rig) < 0.3 or max(lef, rig) < 0.6:
            continue
        if (x, y, w, h) not in boxes:
            boxes.append((x, y, w, h))

    # Green and red boxes are also found by their fill colour (their outlines can blend
    # into the background)
    hsv = cv2.cvtColor(frame[b0:b1], cv2.COLOR_BGR2HSV)
    for mask in (_green_mask(hsv), _red_mask(hsv)):
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((int(15 * s) | 1,) * 2, np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cs:
            x, y, w, h = cv2.boundingRect(c)
            if w < BOX_MIN_W * s or h < BOX_MIN_H * s * 0.8 or w > BOX_MAX_W_FRAC * W or h > 3 * BOX_MAX_H * s:
                continue
            # trim the glow: keep rows/columns that are mostly filled
            sub = mask[y:y + h, x:x + w] > 0
            rows = np.where(sub.mean(axis=1) >= 0.6)[0]
            cols = np.where(sub.mean(axis=0) >= 0.6)[0]
            if len(rows) == 0 or len(cols) == 0:
                continue
            x, w = x + cols[0], cols[-1] - cols[0] + 1
            y, h = y + rows[0] + b0, rows[-1] - rows[0] + 1
            if not (BOX_MIN_H * s * 0.8 <= h <= BOX_MAX_H * s and BOX_MIN_W * s <= w <= BOX_MAX_W_FRAC * W
                    and w / h >= 1.3 and y >= y_lo and y + h <= y_hi):
                continue
            if (mask[y - b0:y - b0 + h, x:x + w] > 0).mean() < 0.75:
                continue
            if (x, y, w, h) not in boxes:
                boxes.append((x, y, w, h))
    return boxes


def _green_mask(hsv):
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    return (((h >= 40) & (h <= 85) & (s >= 90) & (v >= 70)) * 255).astype(np.uint8)


def _red_mask(hsv):
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    return ((((h <= 8) | (h >= 170)) & (s >= 110) & (v >= 70)) * 255).astype(np.uint8)


def box_colour(frame, box):
    """'green', 'red' or 'other', from the fill colour inside the box."""
    x, y, w, h = box
    p = max(5, int(h * 0.16))
    hsv = cv2.cvtColor(frame[y + p:y + h - p, x + p:x + w - p], cv2.COLOR_BGR2HSV)
    if hsv.size == 0:
        return "other"
    if (_green_mask(hsv) > 0).mean() >= GREEN_MIN_FRAC:
        return "green"
    if (_red_mask(hsv) > 0).mean() >= RED_MIN_FRAC:
        return "red"
    return "other"


def overlap(a, b):
    """intersection / area of the smaller box"""
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    i = max(0, x2 - x1) * max(0, y2 - y1)
    return i / float(min(a[2] * a[3], b[2] * b[3]))


def text_mask(frame, box):
    """White-on-black text mask, separated by colour distance from the box's fill,
    so it works for any box/text colour combination."""
    x, y, w, h = box
    p = max(5, int(h * 0.16))
    crop = frame[y + p:y + h - p, x + p:x + w - p].astype(np.float32)
    if crop.shape[0] < 8 or crop.shape[1] < 8:
        return None, p
    fill = np.median(crop.reshape(-1, 3), axis=0)
    dist = np.linalg.norm(crop - fill, axis=2)
    dist = np.clip(dist * (255.0 / max(1.0, dist.max())), 0, 255).astype(np.uint8)
    _, bw = cv2.threshold(dist, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Drop specks, and border lines of a neighbouring box (thin full-height lines showing
    # through, or line/corner pieces poking in from the edge of the crop)
    n, lab, st, _ = cv2.connectedComponentsWithStats(bw)
    ch, cw_ = bw.shape
    for i in range(1, n):
        cx, cy, cw, chh, area = st[i]
        # a neighbour's border entering from the top/bottom: flat and wide, never a letter
        flat_edge_line = (cy == 0 or cy + chh >= ch) and chh <= 0.45 * ch and cw >= 2.5 * chh
        if (chh >= 0.9 * ch and cw <= 6) or area < 6 or flat_edge_line:
            bw[lab == i] = 0
    return bw, p


def _ocr_once(bw, height):
    img = 255 - bw
    scale = height / img.shape[0]
    img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    img = cv2.copyMakeBorder(img, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
    d = pytesseract.image_to_data(img, config="--psm 7", output_type=pytesseract.Output.DICT)
    ws = [(t.strip(), float(c)) for t, c in zip(d["text"], d["conf"]) if t.strip() and float(c) >= 0]
    if not ws:
        return "", 0
    text = " ".join(t for t, _ in ws)
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = fix_roman(text).replace("|", "I")
    return text, min(c for _, c in ws)


def fix_roman(text):
    """OCR often reads the I's in Roman numerals as l, 1 or |: "Red Xlll" -> "Red XIII"."""
    out = []
    for tok in text.split(" "):
        core = tok.rstrip(".,!?")
        # starts with a capital X, V or I and the rest only looks like X / V / I
        if len(core) >= 2 and re.fullmatch(r"[XVI][XVIil1|]+", core):
            tok = re.sub(r"[il1|]", "I", core) + tok[len(core):]
        out.append(tok)
    return " ".join(out)


def ocr(bw):
    """One read; if it's very confident, done. Otherwise read at a second size and
    require agreement (ignoring case); if they disagree, two more reads and a clear
    majority. conf is 0 when there's no agreement."""
    first = _ocr_once(bw, 80)
    if first[0] and first[1] >= INSTANT_CONF:
        return first
    reads = [first, _ocr_once(bw, 130)]
    if reads[0][0].lower() != reads[1][0].lower():
        reads += [_ocr_once(bw, 100), _ocr_once(bw, 60)]
    groups = {}
    for t, c in reads:
        if t:
            groups.setdefault(t.lower(), []).append((t, c))
    if not groups:
        return "", 0
    ranked = sorted(groups.values(), key=lambda g: (len(g), max(c for _, c in g)), reverse=True)
    best = ranked[0]
    if len(best) < 2 or (len(ranked) > 1 and len(ranked[1]) == len(best)):
        return best[0][0], 0
    variants = {}
    for t, c in best:
        variants[t] = variants.get(t, 0) + c
    return max(variants, key=variants.get), max(c for _, c in best)


def classify(frame, box):
    """-> (text, conf, status); status is 'ok' or why the box was skipped."""
    bw, p = text_mask(frame, box)
    if bw is None:
        return "", 0, "empty"
    cols = np.where((bw > 0).sum(axis=0) >= 2)[0]
    if len(cols) == 0:
        return "", 0, "empty"
    h = box[3]
    left, right = cols[0] + p, box[2] - (cols[-1] + p + 1)
    # Real boxes have even padding either side; a box partly behind another doesn't
    if min(left, right) < 0.33 * h or min(left, right) / max(left, right) < 0.5:
        return "", 0, "cut-off"
    if np.diff(cols).max(initial=0) > 0.6 * h:
        return "", 0, "two prompts"
    text, conf = ocr(bw)
    if len(re.sub(r"[^A-Za-z0-9]", "", text)) < 2 or not ALLOWED.match(text) or JUNK.search(text):
        return text, conf, "bad chars"
    return text, conf, "ok"


_cache = {}
_pool = ThreadPoolExecutor(OCR_THREADS)


def classify_cached(frame, box):
    """Re-use the last reading if this box looks the same as last scan (saves OCR time)."""
    x, y, w, h = box
    thumb = cv2.resize(cv2.cvtColor(frame[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY), (64, 16),
                       interpolation=cv2.INTER_AREA).astype(np.int16)
    key = (x // 4, y // 4, w // 4, h // 4)
    now = time.time()
    hit = _cache.get(key)
    if hit is not None and np.abs(hit[0] - thumb).mean() < 3:
        _cache[key] = (hit[0], hit[1], now)
        return hit[1]
    res = classify(frame, box)
    _cache[key] = (thumb, res, now)
    if len(_cache) > 400:
        for k in [k for k, v in _cache.items() if now - v[2] > 5]:
            del _cache[k]
    return res


def read_words(frame):
    """-> (accepted [(box, text, conf)], rejected [(box, text, conf, reason)])"""
    boxes = find_boxes(frame)
    results = list(_pool.map(lambda b: classify_cached(frame, b), boxes))
    cands = [(b, t, c, st) for b, (t, c, st) in zip(boxes, results) if st in ("ok", "bad chars")]
    # Overlapping candidates: keep the smallest. A rectangle covering a front box plus
    # part of the box behind it is always bigger than the front box on its own.
    cands.sort(key=lambda r: r[0][2] * r[0][3])
    kept = []
    for r in cands:
        if all(overlap(r[0], k[0]) < 0.5 for k in kept):
            kept.append(r)
    accepted, rejected = [], []
    for b, text, conf, st in kept:
        if st == "ok" and conf >= OCR_MIN_CONF:
            accepted.append((b, text, conf))
        else:
            rejected.append((b, text, conf, st if st != "ok" else "low conf"))
    return accepted, rejected


def inputs_disabled(frame):
    H, W = frame.shape[:2]
    l, t, r, b = INPUT_BAR_AREA
    hsv = cv2.cvtColor(frame[int(t * H):int(b * H), int(l * W):int(r * W)], cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    red = ((hue < 8) | (hue > 165)) & (sat > 90) & (val > 90)
    return red.mean() > INPUT_DISABLED_RED_FRAC


GAME_WINDOW_TITLE = "FINAL FANTASY XIV"


def game_window_active():
    """True when the active window is the game (always True off Windows)."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return GAME_WINDOW_TITLE.lower() in buf.value.lower()
    except Exception:
        return True


# ============================ BOT ENGINE ============================

class Bot:
    """Runs the scan-and-type loop on a background thread.

    on_event(kind, text) is called from the bot thread for things the window shows:
      kind = "typed" (text = "word|colour"), "status", "info", "error"
    """

    def __init__(self, settings: Settings, on_event):
        self.settings = settings
        self.on_event = on_event
        self.running = False
        self.autoshot = False
        self._quit = False
        self._snapshot = False
        self._screenshot = False
        self._typed = {}            # lowercased word -> time it was typed
        self._thread = threading.Thread(target=self._loop, daemon=True)

    # ---- controls (safe to call from any thread) ----
    def start(self):
        self._thread.start()

    def toggle(self):
        self.running = not self.running
        self.on_event("status", "running" if self.running else "paused")

    def request_snapshot(self):
        self._snapshot = True

    def request_screenshot(self):
        self._screenshot = True

    def set_autoshot(self, on):
        self.autoshot = on

    def shutdown(self):
        self.running = False
        self._quit = True

    # ---- typing ----
    def _tap(self, key):
        import pydirectinput
        pydirectinput.keyDown(key)
        time.sleep(self.settings.key_hold_ms / 1000)
        pydirectinput.keyUp(key)

    def _press_char(self, ch):
        import pydirectinput
        if ch == " ":
            self._tap("space")
            return
        shift, base = False, ch
        if ch.isalpha() and ch.isupper():
            base, shift = ch.lower(), True
        elif ch in SHIFT_CHARS:
            base, shift = SHIFT_CHARS[ch], True
        hold = self.settings.key_hold_ms / 1000
        if shift:
            pydirectinput.keyDown("shift")
            time.sleep(hold)
        self._tap(base)
        if shift:
            time.sleep(hold)
            pydirectinput.keyUp("shift")

    def _type_word(self, text):
        st = self.settings
        if not st.match_case:
            text = text.lower()
        for ch in text:
            if not self.running or (st.only_in_game and not game_window_active()):
                return False
            self._press_char(ch)
            time.sleep(st.key_delay_ms / 1000)
        if st.press_enter and self.running:
            time.sleep(st.enter_delay_ms / 1000)
            self._tap("enter")
        time.sleep(st.after_word_ms / 1000)
        return True

    # ---- capture / files ----
    def _grab(self, sct):
        mons = sct.monitors
        idx = self.settings.monitor if 0 < self.settings.monitor < len(mons) else 1
        img = np.array(sct.grab(mons[idx]))
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    def _save_screenshot(self, frame):
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
        path = os.path.join(SCREENSHOT_DIR, f"shot_{stamp}.png")
        cv2.imwrite(path, frame)
        return path

    def _save_snapshot(self, frame, accepted, rejected):
        dbg = frame.copy()
        for (x, y, w, h), text, conf in accepted:
            cv2.rectangle(dbg, (x, y), (x + w, y + h), (0, 255, 0), 3)
            cv2.putText(dbg, f"{text} ({conf:.0f}, {box_colour(frame, (x, y, w, h))})",
                        (x, max(20, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        for (x, y, w, h), text, conf, why in rejected:
            cv2.rectangle(dbg, (x, y), (x + w, y + h), (0, 0, 255), 2)
            cv2.putText(dbg, f"{text} [{why}]", (x, max(20, y - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        if inputs_disabled(frame):
            cv2.putText(dbg, "INPUTS DISABLED", (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 3)
        os.makedirs(DATA_DIR, exist_ok=True)
        path = os.path.join(DATA_DIR, "debug_frame.png")
        cv2.imwrite(path, dbg)
        return path

    # ---- main loop ----
    def _loop(self):
        try:
            import mss
            import pydirectinput
            pydirectinput.PAUSE = 0
        except Exception as e:      # pragma: no cover - only on a broken install
            self.on_event("error", f"Couldn't start: {e}")
            return

        last_autoshot = 0.0
        streak = {}
        was_disabled = False
        was_away = False
        mss_cls = getattr(mss, "MSS", None) or mss.mss
        with mss_cls() as sct:
            while not self._quit:
                try:
                    due_auto = self.autoshot and time.time() - last_autoshot >= AUTOSHOT_INTERVAL
                    if self._screenshot or due_auto:
                        path = self._save_screenshot(self._grab(sct))
                        self._screenshot = False
                        if due_auto:
                            last_autoshot = time.time()
                        self.on_event("info", f"Screenshot saved: {os.path.basename(path)}")

                    if not self.running and not self._snapshot:
                        time.sleep(0.05)
                        continue

                    frame = self._grab(sct)
                    accepted, rejected = read_words(frame)

                    if self._snapshot:
                        self._snapshot = False
                        path = self._save_snapshot(frame, accepted, rejected)
                        self.on_event("snapshot", path)

                    if not self.running:
                        continue

                    disabled = inputs_disabled(frame)
                    away = self.settings.only_in_game and not game_window_active()
                    if disabled != was_disabled or away != was_away:
                        self.on_event("status", "away" if away else "disabled" if disabled else "running")
                        was_disabled, was_away = disabled, away

                    seen = {t.lower() for _, t, _ in accepted}
                    streak = {k: streak.get(k, 0) + 1 for k in seen}
                    if disabled or away:
                        continue

                    prio = []
                    if self.settings.green_first:
                        prio.append("green")
                    if self.settings.red_second:
                        prio.append("red")
                    order = {c: i for i, c in enumerate(prio)}
                    ranked = sorted(
                        ((order.get(box_colour(frame, b), len(order)), -b[3], -b[1], b, t, c)
                         for b, t, c in accepted), key=lambda r: r[:3])
                    for _, _, _, box, text, conf in ranked:
                        if not self.running:
                            break
                        key = text.lower()
                        if time.time() - self._typed.get(key, 0) < self.settings.repeat_block_s:
                            continue
                        if conf < INSTANT_CONF and streak.get(key, 0) < CONFIRM_SCANS:
                            continue
                        colour = box_colour(frame, box)
                        if self._type_word(text):
                            self._typed[key] = time.time()
                            self.on_event("typed", f"{text}|{colour}")

                    time.sleep(SCAN_DELAY)
                except Exception as e:
                    self.on_event("error", f"{type(e).__name__}: {e}")
                    time.sleep(0.5)
