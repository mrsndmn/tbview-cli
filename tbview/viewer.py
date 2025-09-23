from tbview.dashing_lib.layout import RatioHSplit, RatioVSplit
from tbview.dashing_lib.widgets import PlotextTile, SelectionTile
from tbview.dashing_lib import *
import plotext as plt
from time import sleep
import blessed
from tbview.parser import read_records, read_records_from_offset
from collections import OrderedDict

ERROR = '[ERROR]'
WARN = '[WARN]'
INFO = '[INFO]'
DEBUG = '[DEBUG]'

class TensorboardViewer:
    def __init__(self, event_path, event_tag) -> None:
        self.event_path = event_path
        self.event_tag = event_tag
        self.term = blessed.Terminal()
        self.logger = Log(title=' Log/Err', border_color=15)
        self.tag_selector = SelectionTile(
                    options= [],
                    current=0,
                    title=' Tags List', 
                    border_color=15,
                )
        self.ui = RatioHSplit(
            PlotextTile(self.plot, title='Plot', border_color=15),
            RatioVSplit(
                Text(" 1.Press arrow keys to locate coordinates.\n\n 2.Use number 1-9 or W/S to select tag.\n\n 3.Ctrl+C to quit.", color=15, title=' Tips', border_color=15),
                self.tag_selector,
                self.logger,
                ratios=(2, 4, 2),
                rest_pad_to=1,
            ),
            ratios=(4, 1) if self.term.width > 100 else (3, 1),
            rest_pad_to=1
        )

        self.records = OrderedDict()
        self._last_offset = 0
        self._last_scan_size = 0
        self._profile_enabled = True
        self._frame_count = 0
        self._last_fps_log = 0.0
        self.scan_event(initial=True)

    
    def scan_event(self, initial=False):
        import os, time
        start_ts = time.perf_counter()
        current_size = os.path.getsize(self.event_path)
        # Skip scan if no growth
        if not initial and current_size == self._last_scan_size:
            return
        # Incremental read
        for event, end_off in read_records_from_offset(self.event_path, self._last_offset):
            summary = event.summary
            for value in summary.value:
                if value.HasField('simple_value'):
                    # print(value.tag, value.simple_value, event.step)
                    if value.tag not in self.records:
                        self.records[value.tag] = {}
                    self.records[value.tag][event.step] = value.simple_value
            self._last_offset = end_off
        self._last_scan_size = current_size
        
        self.tag_selector.options = [
            f'[{i+1}] {root_tag} '
            for i, root_tag in enumerate(self.records)
        ]
        if self._profile_enabled:
            self.log(f'scan_event took {(time.perf_counter()-start_ts)*1000:.1f}ms', DEBUG)

    def handle_input(self, key):
        if key is None:
            return
        if key.is_sequence:
            pass 
        else: 
            if key.isdigit():
                digit = int(key)  
                if digit > 0 and digit <= len(self.tag_selector.options):
                    self.tag_selector.current = digit - 1

    def log(self, msg, level=''):
        self.logger.append(self.term.white(f'{level} {msg}'))

    def plot(self, tbox):
        import time
        t0 = time.perf_counter()
        plt.theme('clear')
        plt.cld()
        plt.plot_size(tbox.w, tbox.h)
        keys = list(self.records.keys())
        if not keys:
            return
        safe_idx = max(0, min(self.tag_selector.current, len(keys)-1))
        key = keys[safe_idx]
        steps = list(self.records[key].keys())
        values = list(self.records[key].values())
        plt.title(key)
        plt.plot(steps, values)
        plt.xfrequency(10)
        plt.xlabel('step')
        plt.show()
        if self._profile_enabled:
            self.log(f'plot took {(time.perf_counter()-t0)*1000:.1f}ms', DEBUG)

    def run(self):
        term = self.term
        ui = self.ui
        self.log('tbview-cli started.', INFO)
        self.log(f'current run: {self.event_tag}', INFO)
        try:
            with term.fullscreen(), term.cbreak(), term.hidden_cursor():
                while True:
                    import time
                    frame_start = time.perf_counter()
                    ui.ratios = (4, 1) if term.width > 100 else (3, 1)
                    ui.display()
                    key = term.inkey(timeout=0.05)
                    if key:
                        self.handle_input(key)
                        # do not rescan on every key; rescan if file grew
                        self.scan_event()
                    else:
                        # throttled idle scan ~2fps
                        time.sleep(0.4)
                        self.scan_event()
                    self._frame_count += 1
                    if self._profile_enabled:
                        dt = time.perf_counter() - frame_start
                        if dt > 0:
                            fps = 1.0 / dt
                            if time.perf_counter() - self._last_fps_log > 2.0:
                                self.log(f'frame {self._frame_count} dt {dt*1000:.1f}ms ({fps:.1f} fps)', DEBUG)
                                self._last_fps_log = time.perf_counter()
        except KeyboardInterrupt:
            print('exit.')

