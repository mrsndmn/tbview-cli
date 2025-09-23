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
        self.smoothing_levels = [0, 10, 50, 100, 200]
        self.smoothing_index = 0
        self.smoothing_window = self.smoothing_levels[self.smoothing_index]
        self.x_axis_modes = ['step', 'relative', 'absolute']
        self.x_mode_index = 0
        self.ui = RatioHSplit(
            PlotextTile(self.plot, title='Plot', border_color=15),
            RatioVSplit(
                Text(" 1.Press arrow keys to locate coordinates.\n\n 2.Use number 1-9 or W/S to select tag.\n\n 3.Ctrl+C to quit.\n\n 4.Press 's' to toggle smoothing (0/10/50/100/200).\n\n 5.Press 'x' to toggle X axis (step/rel/abs).", color=15, title=' Tips', border_color=15),
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
        self._profile_enabled = False
        self._frame_count = 0
        self._last_fps_log = 0.0
        import os, time
        self._last_seen_mtime = os.path.getmtime(self.event_path)
        self._last_scan_ts = time.time()
        self.wall_times = {}
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
                    if value.tag not in self.wall_times:
                        self.wall_times[value.tag] = {}
                    self.records[value.tag][event.step] = value.simple_value
                    self.wall_times[value.tag][event.step] = getattr(event, 'wall_time', None)
            self._last_offset = end_off
        self._last_scan_size = current_size
        self._last_seen_mtime = os.path.getmtime(self.event_path)
        self._last_scan_ts = time.time()

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
            elif str(key).lower() == 's':
                self.smoothing_index = (self.smoothing_index + 1) % len(self.smoothing_levels)
                self.smoothing_window = self.smoothing_levels[self.smoothing_index]
                self.log(f'smoothing set to {self.smoothing_window}', INFO)
            elif str(key).lower() == 'x':
                self.x_mode_index = (self.x_mode_index + 1) % len(self.x_axis_modes)
                self.log(f"X axis set to {self.x_axis_modes[self.x_mode_index]}", INFO)

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
        # ensure ordered by step and compute last step for title
        if steps:
            sorted_steps = sorted(steps)
            values = [self.records[key][s] for s in sorted_steps]
            last_step = sorted_steps[-1]
        else:
            sorted_steps = steps
            last_step = None
        if self.smoothing_window and self.smoothing_window > 1:
            values = self._moving_average(values, self.smoothing_window)
        # choose X axis values with adaptive time formatting
        x_mode = self.x_axis_modes[self.x_mode_index]
        custom_ticks = None
        custom_labels = None
        xlabel = 'step'
        if x_mode == 'step' or not steps:
            x_vals = sorted_steps
            xlabel = 'step'
            if x_vals:
                tick_count = max(2, min(10, len(x_vals)))
                step_idx = max(1, len(x_vals) // tick_count)
                idxs = list(range(0, len(x_vals), step_idx))
                if idxs[-1] != len(x_vals) - 1:
                    idxs.append(len(x_vals) - 1)
                custom_ticks = [x_vals[i] for i in idxs]
                custom_labels = [str(x_vals[i]) for i in idxs]
        else:
            times = [self.wall_times.get(key, {}).get(s) for s in sorted_steps]
            if any(t is None for t in times):
                x_vals = sorted_steps
                xlabel = 'step'
                if x_vals:
                    tick_count = max(2, min(10, len(x_vals)))
                    step_idx = max(1, len(x_vals) // tick_count)
                    idxs = list(range(0, len(x_vals), step_idx))
                    if idxs[-1] != len(x_vals) - 1:
                        idxs.append(len(x_vals) - 1)
                    custom_ticks = [x_vals[i] for i in idxs]
                    custom_labels = [str(x_vals[i]) for i in idxs]
            else:
                if x_mode == 'absolute':
                    # Use epoch seconds for x, label ticks as HH:MM and include start day in xlabel
                    x_vals = times
                    from datetime import datetime
                    import time as _time
                    start_dt = datetime.fromtimestamp(times[0])
                    start_day = start_dt.strftime('%d/%m')
                    xlabel = f'time HH:MM (start {start_day})'
                    tick_count = max(2, min(10, len(x_vals)))
                    step_idx = max(1, len(x_vals) // tick_count)
                    idxs = list(range(0, len(x_vals), step_idx))
                    if idxs[-1] != len(x_vals) - 1:
                        idxs.append(len(x_vals) - 1)
                    custom_ticks = [x_vals[i] for i in idxs]
                    custom_labels = [_time.strftime('%H:%M', _time.localtime(x_vals[i])) for i in idxs]
                else:
                    # Relative time: adapt units to seconds/minutes/hours and set ticks
                    t0 = times[0]
                    rel = [t - t0 for t in times]
                    total = rel[-1] if rel else 0
                    if total < 60:
                        divisor = 1.0
                        xlabel = 'time since start (s)'
                        fmt = '{:.0f}'
                    elif total < 3600:
                        divisor = 60.0
                        xlabel = 'time since start (min)'
                        fmt = '{:.1f}'
                    else:
                        divisor = 3600.0
                        xlabel = 'time since start (h)'
                        fmt = '{:.1f}'
                    x_vals = [r / divisor for r in rel]
                    if x_vals:
                        tick_count = max(2, min(10, len(x_vals)))
                        step_idx = max(1, len(x_vals) // tick_count)
                        idxs = list(range(0, len(x_vals), step_idx))
                        if idxs[-1] != len(x_vals) - 1:
                            idxs.append(len(x_vals) - 1)
                        custom_ticks = [x_vals[i] for i in idxs]
                        custom_labels = [fmt.format(x_vals[i]) for i in idxs]
        plt.title(f"{key} (smooth={self.smoothing_window}, last_step={last_step})")
        plt.plot(x_vals, values)
        if custom_ticks is not None and custom_labels is not None:
            try:
                plt.xticks(custom_ticks, custom_labels)
            except Exception:
                pass
        plt.xfrequency(10)
        plt.xlabel(xlabel)
        plt.show()
        if self._profile_enabled:
            self.log(f'plot took {(time.perf_counter()-t0)*1000:.1f}ms', DEBUG)

    def _moving_average(self, values, window):
        if window <= 1 or not values:
            return values
        prefix = [0.0]
        for v in values:
            prefix.append(prefix[-1] + float(v))
        smoothed = []
        for i in range(len(values)):
            start = max(0, i - window + 1)
            total = prefix[i + 1] - prefix[start]
            count = i - start + 1
            smoothed.append(total / count)
        return smoothed

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
                    else:
                        # small sleep to avoid busy loop
                        time.sleep(0.05)

                    # Reload data every 15 seconds if file updated
                    try:
                        import os
                        now = time.time()
                        if now - self._last_scan_ts >= 15.0:
                            current_size = os.path.getsize(self.event_path)
                            current_mtime = os.path.getmtime(self.event_path)
                            if current_size != self._last_scan_size or current_mtime != self._last_seen_mtime:
                                self.scan_event()
                    except Exception as e:
                        self.log(f'failed to check file update: {e}', WARN)
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

