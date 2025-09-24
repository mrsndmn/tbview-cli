from tbview.cli import is_event_file, local_event_name, local_event_dir


def test_is_event_file_and_local_event_name():
    p = "/tmp/events.out.tfevents.1699999999.machine.tag"
    assert is_event_file(p) is True
    # local_event_name strips suffix after the first '.' following the prefix
    assert local_event_name(p).startswith("events.out.tfevents.")


def test_local_event_dir_current_dir_when_empty():
    assert local_event_dir("") == "."
    assert local_event_dir("subdir") == "subdir"


