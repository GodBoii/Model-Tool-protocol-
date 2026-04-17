import sys
import time
import math
import threading
import shutil
from pathlib import Path

class CatEngine:
    def __init__(self):
        # 36x18 virtual pixels = 18x9 terminal characters (using 2x2 Quadrant blocks)
        self.width = 36
        self.height = 18
        self.state = "idle"
        self.is_hidden = False
        self.tick = 0.0
        self.running = False
        self.thread = None
        self._lock = threading.Lock()
        self.telemetry = {
            "cwd": str(Path.cwd().name),
            "sandbox": "workspace-write",
            "files": []
        }
        self.cursor_ratio = 0.5
        
        # Transparent diffing arrays
        self.prev_blocks = [[ "empty" for _ in range(self.width // 2)] for _ in range(self.height // 2)]

    def start(self):
        with self._lock:
            if self.running:
                return
            self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()

    def set_state(self, new_state):
        if self.is_hidden and new_state != "hidden":
            return
        if self.state != new_state:
            if new_state in ("wakeup", "response", "error"):
                self.tick = 0.0
            self.state = new_state

    def set_telemetry(self, data):
        self.telemetry.update(data)

    def set_cursor_ratio(self, ratio):
        self.cursor_ratio = max(0.0, min(1.0, ratio))

    def _loop(self):
        fps = 15
        dt = 1.0 / fps
        try:
            while self.running:
                self._draw_frame()
                time.sleep(dt)
                self.tick += dt
                # auto-revert logic
                if self.state == "wakeup" and self.tick > 2.0:
                    self.state = "thinking"
                elif self.state == "response" and self.tick > 3.0:
                    self.state = "idle"
                elif self.state == "error" and self.tick > 2.0:
                    self.state = "idle"
        except Exception:
            pass

    def _draw_frame(self):
        term_size = shutil.get_terminal_size()
        cols = self.width // 2
        
        if term_size.columns < 40 or term_size.lines < 15:
            return

        start_y = 2
        start_x = term_size.columns - cols - 3

        if not hasattr(self, "last_start_x"):
            self.last_start_x = start_x
            self.last_term_lines = term_size.lines

        # If terminal was resized horizontally or vertically, erase the old position fully
        if self.last_start_x != start_x or self.last_term_lines != term_size.lines:
            erase_out = ["\0337"]
            for y_off in range(self.height // 2):
                erase_out.append(f"\033[{start_y + y_off};{self.last_start_x}H\033[K")
            erase_out.append("\0338")
            sys.stdout.write("".join(erase_out))
            sys.stdout.flush()
            self.last_start_x = start_x
            self.last_term_lines = term_size.lines
            self.prev_blocks = [["empty" for _ in range(cols)] for _ in range(self.height // 2)]

        if self.state == "hidden":
            if not getattr(self, "was_cleared", False):
                # Clear completely off the screen
                out = ["\0337"]
                for cy in range(self.height // 2):
                    out.append(f"\033[{start_y + cy};{start_x}H\033[K")
                out.append("\0338")
                sys.stdout.write("".join(out))
                sys.stdout.flush()
                self.was_cleared = True
                self.prev_blocks = [["empty" for _ in range(cols)] for _ in range(self.height // 2)]
            return
        
        self.was_cleared = False

        pixels = [[None for _ in range(self.width)] for _ in range(self.height)]
        self._render_scene(pixels)

        out = ["\0337"]  # save cursor

        # If generating tokens, text pushes up. Clear the line above to stop trails.
        if self.state in ("response", "thinking"):
            out.append(f"\033[1;{start_x}H\033[K")

        quad_map = [
            ' ', '▗', '▖', '▄', '▝', '▐', '▞', '▟',
            '▘', '▚', '▌', '▙', '▀', '▜', '▛', '█'
        ]

        def color_sq_dist(c1, c2):
            if c1 is None and c2 is None: return 0
            if c1 is None or c2 is None: return 999999
            return (c1[0]-c2[0])**2 + (c1[1]-c2[1])**2 + (c1[2]-c2[2])**2

        for cy in range(0, self.height, 2):
            out.append(f"\033[{start_y + cy//2};{start_x}H")  # move to line start
            
            for cx in range(0, self.width, 2):
                p00 = pixels[cy][cx]
                p10 = pixels[cy][cx+1]
                p01 = pixels[cy+1][cx]
                p11 = pixels[cy+1][cx+1]
                
                block = [p00, p10, p01, p11]
                freq = {}
                for p in block:
                    freq[p] = freq.get(p, 0) + 1
                    
                is_empty = (len(freq) == 1 and list(freq.keys())[0] is None)
                was_empty = (self.prev_blocks[cy//2][cx//2] == "empty")
                
                if is_empty:
                    self.prev_blocks[cy//2][cx//2] = "empty"
                    if was_empty:
                        out.append("\033[1C")  # Skip 1 character entirely (true transparency)
                    else:
                        out.append("\033[0m\033[49m ") # Erase exactly this block
                    continue
                    
                self.prev_blocks[cy//2][cx//2] = "drawn"
                
                # Sort to get top colors
                sorted_colors = sorted(freq.items(), key=lambda x: x[1], reverse=True)
                c_list = [c[0] for c in sorted_colors]
                
                if len(c_list) == 1:
                    fg = c_list[0]
                    bg = None
                else:
                    c1, c2 = c_list[0], c_list[1]
                    if c1 is None:
                        bg, fg = c1, c2
                    elif c2 is None:
                        bg, fg = c2, c1
                    else:
                        fg, bg = c1, c2
                
                char_idx = 0
                for i, p in enumerate(block):
                    if p == fg:
                        mapped = fg
                    elif p == bg:
                        mapped = bg
                    else:
                        # Quantize remaining colors logically to fg or bg
                        if color_sq_dist(p, fg) < color_sq_dist(p, bg):
                            mapped = fg
                        else:
                            mapped = bg
                            
                    if mapped == fg and fg is not None:
                        if i == 0: char_idx |= 8    # Top-Left
                        elif i == 1: char_idx |= 4  # Top-Right
                        elif i == 2: char_idx |= 2  # Bottom-Left
                        elif i == 3: char_idx |= 1  # Bottom-Right
                        
                char = quad_map[char_idx]
                bg_ansi = "\033[49m" if bg is None else f"\033[48;2;{bg[0]};{bg[1]};{bg[2]}m"
                fg_ansi = "" if fg is None else f"\033[38;2;{fg[0]};{fg[1]};{fg[2]}m"
                out.append(f"\033[0m{bg_ansi}{fg_ansi}{char}")
                
            out.append("\033[0m")
            
        self._render_sidebar(out, start_x, start_y, cols)
        out.append("\0338")  # restore cursor
        # Write atomically to terminal
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def _render_sidebar(self, out, start_x, start_y, cols):
        if getattr(self, "state", "idle") == "hidden":
            return
            
        hud_y = start_y + (self.height // 2) + 1
        x = start_x
        
        # Border structure
        w = max(20, cols)
        v = "\033[38;5;238m│\033[0m"
        tl = f"\033[38;5;238m╭{'─' * w}╮\033[0m"
        bl = f"\033[38;5;238m╰{'─' * w}╯\033[0m"
        
        cwd = self.telemetry.get("cwd", "")
        sbx = self.telemetry.get("sandbox", "auto")
        
        # Only draw if there's enough room vertically (terminal lines > 30)
        out.append(f"\033[{hud_y};{x}H{tl}\033[K")
        out.append(f"\033[{hud_y+1};{x}H{v} \033[36m󰉖 \033[38;5;248mCWD:\033[0m \033[97m{cwd[:w-9]:<{w-9}}\033[0m{v}\033[K")
        out.append(f"\033[{hud_y+2};{x}H{v} \033[35m󰆧 \033[38;5;248mSBX:\033[0m \033[97m{sbx[:w-9]:<{w-9}}\033[0m{v}\033[K")
        
        files = self.telemetry.get("files", [])
        if files:
            file_str = f"{len(files)} files"
            out.append(f"\033[{hud_y+3};{x}H{v} \033[32m󰈔 \033[38;5;248mCTX:\033[0m \033[97m{file_str[:w-9]:<{w-9}}\033[0m{v}\033[K")
            out.append(f"\033[{hud_y+4};{x}H{bl}\033[K")
        else:
            out.append(f"\033[{hud_y+3};{x}H{bl}\033[K")

    # --- Vector Drawing Primitives ---
    def _rect(self, pixels, x, y, w, h, color):
        for i in range(max(0, int(y)), min(self.height, int(y+h))):
            for j in range(max(0, int(x)), min(self.width, int(x+w))):
                pixels[i][j] = color

    def _ellipse(self, pixels, cx, cy, rx, ry, color):
        for i in range(max(0, int(cy-ry)), min(self.height, int(cy+ry+1))):
            for j in range(max(0, int(cx-rx)), min(self.width, int(cx+rx+1))):
                if ((j-cx)/rx)**2 + ((i-cy)/ry)**2 <= 1.0:
                    pixels[i][j] = color
                    
    def _triangle(self, pixels, x1, y1, x2, y2, x3, y3, color):
        def sign(p1x, p1y, p2x, p2y, p3x, p3y):
            return (p1x - p3x) * (p2y - p3y) - (p2x - p3x) * (p1y - p3y)

        min_x = int(max(0, min(x1, x2, x3)))
        max_x = int(min(self.width - 1, max(x1, x2, x3)))
        min_y = int(max(0, min(y1, y2, y3)))
        max_y = int(min(self.height - 1, max(y1, y2, y3)))

        for i in range(min_y, max_y + 1):
            for j in range(min_x, max_x + 1):
                d1 = sign(j, i, x1, y1, x2, y2)
                d2 = sign(j, i, x2, y2, x3, y3)
                d3 = sign(j, i, x3, y3, x1, y1)
                has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
                has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
                if not (has_neg and has_pos):
                    # Bounds check
                    if 0 <= i < self.height and 0 <= j < self.width:
                        pixels[i][j] = color

    # --- Compositor ---
    def _render_scene(self, pixels):
        if self.state == "hidden":
            # Leaving pixels fully transparent will automatically erase the cat seamlessly!
            return

        C_BODY = (40, 42, 54)
        C_MID = (68, 71, 90)
        C_BELLY = (248, 248, 242)
        C_PINK = (255, 121, 198)
        C_EYE = (80, 250, 123)
        C_PUPIL = (0, 0, 0)
        C_ERR = (255, 85, 85)
        C_ZZZ = (189, 147, 249)
        C_DOT = (255, 184, 108)

        hx, hy = 21, 7
        bx, by = 18, 12
        tx, ty = 9, 13
        
        breathe = 0.0
        tail_angle = 0.0
        eye_state = "closed"
        dot_pos = None
        zzz_frame = None

        if self.state == "idle":
            breathe = math.sin(self.tick * 2) * 1.5
            hy += breathe * 0.4
            by -= breathe * 0.2
            eye_state = "closed"
            tail_angle = -180
            z_cycle = (self.tick % 4.0) / 4.0
            if z_cycle > 0.1:
                zx = 24 + z_cycle * 9
                zy = 4 - z_cycle * 6
                zzz_frame = (int(zx), int(zy))
                
        elif self.state == "wakeup":
            head_dy = -3 * min(1.0, self.tick * 4)
            hy += head_dy
            eye_state = "open"
            tail_angle = -180 + min(1.0, self.tick * 3) * 140
            
        elif self.state == "thinking":
            eye_state = "open"
            hy -= 3
            tail_angle = math.sin(self.tick * 6) * 20 - 40
            dot_pos = 21 + math.sin(self.tick * 3) * 12
            
        elif self.state == "response":
            eye_state = "happy"
            bounce = abs(math.sin(self.tick * 6)) * 4
            hy -= bounce
            by -= bounce * 0.5
            tail_angle = -20
            
        elif self.state == "error":
            eye_state = "wide"
            C_BODY = C_ERR
            hy -= 3
            tail_angle = -90

        # --- Draw Tail (Layer 1) ---
        for i in range(10):
            dist = i * 1.8
            rad = tail_angle * math.pi / 180.0
            tr = 3.0 - (i * 0.2)
            if self.state == "error":
                tr += 1.5
            px = tx + math.cos(rad) * (dist * 2.0)
            py = ty + math.sin(rad) * dist
            self._ellipse(pixels, px, py, int(tr*2), int(tr), C_MID)

        # --- Draw Body (Layer 2) ---
        breathe_rx = breathe * 1.2
        breathe_ry = breathe * 0.6
        self._ellipse(pixels, bx, by, 12 + breathe_rx, 6 + breathe_ry, C_BODY)
        self._ellipse(pixels, bx + 2.5, by + 1.2, 8 + breathe_rx, 3.5 + breathe_ry, C_BELLY)

        # --- Draw Head & Features (Layer 3) ---
        self._ellipse(pixels, hx, hy, 9, 4.5, C_BODY)
        
        # Ears 
        if self.state == "error":
            self._triangle(pixels, hx-8, hy-1, hx-4, hy-3, hx-2, hy-1, C_BODY)
            self._triangle(pixels, hx+8, hy-1, hx+4, hy-3, hx+2, hy-1, C_BODY)
        else:
            self._triangle(pixels, hx-7, hy-1, hx-3, hy-1.5, hx-6, hy-6, C_BODY)
            self._triangle(pixels, hx-6, hy-1.5, hx-3.5, hy-2, hx-5.5, hy-4.5, C_PINK)
            self._triangle(pixels, hx+7, hy-1, hx+3, hy-1.5, hx+6, hy-6, C_BODY)
            self._triangle(pixels, hx+6, hy-1.5, hx+3.5, hy-2, hx+5.5, hy-4.5, C_PINK)

        # Muzzle / cheeks
        self._ellipse(pixels, hx-2.5, hy+2, 3, 2, C_BELLY)
        self._ellipse(pixels, hx+2.5, hy+2, 3, 2, C_BELLY)
        self._ellipse(pixels, hx, hy+1, 1.5, 1, C_PINK)

        # Eyes & Expressions
        if eye_state == "closed":
            self._rect(pixels, hx-5, hy-1.5, 3, 1, C_PUPIL)
            self._rect(pixels, hx+3, hy-1.5, 3, 1, C_PUPIL)
        elif eye_state == "open":
            pupil_dx = 0
            if getattr(self, "cursor_ratio", None) is not None and self.state in ("idle", "wakeup"):
                # Track user shell cursor dynamically (-2 to +2 shifted from center)
                ratio = self.cursor_ratio
                pupil_dx = int(round(-2.0 + (ratio * 4.0)))
            elif dot_pos:
                if dot_pos > hx + 3: pupil_dx = 1
                elif dot_pos < hx - 3: pupil_dx = -1
                
            self._rect(pixels, hx-5, hy-2, 3, 2, C_EYE)
            self._rect(pixels, hx+3, hy-2, 3, 2, C_EYE)
            self._rect(pixels, hx-4 + pupil_dx, hy-1.5, 1, 1.5, C_PUPIL)
            self._rect(pixels, hx+4 + pupil_dx, hy-1.5, 1, 1.5, C_PUPIL)
        elif eye_state == "happy":
            self._rect(pixels, hx-5, hy-1, 3, 1, C_EYE)
            self._rect(pixels, hx+3, hy-1, 3, 1, C_EYE)
            self._rect(pixels, hx-5, hy-2, 1, 1, C_EYE)
            self._rect(pixels, hx-3, hy-2, 1, 1, C_EYE)
            self._rect(pixels, hx+3, hy-2, 1, 1, C_EYE)
            self._rect(pixels, hx+5, hy-2, 1, 1, C_EYE)
        elif eye_state == "wide":
            self._ellipse(pixels, hx-4, hy-1.5, 3, 2, C_BELLY)
            self._ellipse(pixels, hx+4, hy-1.5, 3, 2, C_BELLY)
            self._rect(pixels, hx-4.5, hy-2, 1.5, 1.5, C_PUPIL)
            self._rect(pixels, hx+3.5, hy-2, 1.5, 1.5, C_PUPIL)

        # --- FX overlay ---
        if dot_pos:
            self._ellipse(pixels, int(dot_pos), int(hy-6), 2, 1, C_DOT)

        if zzz_frame:
            zx, zy = zzz_frame
            self._rect(pixels, zx, zy, 3, 1, C_ZZZ)
            self._rect(pixels, zx+1, zy+1, 1, 1, C_ZZZ)
            self._rect(pixels, zx, zy+2, 3, 1, C_ZZZ)

_ENGINE = CatEngine()

def start_cat_ui():
    if not _ENGINE.running:
        _ENGINE.start()

def set_cat_state(new_state: str):
    _ENGINE.set_state(new_state)

def hide_cat():
    _ENGINE.is_hidden = True
    _ENGINE.set_state("hidden")

def show_cat():
    _ENGINE.is_hidden = False
    _ENGINE.set_state("idle")

