from typing import Any, Callable, cast, Dict, List, Optional, Tuple
import numpy as np
import spikingjelly.datasets as sjds
from torchvision.datasets.utils import extract_archive
import os
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
import time
from ..configure import max_threads_number_for_datasets_preprocess


class DVS128Gesture(sjds.NeuromorphicDatasetFolder):
    def __init__(
            self,
            root: str,
            train: bool = None,
            data_type: str = 'event',
            frames_number: int = None,
            split_by: str = None,
            duration: int = None,
            custom_integrate_function: Callable = None,
            custom_integrated_frames_dir_name: str = None,
            transform: Optional[Callable] = None,
            target_transform: Optional[Callable] = None,
    ) -> None:
        '''
        :param root: root path of the dataset
        :type root: str
        :param train: whether use the train set
        :type train: bool
        :param data_type: `event` or `frame`
        :type data_type: str
        :param frames_number: the integrated frame number
        :type frames_number: int
        :param split_by: `time` or `number`
        :type split_by: str
        :param duration: the time duration of each frame
        :type duration: int
        :param custom_integrate_function: a user-defined function that inputs are ``events, H, W``.
            ``events`` is a dict whose keys are ``['t', 'x', 'y', 'p']`` and values are ``numpy.ndarray``
            ``H`` is the height of the data and ``W`` is the weight of the data.
            For example, H=128 and W=128 for the DVS128 Gesture dataset.
            The user should define how to integrate events to frames, and return frames.
        :type custom_integrate_function: Callable
        :param custom_integrated_frames_dir_name: The name of directory for saving frames integrating by ``custom_integrate_function``.
            If ``custom_integrated_frames_dir_name`` is ``None``, it will be set to ``custom_integrate_function.__name__``
        :type custom_integrated_frames_dir_name: str or None
        :param transform: a function/transform that takes in
            a sample and returns a transformed version.
            E.g, ``transforms.RandomCrop`` for images.
        :type transform: callable
        :param target_transform: a function/transform that takes
            in the target and transforms it.
        :type target_transform: callable

        If ``data_type == 'event'``
            the sample in this dataset is a dict whose keys are ``['t', 'x', 'y', 'p']`` and values are ``numpy.ndarray``.

        If ``data_type == 'frame'`` and ``frames_number`` is not ``None``
            events will be integrated to frames with fixed frames number. ``split_by`` will define how to split events.
            See :class:`spikingjelly.datasets.cal_fixed_frames_number_segment_index` for
            more details.

        If ``data_type == 'frame'``, ``frames_number`` is ``None``, and ``duration`` is not ``None``
            events will be integrated to frames with fixed time duration.

        If ``data_type == 'frame'``, ``frames_number`` is ``None``, ``duration`` is ``None``, and ``custom_integrate_function`` is not ``None``:
            events will be integrated by the user-defined function and saved to the ``custom_integrated_frames_dir_name`` directory in ``root`` directory.
            Here is an example from SpikingJelly's tutorials:

            .. code-block:: python

                from spikingjelly.datasets.dvs128_gesture import DVS128Gesture
                from typing import Dict
                import numpy as np
                import spikingjelly.datasets as sjds
                def integrate_events_to_2_frames_randomly(events: Dict, H: int, W: int):
                    index_split = np.random.randint(low=0, high=events['t'].__len__())
                    frames = np.zeros([2, 2, H, W])
                    frames[0] = sjds.integrate_events_segment_to_frame(events, H, W, 0, index_split)
                    frames[1] = sjds.integrate_events_segment_to_frame(events, H, W, index_split, events['t'].__len__())
                    return frames

                root_dir = 'D:/datasets/DVS128Gesture'
                train_set = DVS128Gesture(root_dir, train=True, data_type='frame', custom_integrate_function=integrate_events_to_2_frames_randomly)

                from spikingjelly.datasets import play_frame
                frame, label = train_set[500]
                play_frame(frame)
        '''
        assert train is not None
        super().__init__(root, train, data_type, frames_number, split_by, duration, custom_integrate_function, custom_integrated_frames_dir_name, transform, target_transform)
    @staticmethod
    def resource_url_md5() -> list:
        '''
        :return: A list ``url`` that ``url[i]`` is a tuple, which contains the i-th file's name, download link, and MD5
        :rtype: list
        '''
        url = 'https://ibm.ent.box.com/s/3hiq58ww1pbbjrinh367ykfdf60xsfm8/folder/50167556794'
        return [
            ('DvsGesture.tar.gz', url, '8a5c71fb11e24e5ca5b11866ca6c00a1'),
            ('gesture_mapping.csv', url, '109b2ae64a0e1f3ef535b18ad7367fd1'),
            ('LICENSE.txt', url, '065e10099753156f18f51941e6e44b66'),
            ('README.txt', url, 'a0663d3b1d8307c329a43d949ee32d19')
        ]

    @staticmethod
    def downloadable() -> bool:
        '''
        :return: Whether the dataset can be directly downloaded by python codes. If not, the user have to download it manually
        :rtype: bool
        '''
        return False

    @staticmethod
    def extract_downloaded_files(download_root: str, extract_root: str):
        '''
        :param download_root: Root directory path which saves downloaded dataset files
        :type download_root: str
        :param extract_root: Root directory path which saves extracted files from downloaded files
        :type extract_root: str
        :return: None

        This function defines how to extract download files.
        '''
        fpath = os.path.join(download_root, 'DvsGesture.tar.gz')
        print(f'Extract [{fpath}] to [{extract_root}].')
        extract_archive(fpath, extract_root)


    @staticmethod
    def load_origin_data(file_name: str) -> Dict:
        '''
        :param file_name: path of the events file
        :type file_name: str
        :return: a dict whose keys are ``['t', 'x', 'y', 'p']`` and values are ``numpy.ndarray``
        :rtype: Dict

        This function defines how to read the origin binary data.
        '''
        return sjds.load_aedat_v3(file_name)

    @staticmethod
    def split_aedat_files_to_np(fname: str, aedat_file: str, csv_file: str, output_dir: str):
        events = DVS128Gesture.load_origin_data(aedat_file)
        print(f'Start to split [{aedat_file}] to samples.')
        # read csv file and get time stamp and label of each sample
        # then split the origin data to samples
        csv_data = np.loadtxt(csv_file, dtype=np.uint32, delimiter=',', skiprows=1)

        # Note that there are some files that many samples have the same label, e.g., user26_fluorescent_labels.csv
        label_file_num = [0] * 11

        # There are some wrong time stamp in this dataset, e.g., in user22_led_labels.csv, ``endTime_usec`` of the class 9 is
        # larger than ``startTime_usec`` of the class 10. So, the following codes, which are used in old version of SpikingJelly,
        # are replaced by new codes.


        for i in range(csv_data.shape[0]):
            # the label of DVS128 Gesture is 1, 2, ..., 11. We set 0 as the first label, rather than 1
            label = csv_data[i][0] - 1
            t_start = csv_data[i][1]
            t_end = csv_data[i][2]
            mask = np.logical_and(events['t'] >= t_start, events['t'] < t_end)
            file_name = os.path.join(output_dir, str(label), f'{fname}_{label_file_num[label]}.npz')
            np.savez(file_name,
                     t=events['t'][mask],
                     x=events['x'][mask],
                     y=events['y'][mask],
                     p=events['p'][mask]
                     )
            print(f'[{file_name}] saved.')
            label_file_num[label] += 1

        # old codes:

        # index = 0
        # index_l = 0
        # index_r = 0
        # for i in range(csv_data.shape[0]):
        #     # the label of DVS128 Gesture is 1, 2, ..., 11. We set 0 as the first label, rather than 1
        #     label = csv_data[i][0] - 1
        #     t_start = csv_data[i][1]
        #     t_end = csv_data[i][2]
        #
        #     while True:
        #         t = events['t'][index]
        #         if t < t_start:
        #             index += 1
        #         else:
        #             index_l = index
        #             break
        #     while True:
        #         t = events['t'][index]
        #         if t < t_end:
        #             index += 1
        #         else:
        #             index_r = index
        #             break
        #
        #     file_name = os.path.join(output_dir, str(label), f'{fname}_{label_file_num[label]}.npz')
        #     np.savez(file_name,
        #         t=events['t'][index_l:index_r],
        #         x=events['x'][index_l:index_r],
        #         y=events['y'][index_l:index_r],
        #         p=events['p'][index_l:index_r]
        #     )
        #     print(f'[{file_name}] saved.')
        #     label_file_num[label] += 1

    @staticmethod
    def create_events_np_files(extract_root: str, events_np_root: str):
        '''
        :param extract_root: Root directory path which saves extracted files from downloaded files
        :type extract_root: str
        :param events_np_root: Root directory path which saves events files in the ``npz`` format
        :type events_np_root:
        :return: None

        This function defines how to convert the origin binary data in ``extract_root`` to ``npz`` format and save converted files in ``events_np_root``.
        '''
        aedat_dir = os.path.join(extract_root, 'DvsGesture')
        train_dir = os.path.join(events_np_root, 'train')
        test_dir = os.path.join(events_np_root, 'test')
        os.mkdir(train_dir)
        os.mkdir(test_dir)
        print(f'Mkdir [{train_dir, test_dir}.')
        for label in range(11):
            os.mkdir(os.path.join(train_dir, str(label)))
            os.mkdir(os.path.join(test_dir, str(label)))
        print(f'Mkdir {os.listdir(train_dir)} in [{train_dir}] and {os.listdir(test_dir)} in [{test_dir}].')

        with open(os.path.join(aedat_dir, 'trials_to_train.txt')) as trials_to_train_txt, open(
                os.path.join(aedat_dir, 'trials_to_test.txt')) as trials_to_test_txt:
            # use multi-thread to accelerate
            t_ckp = time.time()
            with ThreadPoolExecutor(max_workers=min(multiprocessing.cpu_count(), max_threads_number_for_datasets_preprocess)) as tpe:
                print(f'Start the ThreadPoolExecutor with max workers = [{tpe._max_workers}].')

                for fname in trials_to_train_txt.readlines():
                    fname = fname.strip()
                    if fname.__len__() > 0:
                        aedat_file = os.path.join(aedat_dir, fname)
                        fname = os.path.splitext(fname)[0]
                        tpe.submit(DVS128Gesture.split_aedat_files_to_np, fname, aedat_file, os.path.join(aedat_dir, fname + '_labels.csv'), train_dir)

                for fname in trials_to_test_txt.readlines():
                    fname = fname.strip()
                    if fname.__len__() > 0:
                        aedat_file = os.path.join(aedat_dir, fname)
                        fname = os.path.splitext(fname)[0]
                        tpe.submit(DVS128Gesture.split_aedat_files_to_np, fname, aedat_file,
                                   os.path.join(aedat_dir, fname + '_labels.csv'), test_dir)

            print(f'Used time = [{round(time.time() - t_ckp, 2)}s].')
        print(f'All aedat files have been split to samples and saved into [{train_dir, test_dir}].')

    @staticmethod
    def get_H_W() -> Tuple:
        '''
        :return: A tuple ``(H, W)``, where ``H`` is the height of the data and ``W` is the weight of the data.
            For example, this function returns ``(128, 128)`` for the DVS128 Gesture dataset.
        :rtype: tuple
        '''
        return 128, 128