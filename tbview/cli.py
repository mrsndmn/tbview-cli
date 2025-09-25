import argparse
import os
import sys
import inquirer
from tbview.viewer import TensorboardViewer
from tbview.parser import read_records

def check_file_or_directory(path):
    if not os.path.exists(path):
        raise argparse.ArgumentTypeError(f"{path} is not a valid file or directory")
    return path

def is_event_file(path:str):
    return os.path.basename(path).startswith('events.out.tfevents')

def local_event_name(path):
    base = os.path.basename(path)
    base = base.replace('events.out.tfevents.', '')
    return 'events.out.tfevents.' + base[:base.index('.')]

def local_event_dir(path):
    dir = os.path.basename(path)
    if dir == '':
        dir = '.'
    return dir

def run_main(args):
    path = os.path.abspath(args.path)

    if os.path.isfile(path):
        if is_event_file(path):
            target_event_path = os.path.abspath(path)
            target_event_name = local_event_name(path)
            target_event_dir = None
        else:
            print(f"Warning: invalid event filename: {path}")
            target_event_path = os.path.abspath(path)
            target_event_name = os.path.basename(path)
            target_event_dir = None
    elif os.path.isdir(path):
        # Loop to support going back from viewer with 'q' and refreshing available logs
        previously_selected = set()
        while True:
            target_options = []
            for root, dirs, files in os.walk(path):
                for file in files:
                    if is_event_file(file):
                        size = os.path.getsize(os.path.join(root, file))
                        display_path_no_prefix = root.replace(path, '').lstrip(os.sep)
                        target_options.append((root, file, size, display_path_no_prefix))
            if len(target_options) == 0:
                raise RuntimeError(f"No event file found in directory {path}")
            target_options = sorted(target_options, key=lambda x:x[1], reverse=True)
            options = [f'[{i}] {op[3]}/{local_event_name(op[1])}' for i, op in enumerate(target_options)]
            # Pre-select previously chosen items if returning from viewer
            default_selected = []
            if previously_selected:
                for i, op in enumerate(target_options):
                    root, file, _size, _disp = op
                    if (root, file) in previously_selected:
                        default_selected.append(options[i])

            questions = [
                inquirer.Checkbox('choices',
                                   message="Select one or more event files (space to toggle, enter to view)",
                                   choices=options,
                                   default=default_selected if default_selected else None,
                                   carousel=True,
                                   )
            ]
            answers = inquirer.prompt(questions)
            if answers is None:
                return
            selected = answers.get('choices') or []
            if not selected:
                return

            # Map selections to event paths and tags
            selected_indices = [options.index(choice) for choice in selected]
            selected_event_paths = []
            selected_event_tags = []
            for idx in selected_indices:
                root, file, _size, _disp = target_options[idx]
                ev_path = os.path.abspath(os.path.join(root, file))
                ev_name = local_event_name(file)

                ev_dir = root.replace(path, '').lstrip(os.sep)
                ev_tag = ev_dir if ev_dir is not None else ev_name
                selected_event_paths.append(ev_path)
                selected_event_tags.append(ev_tag)

            tbviewer = TensorboardViewer(selected_event_paths, selected_event_tags)
            should_reselect = tbviewer.run()
            if not should_reselect:
                return
            # Remember selected items for next loop iteration
            previously_selected = set()
            for idx in selected_indices:
                root, file, _size, _disp = target_options[idx]
                previously_selected.add((root, file))
    
    target_event_tag = target_event_name if target_event_dir is None else target_event_dir

    if args.h5:
        import h5py
        import numpy as np
        records = {}
        for event in read_records(target_event_path):
            summary = event.summary
            for value in summary.value:
                if value.HasField('simple_value'):
                    # print(value.tag, value.simple_value, event.step)
                    if value.tag not in records:
                        records[value.tag] = {}
                    records[value.tag][event.step] = value.simple_value
        
        with h5py.File(os.path.dirname(target_event_path)+os.sep+'[hdf5]' + os.path.basename(target_event_path)+'.h5', 'w') as hf:
            for tag in records:
                group = hf.create_group(tag)
                
                steps = sorted(records[tag].keys())
                values = [records[tag][step] for step in steps]
                
                steps_array = np.array(steps, dtype='int64')
                values_array = np.array(values, dtype='float32')
                
                group.create_dataset('steps', data=steps_array)
                group.create_dataset('values', data=values_array)
    else:
        tbviewer = TensorboardViewer(target_event_path, target_event_tag)
        tbviewer.run()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('path', help='path to tensorboard log directory or event file', type=check_file_or_directory)
    parser.add_argument('-h5', action='store_true', help='convert to h5 file')
    parser.usage = f'{sys.argv[0]} path'

    args = parser.parse_args()

    run_main(args)

if __name__ == '__main__':
    main()
