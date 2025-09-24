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
        # Support single or multiple runs
        if isinstance(event_path, (list, tuple)):
            self.event_paths = list(event_path)
            self.run_tags = list(event_tag) if isinstance(event_tag, (list, tuple)) else [str(event_tag)]
        else:
            self.event_paths = [event_path]
            self.run_tags = [event_tag]
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
        self.series_colors = ['red', 'green', 'yellow', 'blue', 'magenta', 'cyan']
        self.ui = RatioHSplit(
            PlotextTile(self.plot, title='Plot', border_color=15),
            RatioVSplit(
                Text(" 1.Press arrow keys to locate coordinates.\n\n 2.Use number 1-9 or W/S to select tag.\n\n 3.Press 'q' to go back to selection.\n\n 4.Ctrl+C to quit.\n\n 5.Press 's' to toggle smoothing (0/10/50/100/200).\n\n 6.Press 'x' to toggle X axis (step/rel/abs).", color=15, title=' Tips', border_color=15),
                self.tag_selector,
                self.logger,
                ratios=(2, 4, 2),
                rest_pad_to=1,
            ),
            ratios=(4, 1) if self.term.width > 100 else (3, 1),
            rest_pad_to=1
        )

        # Per-run data structures
        self.records_by_run = {tag: OrderedDict() for tag in self.run_tags}
        self.wall_times_by_run = {tag: {} for tag in self.run_tags}
        self._last_offset_by_run = {tag: 0 for tag in self.run_tags}
        self._last_scan_size_by_run = {tag: 0 for tag in self.run_tags}
        self._profile_enabled = False
        self._frame_count = 0
        self._last_fps_log = 0.0
        import os, time
        self._last_seen_mtime_by_run = {}
        for p, tag in zip(self.event_paths, self.run_tags):
            try:
                self._last_seen_mtime_by_run[tag] = os.path.getmtime(p)
            except Exception:
                self._last_seen_mtime_by_run[tag] = 0.0
        self._last_scan_ts = time.time()
        self._quit_and_reselect = False
        self.scan_events(initial=True)


    def scan_events(self, initial=False):
        import os, time
        start_ts = time.perf_counter()
        for path, run_tag in zip(self.event_paths, self.run_tags):
            try:
                current_size = os.path.getsize(path)
            except Exception:
                continue
            # Skip scan if no growth
            if not initial and current_size == self._last_scan_size_by_run.get(run_tag, 0):
                continue
            # Incremental read per run
            for event, end_off in read_records_from_offset(
                path,
                self._last_offset_by_run.get(run_tag, 0),
                warn=lambda msg: self.log(msg, WARN)
            ):
                summary = event.summary
                for value in summary.value:
                    if value.HasField('simple_value'):
                        per_run_records = self.records_by_run[run_tag]
                        per_run_times = self.wall_times_by_run[run_tag]
                        if value.tag not in per_run_records:
                            per_run_records[value.tag] = {}
                        if value.tag not in per_run_times:
                            per_run_times[value.tag] = {}
                        per_run_records[value.tag][event.step] = value.simple_value
                        per_run_times[value.tag][event.step] = getattr(event, 'wall_time', None)
                self._last_offset_by_run[run_tag] = end_off
            self._last_scan_size_by_run[run_tag] = current_size
            try:
                self._last_seen_mtime_by_run[run_tag] = os.path.getmtime(path)
            except Exception:
                pass
            self._last_scan_ts = time.time()

        # Update tag options as union across runs
        all_tags = OrderedDict()
        for run_tag in self.run_tags:
            for t in self.records_by_run.get(run_tag, {}):
                all_tags.setdefault(t, None)
        self.tag_selector.options = [
            f'[{i+1}] {root_tag} '
            for i, root_tag in enumerate(all_tags.keys())
        ]
        if self._profile_enabled:
            self.log(f'scan_events took {(time.perf_counter()-start_ts)*1000:.1f}ms', DEBUG)

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
            elif str(key).lower() == 'q':
                self._quit_and_reselect = True

    def log(self, msg, level=''):
        self.logger.append(self.term.white(f'{level} {msg}'))

    def plot(self, tbox):
        import time
        t0 = time.perf_counter()
        plt.theme('clear')
        plt.cld()
        plt.plot_size(tbox.w, tbox.h)
        # Collect union of tags across runs for selection
        all_tags = OrderedDict()
        for run_tag in self.run_tags:
            for t in self.records_by_run.get(run_tag, {}):
                all_tags.setdefault(t, None)
        keys = list(all_tags.keys())
        if not keys:
            return
        safe_idx = max(0, min(self.tag_selector.current, len(keys)-1))
        key = keys[safe_idx]
        x_mode = self.x_axis_modes[self.x_mode_index]

        # Build and plot series for each run that has this tag
        last_step = None
        any_series = False
        xlabel = 'step'
        custom_ticks = None
        custom_labels = None
        for idx, (run_tag, path) in enumerate(zip(self.run_tags, self.event_paths)):
            per_run_records = self.records_by_run.get(run_tag, {})
            if key not in per_run_records:
                continue
            steps = list(per_run_records[key].keys())
            if not steps:
                continue
            sorted_steps = sorted(steps)
            values = [per_run_records[key][s] for s in sorted_steps]
            if self.smoothing_window and self.smoothing_window > 1:
                values = self._moving_average(values, self.smoothing_window)
            # derive x values per series
            if x_mode == 'step' or not steps:
                x_vals = sorted_steps
                xlabel = 'step'
            else:
                times = [self.wall_times_by_run.get(run_tag, {}).get(key, {}).get(s) for s in sorted_steps]
                if any(t is None for t in times):
                    x_vals = sorted_steps
                    xlabel = 'step'
                else:
                    if x_mode == 'absolute':
                        x_vals = times
                        from datetime import datetime
                        import time as _time
                        start_dt = datetime.fromtimestamp(times[0])
                        start_day = start_dt.strftime('%d/%m')
                        xlabel = f'time HH:MM (start {start_day})'
                    else:
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
            color = self.series_colors[idx % len(self.series_colors)]
            try:
                plt.plot(x_vals, values, label=str(run_tag), color=color)
            except Exception:
                plt.plot(x_vals, values, color=color)
            any_series = True
            if sorted_steps:
                last_step = sorted_steps[-1]

        if not any_series:
            return

        plt.title(f"{key} (smooth={self.smoothing_window}, last_step={last_step})")
        try:
            plt.legend(True)
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
        if len(self.run_tags) == 1:
            self.log(f'current run: {self.run_tags[0]}', INFO)
        else:
            self.log(f'current runs: {", ".join(self.run_tags)}', INFO)
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
                        if self._quit_and_reselect:
                            return True
                    else:
                        # small sleep to avoid busy loop
                        time.sleep(0.05)

                    # Reload data every 15 seconds if file updated
                    try:
                        import os
                        now = time.time()
                        if now - self._last_scan_ts >= 15.0:
                            needs_scan = False
                            for path, run_tag in zip(self.event_paths, self.run_tags):
                                try:
                                    current_size = os.path.getsize(path)
                                    current_mtime = os.path.getmtime(path)
                                except Exception:
                                    continue
                                if (current_size != self._last_scan_size_by_run.get(run_tag, 0)
                                    or current_mtime != self._last_seen_mtime_by_run.get(run_tag, 0)):
                                    needs_scan = True
                                    break
                            if needs_scan:
                                self.scan_events()
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
            return False
        return False

