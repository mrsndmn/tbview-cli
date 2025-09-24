from tbview.viewer import TensorboardViewer


class Dummy:
    pass


def test_moving_average_simple_cases():
    dummy = Dummy()
    vals = [1, 2, 3, 4]
    assert TensorboardViewer._moving_average(dummy, vals, 0) == vals
    assert TensorboardViewer._moving_average(dummy, vals, 1) == vals
    # window 2 -> prefix mean over last 2 values
    assert TensorboardViewer._moving_average(dummy, vals, 2) == [1.0, 1.5, 2.5, 3.5]


def test_format_duration_formats_compactly():
    dummy = Dummy()
    assert TensorboardViewer._format_duration(dummy, 5) == "00:05"
    assert TensorboardViewer._format_duration(dummy, 60) == "01:00"
    assert TensorboardViewer._format_duration(dummy, 3661) == "1:01:01"


def test_compute_run_epoch_eta_and_speed_from_epoch_series():
    # Build a minimal self-like object with required attributes
    self_like = Dummy()
    self_like.records_by_run = {
        "runA": {
            "train/epoch": {0: 0.0, 10: 0.5, 20: 1.0},
        }
    }
    self_like.wall_times_by_run = {
        "runA": {
            "train/epoch": {0: 100.0, 10: 110.0, 20: 120.0},
        }
    }

    eta_speed = TensorboardViewer._compute_run_epoch_eta(self_like, "runA")
    assert eta_speed is not None
    eta, speed = eta_speed
    # When first epoch>=1 at t=120 and t0=100 -> eta 20s
    assert abs(eta - 20.0) < 1e-6
    # steps elapsed between first and idx_ge1: 20 - 0 over 20s => 1.0 steps/s
    assert speed is not None and abs(speed - 1.0) < 1e-6


