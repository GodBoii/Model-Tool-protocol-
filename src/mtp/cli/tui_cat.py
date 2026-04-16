import sys
import time
import math
import threading
import shutil

class CatEngine:
    def __init__(self):
        self.width = 32
        self.height = 20
        self.state = "idle"
        self.tick = 0.0
        self.running = False
        self.thread = None
        self._lock = threading.Lock()

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
        if self.state != new_state:
            if new_state in ("wakeup", "response", "error"):
                self.tick = 0.0
            self.state = new_state

    def _loop(self):
        fps = 12
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
        # Prevent drawing if terminal is too small
        term_size = shutil.get_terminal_size()
        if term_size.columns < 60 or term_size.lines < 20:
            return

        pixels = [[None for _ in range(self.width)] for _ in range(self.height)]
        self._render_scene(pixels)

        out = ["\0337"]  # save cursor
        start_y = 2
        start_x = term_size.columns - self.width - 2

        for y in range(0, self.height, 2):
            out.append(f"\033[{start_y + y//2};{start_x}H")  # move to line
            for x in range(self.width):
                top = pixels[y][x]
                bot = pixels[y+1][x] if y+1 < self.height else None
                
                if top is None and bot is None:
                    out.append("\033[0m\033[49m ")
                elif bot is None:
                    out.append(f"\033[0m\033[38;2;{top[0]};{top[1]};{top[2]}m\033[49m\u2580")
                elif top is None:
                    out.append(f"\033[0m\033[38;2;{bot[0]};{bot[1]};{bot[2]}m\033[49m\u2584")
                else:
                    out.append(f"\033[0m\033[38;2;{top[0]};{top[1]};{top[2]}m\033[48;2;{bot[0]};{bot[1]};{bot[2]}m\u2580")
            out.append("\033[0m")
            
        out.append("\0338")  # restore cursor
        # write atomically
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    # --- Drawing Primitives ---
    def _rect(self, pixels, x, y, w, h, color):
        for i in range(max(0, int(y)), min(self.height, int(y+h))):
            for j in range(max(0, int(x)), min(self.width, int(x+w))):
                pixels[i][j] = color

    def _circle(self, pixels, cx, cy, r, color):
        for i in range(max(0, int(cy-r)), min(self.height, int(cy+r+1))):
            for j in range(max(0, int(cx-r)), min(self.width, int(cx+r+1))):
                if (j-cx)**2 + (i-cy)**2 <= r*r:
                    pixels[i][j] = color
                    
    def _ellipse(self, pixels, cx, cy, rx, ry, color):
        for i in range(max(0, int(cy-ry)), min(self.height, int(cy+ry+1))):
            for j in range(max(0, int(cx-rx)), min(self.width, int(cx+rx+1))):
                if ((j-cx)/rx)**2 + ((i-cy)/ry)**2 <= 1.0:
                    pixels[i][j] = color

    # --- Compositor ---
    def _render_scene(self, pixels):
        C_BODY = (40, 42, 54)
        C_MID = (68, 71, 90)
        C_BELLY = (248, 248, 242)
        C_PINK = (255, 121, 198)
        C_EYE = (80, 250, 123)
        C_PUPIL = (0, 0, 0)
        C_ERR = (255, 85, 85)
        C_ZZZ = (189, 147, 249)
        C_DOT = (255, 184, 108)
        
        hx, hy = 18, 9   # Head center
        bx, by = 14, 14  # Body center
        tx, ty = 6, 17   # Tail base
        
        breathe = 0.0
        tail_angle = 0.0
        head_dy = 0.0
        eye_state = "closed"
        dot_pos = None
        zzz_frame = None

        if self.state == "idle":
            breathe = math.sin(self.tick * 2) * 1.5
            hy += breathe * 0.4
            by -= breathe * 0.4
            eye_state = "closed"
            tail_angle = -180  # wrapped around
            z_cycle = (self.tick % 4.0) / 4.0
            if z_cycle > 0.1:
                zzz_frame = (16 + z_cycle*6, 6 - z_cycle*10)
                
        elif self.state == "wakeup":
            head_dy = -3 * min(1.0, self.tick * 4)
            hy += head_dy
            eye_state = "open"
            # animate tail uncurling and flicking
            tail_angle = -180 + min(1.0, self.tick * 3) * 135
            
        elif self.state == "thinking":
            eye_state = "open"
            hy -= 3
            tail_angle = math.sin(self.tick * 5) * 20 - 45
            dot_pos = 18 + math.sin(self.tick * 3) * 6
            
        elif self.state == "response":
            eye_state = "happy"
            bounce = abs(math.sin(self.tick * 6)) * 3
            hy -= bounce
            by -= bounce
            tail_angle = -30
            
        elif self.state == "error":
            eye_state = "wide"
            C_BODY = C_ERR
            hy -= 4
            tail_angle = -90

        # --- Draw Tail ---
        for i in range(8):
            dist = i * 2.0
            rad = tail_angle * math.pi / 180.0
            tr = 3.0 - (i * 0.2)
            if self.state == "error":
                tr += 1.5  # Poofed up
            px = tx + math.cos(rad) * dist
            py = ty + math.sin(rad) * dist
            self._circle(pixels, px, py, tr, C_MID)

        # --- Draw Body ---
        self._ellipse(pixels, bx, by, 9, 5 + breathe, C_BODY)
        self._ellipse(pixels, bx + 2, by + 1, 6, 3 + breathe * 0.7, C_BELLY)

        # --- Draw Head & Features ---
        self._circle(pixels, hx, hy, 5, C_BODY)
        
        if self.state == "error":
            # flattened back ears
            self._circle(pixels, hx-4, hy-2, 2, C_BODY)
            self._circle(pixels, hx+4, hy-2, 2, C_BODY)
        else:
            # perked up ears
            self._circle(pixels, hx-4, hy-4, 2.5, C_BODY)
            self._circle(pixels, hx+4, hy-4, 2.5, C_BODY)
            self._circle(pixels, hx-4, hy-3.5, 1.2, C_PINK)
            self._circle(pixels, hx+4, hy-3.5, 1.2, C_PINK)

        # Muzzle
        self._ellipse(pixels, hx-1.5, hy+1.5, 2, 1.5, C_BELLY)
        self._ellipse(pixels, hx+1.5, hy+1.5, 2, 1.5, C_BELLY)
        self._rect(pixels, hx-0.5, hy+0.5, 2, 1, C_PINK)

        # Eyes
        if eye_state == "closed":
            self._rect(pixels, hx-3, hy-1, 2, 1, C_PUPIL)
            self._rect(pixels, hx+2, hy-1, 2, 1, C_PUPIL)
        elif eye_state == "open":
            dx = 0
            if dot_pos:
                dx = 1 if dot_pos > hx else (-1 if dot_pos < hx else 0)
            self._rect(pixels, hx-3, hy-2, 2, 2, C_EYE)
            self._rect(pixels, hx+2, hy-2, 2, 2, C_EYE)
            self._rect(pixels, hx-3+dx, hy-1, 1, 1, C_PUPIL)
            self._rect(pixels, hx+2+dx, hy-1, 1, 1, C_PUPIL)
        elif eye_state == "happy":
            self._rect(pixels, hx-3, hy-2, 2, 1, C_EYE)
            self._rect(pixels, hx+2, hy-2, 2, 1, C_EYE)
            self._rect(pixels, hx-3, hy-1, 1, 1, C_EYE)
            self._rect(pixels, hx+3, hy-1, 1, 1, C_EYE)
        elif eye_state == "wide":
            self._circle(pixels, hx-2.5, hy-1.5, 1.5, C_BELLY)
            self._circle(pixels, hx+2.5, hy-1.5, 1.5, C_BELLY)
            self._rect(pixels, hx-3, hy-2, 1, 1, C_PUPIL)
            self._rect(pixels, hx+2, hy-2, 1, 1, C_PUPIL)

        # --- FX ---
        if dot_pos:
            self._circle(pixels, dot_pos, hy-6, 1, C_DOT)

        if zzz_frame:
            zx, zy = zzz_frame
            self._rect(pixels, zx, zy, 2, 1, C_ZZZ)
            self._rect(pixels, zx+1, zy+1, 1, 1, C_ZZZ)
            self._rect(pixels, zx, zy+2, 2, 1, C_ZZZ)

_ENGINE = CatEngine()

def start_cat_ui():
    _ENGINE.start()

def set_cat_state(new_state: str):
    _ENGINE.set_state(new_state)

