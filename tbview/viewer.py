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
        self._xlim_steps = None  # tuple (start_step, end_step) or None
        self._awaiting_xlim_input = False
        self._xlim_input_buffer = ''
        self._ylim = None  # tuple (ymin, ymax) or None
        self._awaiting_ylim_input = False
        self._ylim_input_buffer = ''
        self.ui = RatioHSplit(
            PlotextTile(self.plot, title='Plot', border_color=15),
            RatioVSplit(
                Text(" 1.Press arrow keys to locate coordinates.\n\n 2.Use number 1-9 or to select tag.\n\n 3.Press 'q' to go back to selection.\n\n 4.Ctrl+C to quit.\n\n 5.Press 's' to toggle smoothing (0/10/50/100/200).\n\n 6.Press 'm' to toggle X axis (step/rel/abs).\n\n 7.Press 'x' to set xlim in steps (start:end), ESC to cancel.\n\n 8.Press 'y' to set ylim (min:max), ESC to cancel.", color=15, title=' Tips', border_color=15),
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
        # Handle ylim interactive input mode
        if self._awaiting_ylim_input:
            if key.is_sequence:
                name = getattr(key, 'name', '')
                if name in ('KEY_BACKSPACE', 'KEY_DELETE'):
                    if self._ylim_input_buffer:
                        self._ylim_input_buffer = self._ylim_input_buffer[:-1]
                    self._render_ylim_prompt()
                elif name in ('KEY_ENTER',):
                    self._finalize_ylim_input()
                elif name in ('KEY_ESCAPE',):
                    self._awaiting_ylim_input = False
                    self._ylim_input_buffer = ''
                    self.log('ylim input cancelled', INFO)
                else:
                    pass
            else:
                ch = str(key)
                if ch in ('\n', '\r'):
                    self._finalize_ylim_input()
                elif ch.isprintable():
                    self._ylim_input_buffer += ch
                    self._render_ylim_prompt()
            return
        # Handle xlim interactive input mode
        if self._awaiting_xlim_input:
            # Accept digits, colon, minus, backspace, enter
            if key.is_sequence:
                name = getattr(key, 'name', '')
                if name in ('KEY_BACKSPACE', 'KEY_DELETE'):
                    if self._xlim_input_buffer:
                        self._xlim_input_buffer = self._xlim_input_buffer[:-1]
                    self._render_xlim_prompt()
                elif name in ('KEY_ENTER',):
                    self._finalize_xlim_input()
                elif name in ('KEY_ESCAPE',):
                    self._awaiting_xlim_input = False
                    self._xlim_input_buffer = ''
                    self.log('xlim input cancelled', INFO)
                else:
                    pass
            else:
                ch = str(key)
                if ch in ('\n', '\r'):
                    self._finalize_xlim_input()
                elif ch.isprintable():
                    self._xlim_input_buffer += ch
                    self._render_xlim_prompt()
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
            elif str(key).lower() == 'm':
                self.x_mode_index = (self.x_mode_index + 1) % len(self.x_axis_modes)
                self.log(f"X axis set to {self.x_axis_modes[self.x_mode_index]}", INFO)
            elif str(key).lower() == 'q':
                self._quit_and_reselect = True
            elif str(key).lower() == 'x':
                self._awaiting_xlim_input = True
                self._xlim_input_buffer = ''
                self.log("Enter xlim in steps as start:end (empty to clear). Press Enter to apply.", INFO)
                # Echo interactive prompt line
                self._render_xlim_prompt()
            elif str(key).lower() == 'y':
                self._awaiting_ylim_input = True
                self._ylim_input_buffer = ''
                self.log("Enter ylim as min:max (empty to clear). Press Enter to apply.", INFO)
                self._render_ylim_prompt()

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
        global_last_step = None
        global_xlim_min = None
        global_xlim_max = None
        global_ymin = None
        global_ymax = None
        global_xmin_step = None
        global_xmax_step = None
        for idx, (run_tag, path) in enumerate(zip(self.run_tags, self.event_paths)):
            per_run_records = self.records_by_run.get(run_tag, {})
            if key not in per_run_records:
                continue
            steps = list(per_run_records[key].keys())
            if not steps:
                continue
            sorted_steps = sorted(steps)
            raw_values = [per_run_records[key][s] for s in sorted_steps]
            values = list(raw_values)
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
            # Compute per-run ETA and speed (steps/s) using train/epoch, always show if available
            eta_str = None
            speed_str = None
            try:
                eta_sec, steps_per_sec = self._compute_run_epoch_eta(run_tag)
                # self.log(f'eta_sec: {eta_sec}, steps_per_sec: {steps_per_sec}', DEBUG)
                if eta_sec is not None:
                    eta_str = self._format_duration(eta_sec)
                if steps_per_sec is not None and steps_per_sec > 0:
                    if steps_per_sec > 10:
                        speed_str = f"{steps_per_sec:.1f} steps/s"
                    elif steps_per_sec > 1:
                        speed_str = f"{steps_per_sec:.2f} steps/s"
                    else:
                        speed_str = f"{steps_per_sec:.4f} step/s"
            except Exception:
                eta_str = None
                speed_str = None

            color = self.series_colors[idx % len(self.series_colors)]
            try:
                plot_label = str(run_tag)
                extra_parts = []
                if eta_str is not None:
                    extra_parts.append(f"eta {eta_str}")
                if speed_str is not None:
                    extra_parts.append(speed_str)
                if extra_parts:
                    plot_label = f"{plot_label} (" + ", ".join(extra_parts) + ")"
                plt.plot(x_vals, values, label=plot_label, color=color)
            except Exception:
                plt.plot(x_vals, values, color=color)
            any_series = True
            if sorted_steps:
                s_last = sorted_steps[-1]
                if global_last_step is None or s_last > global_last_step:
                    global_last_step = s_last
                # track global x range in step space
                s_first = sorted_steps[0]
                if global_xmin_step is None or s_first < global_xmin_step:
                    global_xmin_step = s_first
                if global_xmax_step is None or s_last > global_xmax_step:
                    global_xmax_step = s_last
            # track global y range for ylim validation
            if values:
                vmin = min(values)
                vmax = max(values)
                if global_ymin is None or vmin < global_ymin:
                    global_ymin = vmin
                if global_ymax is None or vmax > global_ymax:
                    global_ymax = vmax

            # Compute desired axis-space xlim from step-based limits without filtering
            if self._xlim_steps is not None:
                start_s, end_s = self._xlim_steps
                if x_mode == 'step' or not sorted_steps:
                    # Will set directly after loop
                    pass
                else:
                    # Map steps within [start:end] to current axis x values
                    selected_x = [x for s, x in zip(sorted_steps, x_vals) if (s >= start_s and s <= end_s)]
                    if selected_x:
                        run_min = min(selected_x)
                        run_max = max(selected_x)
                        if global_xlim_min is None or run_min < global_xlim_min:
                            global_xlim_min = run_min
                        if global_xlim_max is None or run_max > global_xlim_max:
                            global_xlim_max = run_max

        if not any_series:
            return

        last_step = global_last_step
        plt.title(f"{key} (smooth={self.smoothing_window}, last_step={last_step})")
        plt.xfrequency(10)
        plt.xlabel(xlabel)
        # Apply xlim after plotting
        if self._xlim_steps is not None:
            start_s, end_s = self._xlim_steps
            if x_mode == 'step':
                # clamp to available step range to avoid plotext errors
                if global_xmin_step is not None and global_xmax_step is not None:
                    x0 = min(start_s, end_s)
                    x1 = max(start_s, end_s)
                    cx0 = max(x0, global_xmin_step)
                    cx1 = min(x1, global_xmax_step)
                    if cx1 > cx0:
                        plt.xlim(cx0, cx1)
                    else:
                        self.log('requested xlim is outside data range; ignoring', WARN)
                        self._xlim_steps = None
                else:
                    self.log('no data range available for xlim; ignoring', WARN)
                    self._xlim_steps = None
            else:
                if global_xlim_min is not None and global_xlim_max is not None:
                    if global_xlim_max > global_xlim_min:
                        try:
                            plt.xlim(global_xlim_min, global_xlim_max)
                        except Exception as e:
                            self.log(f'failed to set xlim for {x_mode}: {global_xlim_min} {global_xlim_max}: {e}', WARN)
                    else:
                        self.log('computed xlim has non-positive width; ignoring', WARN)
                else:
                    # No points within requested range in time-based x axis; clear invalid selection
                    self.log('requested xlim selects no points in current x mode; clearing', WARN)
                    self._xlim_steps = None
        # Apply ylim after plotting
        if self._ylim is not None:
            y_min, y_max = self._ylim
            if global_ymin is not None and global_ymax is not None:
                a = min(y_min, y_max)
                b = max(y_min, y_max)
                cy0 = max(a, global_ymin)
                cy1 = min(b, global_ymax)
                if cy1 > cy0:
                    plt.ylim(cy0, cy1)
                else:
                    self.log('requested ylim is outside data range; ignoring', WARN)
                    self._ylim = None
            else:
                self.log('no data range available for ylim; ignoring', WARN)
                self._ylim = None
        # Safeguard rendering to avoid crashing the UI on plotting errors
        try:
            plt.show()
        except Exception as e:
            self.log(f'plot rendering failed: {e}', ERROR)
            # Clear potentially invalid limits to recover next frame
            self._xlim_steps = None
            self._ylim = None
        if self._profile_enabled:
            self.log(f'plot took {(time.perf_counter()-t0)*1000:.1f}ms', DEBUG)

    def _finalize_xlim_input(self):
        raw = (self._xlim_input_buffer or '').strip()
        self._awaiting_xlim_input = False
        self._xlim_input_buffer = ''
        if raw == '':
            self._xlim_steps = None
            self.log('xlim cleared', INFO)
            return
        try:
            if ':' in raw:
                start_s, end_s = raw.split(':', 1)
                start_v = int(start_s.strip())
                end_v = int(end_s.strip())
            else:
                start_v = 0
                end_v = int(raw)
            if start_v > end_v:
                start_v, end_v = end_v, start_v
            # Validate against available step range for currently selected tag
            selected_tag = self._get_selected_tag()
            gmin, gmax = self._get_global_step_range_for_tag(selected_tag)
            if gmin is None or gmax is None:
                self._xlim_steps = None
                self.log('no data available to apply xlim; ignoring', WARN)
                return
            # Clamp to data range
            cx0 = max(start_v, gmin)
            cx1 = min(end_v, gmax)
            if cx1 <= cx0:
                self._xlim_steps = None
                self.log('requested xlim is outside data range; ignoring', WARN)
                return
            self._xlim_steps = (cx0, cx1)
            if (cx0, cx1) != (start_v, end_v):
                self.log(f'clamped xlim (steps) to {cx0}:{cx1}', INFO)
            else:
                self.log(f'set xlim (steps) to {cx0}:{cx1}', INFO)
        except Exception as e:
            self.log(f'failed to parse xlim: {e}', WARN)

    def _render_xlim_prompt(self):
        self.logger.replace_last(self.term.white(f"{INFO} Enter xlim in steps as start:end (ESC to cancel): {self._xlim_input_buffer}"))

    def _finalize_ylim_input(self):
        raw = (self._ylim_input_buffer or '').strip()
        self._awaiting_ylim_input = False
        self._ylim_input_buffer = ''
        if raw == '':
            self._ylim = None
            self.log('ylim cleared', INFO)
            return
        try:
            if ':' in raw:
                start_s, end_s = raw.split(':', 1)
                y_min = float(start_s.strip())
                y_max = float(end_s.strip())
            else:
                y_min = 0.0
                y_max = float(raw)
            if y_min > y_max:
                y_min, y_max = y_max, y_min
            self._ylim = (y_min, y_max)
            self.log(f'set ylim to {self._ylim[0]}:{self._ylim[1]}', INFO)
        except Exception as e:
            self.log(f'failed to parse ylim: {e}', WARN)

    def _render_ylim_prompt(self):
        self.logger.replace_last(self.term.white(f"{INFO} Enter ylim as min:max (ESC to cancel): {self._ylim_input_buffer}"))

    def _get_selected_tag(self):
        """Return the currently selected tag name or None if unavailable."""
        # Collect union of tags across runs
        all_tags = OrderedDict()
        for run_tag in self.run_tags:
            for t in self.records_by_run.get(run_tag, {}):
                all_tags.setdefault(t, None)
        keys = list(all_tags.keys())
        if not keys:
            return None
        safe_idx = max(0, min(self.tag_selector.current, len(keys)-1))
        return keys[safe_idx]

    def _get_global_step_range_for_tag(self, tag):
        """Compute global min/max step across runs for the given tag.

        Returns (min_step, max_step) or (None, None) if no data.
        """
        if tag is None:
            return None, None
        global_xmin_step = None
        global_xmax_step = None
        for run_tag in self.run_tags:
            per_run_records = self.records_by_run.get(run_tag, {})
            if tag not in per_run_records:
                continue
            steps = list(per_run_records[tag].keys())
            if not steps:
                continue
            s_first = min(steps)
            s_last = max(steps)
            if global_xmin_step is None or s_first < global_xmin_step:
                global_xmin_step = s_first
            if global_xmax_step is None or s_last > global_xmax_step:
                global_xmax_step = s_last
        return global_xmin_step, global_xmax_step

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

    def _format_duration(self, seconds):
        try:
            secs = max(0, int(round(seconds)))
        except Exception as e:
            self.log(f'failed to format duration: {e}', WARN)
            return "?"
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        else:
            return f"{m:02d}:{s:02d}"

    def _compute_run_epoch_eta(self, run_tag):
        per_run_records = self.records_by_run.get(run_tag, {})
        if 'train/epoch' not in per_run_records:
            return None
        epoch_series = per_run_records['train/epoch']
        if not epoch_series:
            return None
        steps = sorted(epoch_series.keys())
        values = [epoch_series[s] for s in steps]
        times_map = self.wall_times_by_run.get(run_tag, {}).get('train/epoch', {})
        times_abs = [times_map.get(s) for s in steps]
        if not times_abs or times_abs[0] is None:
            return None
        t0_abs = times_abs[0]
        # Find first index where epoch >= 1
        idx_ge1 = None
        for i, (v, t) in enumerate(zip(values, times_abs)):
            try:
                if v is not None and float(v) >= 1.0 and t is not None:
                    idx_ge1 = i
                    break
            except Exception as e:
                self.log(f'failed to compute run epoch eta: {e}', WARN)
                continue
        if idx_ge1 is not None:
            eta = max(0.0, float(times_abs[idx_ge1] - t0_abs))
            # Compute speed: steps per second using last observed step and time window
            steps_elapsed = float(steps[idx_ge1] - steps[0]) if len(steps) > 0 else 0.0
            time_elapsed = float(times_abs[idx_ge1] - t0_abs)
            speed = (steps_elapsed / time_elapsed) if time_elapsed > 0 else None
            return eta, speed
        # Otherwise use last valid point for projection
        last_idx = None
        for i in range(len(values) - 1, -1, -1):
            v = values[i]
            t = times_abs[i]
            try:
                if v is not None and float(v) > 0 and t is not None:
                    last_idx = i
                    break
            except Exception as e:
                self.log(f'failed to compute run epoch eta: {e}', WARN)
                continue
        if last_idx is None:
            return None
        frac = float(values[last_idx])
        t_rel = float(times_abs[last_idx] - t0_abs)
        # Compute speed using steps and duration till last_idx
        steps_elapsed = float(steps[last_idx] - steps[0]) if len(steps) > 0 else 0.0
        time_elapsed = float(times_abs[last_idx] - t0_abs)
        speed = (steps_elapsed / time_elapsed) if time_elapsed > 0 else None
        if frac <= 0:
            return None
        eta = max(0.0, t_rel * (1.0 / frac) - time_elapsed)
        return eta, speed

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

