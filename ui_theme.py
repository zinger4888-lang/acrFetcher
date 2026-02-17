import os


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)

def _clamp255(n: int) -> int:
    return 0 if n < 0 else (255 if n > 255 else n)


def _rgb_to_ansi256(r: int, g: int, b: int) -> int:
    # 6x6x6 cube (16..231) + grayscale (232..255)
    r = _clamp255(r)
    g = _clamp255(g)
    b = _clamp255(b)

    def _cube(v: int) -> int:
        return int(round(v / 51.0))

    ri, gi, bi = _cube(r), _cube(g), _cube(b)
    ri = 0 if ri < 0 else (5 if ri > 5 else ri)
    gi = 0 if gi < 0 else (5 if gi > 5 else gi)
    bi = 0 if bi < 0 else (5 if bi > 5 else bi)

    cube_code = 16 + (36 * ri) + (6 * gi) + bi
    cube_rgb = (ri * 51, gi * 51, bi * 51)

    avg = int(round((r + g + b) / 3.0))
    gray_index = int(round((avg - 8) / 10.0))
    gray_index = 0 if gray_index < 0 else (23 if gray_index > 23 else gray_index)
    gray_code = 232 + gray_index
    gray_level = 8 + gray_index * 10
    gray_rgb = (gray_level, gray_level, gray_level)

    def _dist(a: tuple[int, int, int], x: tuple[int, int, int]) -> int:
        return (a[0] - x[0]) ** 2 + (a[1] - x[1]) ** 2 + (a[2] - x[2]) ** 2

    return gray_code if _dist((r, g, b), gray_rgb) < _dist((r, g, b), cube_rgb) else cube_code


_ANSI16_RGB = [
    (0, 0, 0),       # 30
    (205, 49, 49),   # 31
    (13, 188, 121),  # 32
    (229, 229, 16),  # 33
    (36, 114, 200),  # 34
    (188, 63, 188),  # 35
    (17, 168, 205),  # 36
    (229, 229, 229), # 37
    (102, 102, 102), # 90
    (241, 76, 76),   # 91
    (35, 209, 139),  # 92
    (245, 245, 67),  # 93
    (59, 142, 234),  # 94
    (214, 112, 214), # 95
    (41, 184, 219),  # 96
    (255, 255, 255), # 97
]


def _rgb_to_ansi16_code(r: int, g: int, b: int) -> int:
    r = _clamp255(r)
    g = _clamp255(g)
    b = _clamp255(b)

    best_i = 0
    best_d = 10**18
    for i, (rr, gg, bb) in enumerate(_ANSI16_RGB):
        d = (r - rr) ** 2 + (g - gg) ** 2 + (b - bb) ** 2
        if d < best_d:
            best_d = d
            best_i = i
    return (30 + best_i) if best_i < 8 else (90 + (best_i - 8))


def _colors_enabled() -> bool:
    force = (os.getenv("ACRFETCHER_FORCE_COLOR") or "").strip().lower()
    if force in ("1", "true", "yes", "on"):
        return True
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("TERM", "").lower() == "dumb":
        return False
    return True


class Theme:
    def __init__(self) -> None:
        self.enabled = _colors_enabled()
        self.reset = "\033[0m" if self.enabled else ""
        self.dim_code = "\033[2m" if self.enabled else ""
        colorterm = (os.getenv("COLORTERM") or "").lower()
        term_prog = (os.getenv("TERM_PROGRAM") or "").lower()
        self.truecolor = ("truecolor" in colorterm) or ("24bit" in colorterm) or ("iterm" in term_prog)
        term = (os.getenv("TERM") or "").lower()
        self.supports_256 = self.truecolor or ("256color" in term)

        # Palette (cyberpunk, high contrast; no pastels)
        self.text = "#f2f6ff"         # cold near-white
        self.dim_color = "#7d869d"    # steel label gray
        self.border_color = "#2a3147" # deep slate

        self.accent_1 = "#00e5ff"     # electric cyan (links/active)
        self.accent_2 = "#ff2aa6"     # hot magenta (events/attention)
        self.secondary_color = "#a64dff"  # neon violet (secondary emphasis)
        self.monitor_color = "#00ffa8"    # neon mint (monitoring/idle live)

        self.success_color = "#7cff00"    # toxic green
        self.warn_color = "#ff9f1c"       # amber
        self.red = "#ff2e2e"              # error red

    def fg(self, color: str, text: str, *, dim: bool = False, bold: bool = False) -> str:
        if not self.enabled:
            return text
        r, g, b = _hex_to_rgb(color)
        codes: list[str] = []
        if dim:
            codes.append("2")
        if bold:
            codes.append("1")
        if self.truecolor:
            codes.append(f"38;2;{r};{g};{b}")
        elif self.supports_256:
            codes.append(f"38;5;{_rgb_to_ansi256(r, g, b)}")
        else:
            codes.append(str(_rgb_to_ansi16_code(r, g, b)))
        prefix = "\033[" + ";".join(codes) + "m"
        return f"{prefix}{text}{self.reset}"

    def _fg(self, color: str) -> str:
        if not self.enabled:
            return ""
        r, g, b = _hex_to_rgb(color)
        if self.truecolor:
            return f"\033[38;2;{r};{g};{b}m"
        if self.supports_256:
            return f"\033[38;5;{_rgb_to_ansi256(r, g, b)}m"
        return f"\033[{_rgb_to_ansi16_code(r, g, b)}m"

    def _wrap(self, color: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"{self._fg(color)}{text}{self.reset}"

    def dim(self, text: str) -> str:
        if not self.enabled:
            return text
        return f"{self.dim_code}{text}{self.reset}"

    def border(self, text: str, bright: bool = False) -> str:
        # "bright" is kept for backwards compatibility; border stays in border_color.
        _ = bright
        return self._wrap(self.border_color, text)

    def pink_text(self, text: str) -> str:
        return self._wrap(self.accent_2, text)

    def purple_text(self, text: str) -> str:
        # Secondary emphasis (e.g. command lists, ticket ids).
        return self._wrap(self.secondary_color, text)

    def cyan_text(self, text: str) -> str:
        return self._wrap(self.accent_1, text)

    def lime_text(self, text: str) -> str:
        return self._wrap(self.success_color, text)

    def amber_text(self, text: str) -> str:
        return self._wrap(self.warn_color, text)

    def red_text(self, text: str) -> str:
        return self._wrap(self.red, text)

    def gray_text(self, text: str) -> str:
        return self._wrap(self.dim_color, text)

    def white_text(self, text: str) -> str:
        return self._wrap(self.text, text)

    def noise(self, text: str) -> str:
        return self._wrap(self.dim_color, text)

    # Explicit semantic helpers
    def accent(self, text: str) -> str:
        return self._wrap(self.accent_2, text)

    def link(self, text: str) -> str:
        return self._wrap(self.accent_1, text)

    def success(self, text: str) -> str:
        return self._wrap(self.success_color, text)

    def warn(self, text: str) -> str:
        return self._wrap(self.warn_color, text)

    def error(self, text: str) -> str:
        return self._wrap(self.red, text)

    def monitor(self, text: str) -> str:
        return self._wrap(self.monitor_color, text)

    def text_color(self, text: str) -> str:
        return self._wrap(self.text, text)

    def dim_text(self, text: str) -> str:
        return self._wrap(self.dim_color, text)


theme = Theme()
