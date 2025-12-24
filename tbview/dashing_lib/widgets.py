from .dashing import Tile, TBox, Text
import io
from contextlib import redirect_stdout
from typing import Callable

class PlotextTile(Tile):
    def __init__(self, plot_fn: Callable[[TBox], None], *args, **kw):
        super(PlotextTile, self).__init__(**kw)
        self.plot_fn = plot_fn

    def _display(self, tbox, parent):
        tbox = self._draw_borders_and_title(tbox)
        st = self.plot_to_string(TBox(tbox.t, 0, 0, w=tbox.w-4, h=tbox.h-2 ))
        dx = 0
        for dx, line in enumerate(st.splitlines()):
            print(
                tbox.t.move(tbox.x + dx + 1, tbox.y + 2)
                + line
                + " " * (tbox.w - len(line) - 1)
            )
        dx += 2
        while dx < tbox.h:
            print(tbox.t.move(tbox.x + dx, tbox.y) + " " * tbox.w)
            dx += 1

    def plot_to_string(self, tbox):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.plot_fn(tbox)
        return buf.getvalue()



class SelectionTile(Text):
    def __init__(self, options, current=0, color=0, *args, **kw):
        super().__init__('', color, *args, **kw)
        self._current = current
        self._options = options
        self._scroll_offset = 0

    @property
    def current(self):
        return self._current
    
    @current.setter
    def current(self, c):
        try:
            c = int(c)
        except Exception:
            c = 0
        if not self._options:
            self._current = 0
            self._scroll_offset = 0
            return
        if c < 0:
            c = 0
        if c >= len(self._options):
            c = len(self._options) - 1
        self._current = c
    
    @property
    def options(self):
        return self._options
    
    @options.setter
    def options(self, options):
        self._options = options
        # Keep selection and scroll offset in a valid range.
        if not self._options:
            self._current = 0
            self._scroll_offset = 0
        else:
            if self._current < 0:
                self._current = 0
            if self._current >= len(self._options):
                self._current = len(self._options) - 1
            if self._scroll_offset < 0:
                self._scroll_offset = 0
            if self._scroll_offset >= len(self._options):
                self._scroll_offset = max(0, len(self._options) - 1)
    
    def _apply_options_to_text(self, tbox:TBox):
        t = tbox.t
        styled_options = [
            (t.on_white if i == self._current else t.white)(opt)
            for i,opt in enumerate(self._options)
        ]
        self.text = '\n'.join(styled_options)
    
    def _display(self, tbox, parent):
        # Render options without wrapping to avoid breaking ANSI sequences
        tbox = self._draw_borders_and_title(tbox)
        t = tbox.t
        # Ensure current selection is visible within the viewport.
        viewport_h = max(0, tbox.h)
        if self._options and viewport_h > 0:
            if self._current < self._scroll_offset:
                self._scroll_offset = self._current
            elif self._current >= self._scroll_offset + viewport_h:
                self._scroll_offset = self._current - viewport_h + 1
            max_offset = max(0, len(self._options) - viewport_h)
            if self._scroll_offset > max_offset:
                self._scroll_offset = max_offset

        dx = 0
        start = self._scroll_offset
        end = len(self._options)
        for i in range(start, end):
            if dx >= tbox.h:
                break
            opt = self._options[i]
            visible_text = opt[:tbox.w]
            styled = (t.on_white if i == self._current else t.white)(visible_text)
            print(
                t.move(tbox.x + dx, tbox.y)
                + styled
                + " " * (tbox.w - len(visible_text))
            )
            dx += 1
        while dx < tbox.h:
            print(t.move(tbox.x + dx, tbox.y) + " " * tbox.w)
            dx += 1