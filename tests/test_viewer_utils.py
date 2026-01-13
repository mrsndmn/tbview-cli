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


def test_sample_data_returns_original_when_below_threshold():
    dummy = Dummy()
    x = list(range(500))
    y = [i * 2 for i in range(500)]
    x_sampled, y_sampled = TensorboardViewer._sample_data(dummy, x, y, max_points=1000)
    assert x_sampled == x
    assert y_sampled == y


def test_sample_data_reduces_points_when_above_threshold():
    dummy = Dummy()
    x = list(range(5000))
    y = [i * 2 for i in range(5000)]
    x_sampled, y_sampled = TensorboardViewer._sample_data(dummy, x, y, max_points=1000)
    # Should reduce to approximately max_points
    assert len(x_sampled) <= 1000
    assert len(y_sampled) <= 1000
    assert len(x_sampled) == len(y_sampled)
    # Should preserve first and last points
    assert x_sampled[0] == x[0]
    assert x_sampled[-1] == x[-1]
    assert y_sampled[0] == y[0]
    assert y_sampled[-1] == y[-1]


def test_sample_data_preserves_correspondence():
    dummy = Dummy()
    x = list(range(3000))
    y = [i * 3 + 5 for i in range(3000)]
    x_sampled, y_sampled = TensorboardViewer._sample_data(dummy, x, y, max_points=500)
    # Check that x and y values still correspond (y = x * 3 + 5)
    for xs, ys in zip(x_sampled, y_sampled):
        assert ys == xs * 3 + 5


