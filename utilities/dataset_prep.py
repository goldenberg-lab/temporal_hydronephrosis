"""
Use this module to prepare data for model training. Can be used to prepare sequence of images.
"""

import json
import math
import os
import random
from collections import defaultdict

import cv2
import numpy as np
import pytorch_lightning as pl
import torch
from PIL import Image
from kornia.augmentation import *
from skimage import img_as_float, transform, exposure
from skimage.transform import resize
from sklearn.model_selection import StratifiedKFold, StratifiedShuffleSplit
from sklearn.utils import shuffle
from torch.utils.data import DataLoader


# Main data module
class KidneyDataModule(pl.LightningDataModule):
    def __init__(self, args, dataloader_params, debug=True):
        super().__init__()
        self.args = args
        self.train_dataloader_params = dataloader_params
        self.val_dataloader_params = dataloader_params.copy()
        self.val_dataloader_params['shuffle'] = False

        self.SEED = 42
        self.debug = debug
        if self.debug:  # if debugging, will save training and validation dictionaries to object
            self.train_dicts, self.val_dicts = None, None

        self.fold = 0  # indexer for cross-fold validation
        self.train_img_dict, self.train_label_dict, self.train_cov_dict, self.train_study_ids = None, None, None, None
        self._train_val_idx_generator = None

        self.test_set, self.st_test_set, self.stan_test_set, self.ui_test_set = None, None, None, None

    def setup(self, stage='fit'):
        args = self.args
        if stage == 'fit':
            train_dict, test_dict = load_dataset(args.json_infile, test_prop=0.2, ordered_split=args.ordered_split,
                                                 train_only=args.train_only,
                                                 data_dir=args.data_dir)

            train_img_dict, train_label_dict, train_cov_dict, train_study_ids = get_data_dicts(train_dict,
                                                                                               data_dir=args.data_dir,
                                                                                               seq=not args.single_visit,
                                                                                               include_baseline_date=args.ordered_validation)
            # Prepare data into sequences

            # Split into train-validation sets
            if args.include_validation:
                train_labels = list(train_label_dict.values())
                if args.cv:
                    train_val_ids_generator = make_cross_validation_sets(train_study_ids, train_labels,
                                                                         num_folds=args.num_folds)
                else:
                    train_val_ids_generator = make_train_val_split(train_study_ids, train_labels, train_cov_dict,
                                                                   ordered_split=args.ordered_validation)
            else:
                train_val_ids_generator = [(range(len(train_study_ids)), [])]

            # Remove baseline date from covariates (if used to order train-val_split)
            remove_bl_date(train_cov_dict)

            self._train_val_idx_generator = train_val_ids_generator

            self.train_img_dict = train_img_dict
            self.train_label_dict = train_label_dict
            self.train_cov_dict = train_cov_dict
            self.train_study_ids = np.array(list(self.train_img_dict.keys()))

            # Test set
            if not args.train_only:
                self.test_set = get_data_dicts(test_dict, data_dir=args.data_dir, seq=not args.single_visit,
                                               last_visit_only=args.single_visit)

        if stage == 'test_heldout':
            # Silent Trial
            st_test_dict = load_test_dataset(args.json_st_test, data_dir=args.data_dir)
            self.st_test_set = get_data_dicts(st_test_dict, data_dir=args.data_dir, silent_trial=True)

            # Stanford
            stan_test_dict = load_test_dataset(args.json_stan_test, data_dir=args.data_dir)
            self.stan_test_set = get_data_dicts(stan_test_dict, data_dir=args.data_dir)

            # UIowa
            ui_test_dict = load_test_dataset(args.json_ui_test, data_dir=args.data_dir)
            self.ui_test_set = get_data_dicts(ui_test_dict, data_dir=args.data_dir)

    def train_dataloader(self):
        train_idx, _ = self._train_val_idx_generator[self.fold]
        train_ids = self.train_study_ids[train_idx]

        train_imgs_dict = {train_id: self.train_img_dict[train_id] for train_id in train_ids}
        train_labels_dict = {train_id: self.train_label_dict[train_id] for train_id in train_ids}
        train_cov_dict = {train_id: self.train_cov_dict[train_id] for train_id in train_ids}

        train_dicts = train_imgs_dict, train_labels_dict, train_cov_dict, train_ids

        if self.debug:
            self.train_dicts = train_dicts

        training_set = KidneyDataset(train_dicts, include_cov=self.args.include_cov)
        training_generator = DataLoader(training_set, **self.train_dataloader_params)
        return training_generator

    def val_dataloader(self):
        _, val_ids = self._train_val_idx_generator[self.fold]
        val_ids = self.train_study_ids[val_ids]

        if len(val_ids) == 0:
            return None

        val_imgs_dict = {val_id: self.train_img_dict[val_id] for val_id in val_ids}
        val_labels_dict = {val_id: self.train_label_dict[val_id] for val_id in val_ids}
        val_cov_dict = {val_id: self.train_cov_dict[val_id] for val_id in val_ids}
        val_dicts = val_imgs_dict, val_labels_dict, val_cov_dict, val_ids

        if self.debug:
            self.val_dicts = val_dicts

        val_set = KidneyDataset(data_dicts=val_dicts, include_cov=self.args.include_cov)
        val_generator = DataLoader(val_set, **self.val_dataloader_params)
        return val_generator

    def test_dataloader(self):
        test_set = KidneyDataset(data_dicts=self.test_set, include_cov=self.args.include_cov)
        test_generator = DataLoader(test_set, **self.val_dataloader_params)
        return test_generator

    def st_test_dataloader(self):
        st_test_set = KidneyDataset(data_dicts=self.st_test_set, include_cov=args.include_cov)
        st_test_generator = DataLoader(st_test_set, **self.val_dataloader_params)
        return st_test_generator

    def stan_test_dataloader(self):
        stan_test_set = KidneyDataset(data_dicts=self.stan_test_set, include_cov=args.include_cov)
        stan_test_generator = DataLoader(stan_test_set, **self.val_dataloader_params)
        return stan_test_generator

    def ui_test_dataloader(self):
        ui_test_set = KidneyDataset(data_dicts=self.ui_test_set, include_cov=args.include_cov)
        ui_test_generator = DataLoader(ui_test_set, **self.val_dataloader_params)
        return ui_test_generator


class DataAugmentation(torch.nn.Module):
    """Module to perform batch data augmentation on torch tensors using Kornia."""

    def __init__(self, normalize=False, random_rotation=False, color_jitter=False,
                 random_gaussian_blur=False, random_motion_blur=False, random_noise=False,
                 prob=0.) -> None:
        super().__init__()
        self.transforms = torch.nn.Sequential()

        if normalize:
            self.transforms.add(Normalize(mean=0.0, std=1.0, p=1.0))

        if random_rotation:
            self.transforms.add(RandomRotation((-0.15, 0.15), p=prob))

        if color_jitter:
            self.transforms.add(ColorJitter(brightness=[0.5, 2], contrast=[0.5, 2], p=prob))

        if random_gaussian_blur:
            self.transforms.add(RandomGaussianBlur((3, 3), (0.1, 2.0), p=prob))

        if random_motion_blur:
            self.transforms.add(RandomMotionBlur(3, 35., 0.5, p=prob))

        if random_noise:
            self.transforms.add(RandomGaussianNoise(p=prob))

    @torch.no_grad()
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x_augmented = self.transforms(x)
        return x_augmented


# ==DATA LOADING==:
class KidneyDataset(torch.utils.data.Dataset):
    def __init__(self, data_dicts, include_cov=False, rand_crop=False):
        self.image_dict, self.label_dict, self.cov_dict, self.study_ids = data_dicts
        self.include_cov = include_cov

    def __getitem__(self, index):
        id_ = list(self.study_ids)[index]
        img, y, cov = self.image_dict[id_], self.label_dict[id_], self.cov_dict[id_]

        id_split = id_.split("_")
        data = {'img': torch.FloatTensor(img)}
        data['img'] = torch.div(data['img'], 255)

        if self.include_cov:

            if type(cov['Age_wks']) in [int, float, np.int32, np.float32, np.float64]:
                age = [cov['Age_wks']]
            else:
                age = cov['Age_wks']
            data['Age_wks'] = torch.FloatTensor(age)
            data['Side_L'] = torch.FloatTensor([1 if id_split[1] == 'Left' else 0] * len(age))

        return data, y, id_

    def __len__(self):
        return len(self.study_ids)

    # def get_class_proportions(self):
    #     """Returns an array of values inversely proportional to the number of items in the class. Classes with a greater
    #     number of examples compared to other classes receives lesser weight.
    #     """
    #     counts = np.bincount(self.y)
    #     labels_weights = 1. / counts
    #     weights = labels_weights[self.y]
    #     return weights


def load_dataset(json_infile, test_prop, data_dir, ordered_split=False, train_only=False):
    """Return dictionaries of train (and optionally test) set, where each key is the study ID of some patient.

    @param json_infile: json containing information about each patient and file paths to each kidney side
    @param test_prop: proportion to leave as test set
    @param data_dir: path to directory containing files
    @param ordered_split: sort by date before splitting by patient id
    @param train_only: split dataset into train-test split if true
    """
    with open(data_dir + json_infile, 'r') as fp:
        in_dict = json.load(fp)

    if 'SU2bae8dc' in in_dict:
        in_dict.pop('SU2bae8dc', None)

    if train_only:
        train_out = in_dict
        return train_out, None
    else:
        pt_ids = list(in_dict.keys())
        BL_dates = []
        for my_key in pt_ids:
            if 'BL_date' in in_dict[my_key].keys():
                BL_dates.extend([in_dict[my_key]['BL_date']])
            else:
                BL_dates.extend(['2021-01-01'])

        if ordered_split:
            sorted_pt_BL_dates = sorted(zip(pt_ids, BL_dates))
            test_n = round(len(pt_ids) * test_prop)
            train_out = dict()
            test_out = dict()

            for i in range(len(pt_ids) - test_n):
                study_id, _ = sorted_pt_BL_dates[i]
                train_out[study_id] = in_dict[study_id]

            for i in range(len(pt_ids) - test_n + 1, len(pt_ids)):
                study_id, _ = sorted_pt_BL_dates[i]
                test_out[study_id] = in_dict[study_id]
        else:
            shuffled_pt_id, shuffled_BL_dates = shuffle(list(pt_ids), BL_dates, random_state=42)
            test_n = round(len(shuffled_pt_id) * test_prop)
            train_out = dict()
            test_out = dict()

            for i in range(test_n):
                study_id = shuffled_pt_id[i]
                test_out[study_id] = in_dict[study_id]

            for i in range(test_n + 1, len(shuffled_pt_id)):
                study_id = shuffled_pt_id[i]
                train_out[study_id] = in_dict[study_id]

        return train_out, test_out


def load_test_dataset(json_infile, data_dir):
    with open(data_dir + json_infile, 'r') as fp:
        in_dict = json.load(fp)

    if 'SU2bae8dc' in in_dict:
        in_dict.pop('SU2bae8dc', None)

    return in_dict


def get_data_dicts(in_dict, data_dir, update_num=None, silent_trial=False, last_visit_only=False, seq=False,
                   include_baseline_date=False):
    """Return tuple containing 3 dictionaries of images, labels, covariates and a list of study IDs.

    :param in_dict: Dictionary of nested dictionaries, where outermost keys correspond to patient IDs.
    :param data_dir: Path to data directory
    :param silent_trial: If data is from silent trial, perform specific processing
    :param update_num: Number to append to patient ID if transformation is applied
    :param last_visit_only: If true, only include the last ultrasound visit.
    :param seq: If true, returned dicts will contain sequences (lists) of images and covariates, corresponding to visits
    :param include_baseline_date: If true, include baseline date for each patient.
    """
    img_dict = dict()
    label_dict = dict()
    cov_dict = dict()

    for study_id in in_dict.keys():
        try:
            sides = np.setdiff1d(list(in_dict[study_id].keys()), ['BL_date', 'Sex'])
            for side in sides:
                surgery = get_surgery_label(in_dict[study_id][side])
                us_nums = [my_key for my_key in in_dict[study_id][side].keys() if my_key != 'surgery']

                if len(us_nums) == 0 or surgery is None:
                    continue

                if last_visit_only:
                    us_nums = sorted(us_nums)[-1]

                for us_num in us_nums:
                    try:
                        if in_dict[study_id][side][us_num]['Age_wks'] in ["NA", None] or \
                                not {'sag', 'trv'}.issubset(in_dict[study_id][side][us_num].keys()):
                            continue
                    except KeyError as e:
                        print(study_id, side, us_num, 'Age_wks')
                        print(us_nums)
                        raise e

                    dict_key = study_id + "_" + side
                    dict_key += f"_{us_num}" if not seq else ""
                    dict_key += f"_{update_num}" if update_num is not None else ""

                    # Get covariates
                    machine = in_dict[study_id][side][us_num]['US_machine']
                    sex = in_dict[study_id]['Sex']
                    age_wks = in_dict[study_id][side][us_num]['Age_wks']
                    bl_date = in_dict[study_id]['BL_date'] if include_baseline_date else None

                    # Verify label and covariate age
                    if surgery not in [0, 1]:
                        print("Invalid surgery value! Will be skipped.")
                        continue
                    if age_wks < 0:
                        print("Negative age value! Will be skipped.")
                        continue
                    elif age_wks > 52 * 10:
                        print("Patient over the age of 10! Will be skipped.")
                        continue

                    # Initialize
                    if dict_key not in img_dict:
                        img_dict[dict_key] = dict() if not seq else list()
                    if dict_key not in cov_dict:
                        cov_dict[dict_key] = dict() if not seq else defaultdict(list)

                    # Get images
                    if silent_trial:
                        sag_img = process_input_image(data_dir + in_dict[study_id][side][us_num]['sag'])
                        trv_img = process_input_image(data_dir + in_dict[study_id][side][us_num]['trv'])
                    else:
                        sag_img = special_ST_preprocessing(data_dir + in_dict[study_id][side][us_num]['sag'])
                        trv_img = special_ST_preprocessing(data_dir + in_dict[study_id][side][us_num]['trv'])

                    # Assign values
                    assign_images(img_dict, dict_key, sag_img, trv_img, split_view=False, seq=seq)
                    label_dict[dict_key] = surgery
                    assign_covariates(cov_dict, dict_key, machine, sex, age_wks, seq=seq, bl_date=bl_date)

                    assert label_dict[dict_key] is not None
                    assert img_dict[dict_key] is not None
                    assert cov_dict[dict_key]['Sex'] is not None
                    assert cov_dict[dict_key]['Age_wks'] is not None
        except AttributeError as e:
            print(e)
        except AssertionError as f:
            print(f)

    ids = list(img_dict.keys())
    return img_dict, label_dict, cov_dict, ids


def make_cross_validation_sets(train_study_ids, train_labels, num_folds=5):
    """Cross-fold validation. Split training data into training and validation splits using IDs. Return list containing
    tuples of (train_ids, val_ids), where each tuple corresponds to <num_folds> splits."""
    skf = StratifiedKFold(n_splits=num_folds, shuffle=True, random_state=42)
    return list(skf.split(train_study_ids, train_labels))


def make_train_val_split(train_study_ids, train_labels, train_cov_dict, split=0.8, ordered_split=True):
    """Split training data into a training set and validation set."""
    if ordered_split:
        date = [train_cov_dict[id_]['BL_date'] for id_ in train_study_ids]
        sorted_id_date = sorted(zip(train_study_ids, date), key=lambda x: x[1])
        sorted_ids = list(zip(*sorted_id_date))[0]
        end_idx = math.floor((len(sorted_ids) * split))

        train_ids = sorted_ids[:end_idx]

        train_idx = []
        val_idx = []
        for i in range(len(train_study_ids)):
            if train_study_ids[i] in train_ids:
                train_idx.append(i)
            else:
                val_idx.append(i)

        shuffled_train_idx = shuffle(train_idx, random_state=42)
        shuffled_val_idx = shuffle(val_idx, random_state=42)

        return [(shuffled_train_idx, shuffled_val_idx)]
    else:
        sss = StratifiedShuffleSplit(n_splits=1, train_size=split, random_state=42)
        return list(sss.split(X=train_study_ids, y=train_labels))


# ==HELPER FUNCTIONS==:
def get_surgery_label(side_dict: dict):
    """Given dictionary for a kidney side, extract surgery label (1/0). Return None if not found."""
    surgery = None
    if type(side_dict) == dict:
        if 'surgery' in list(side_dict.keys()):
            if type(side_dict['surgery']) == int:
                surgery = side_dict['surgery']
            elif type(side_dict['surgery']) == list:
                s1 = [i for i in side_dict['surgery'] if type(i) == int]
                if len(s1) > 0:
                    surgery = s1[0]
    return surgery


def assign_images(img_dict: dict, dict_key: str, sag_img, trv_img, split_view=False, seq=False):
    """Assign or append images (separately or combined) to <img_dict>."""
    if not seq:
        if split_view:
            img_dict[dict_key]['sag'] = sag_img
            img_dict[dict_key]['trv'] = trv_img
        else:
            img_dict[dict_key] = np.stack([sag_img, trv_img])
    else:
        if split_view:
            raise NotImplementedError("Sequences of split views (trans, sag) is not implemented!")
        else:
            img_dict[dict_key].append(np.stack([sag_img, trv_img]))


def assign_covariates(cov_dict: dict, dict_key: str, machine: str, sex, age_wks, seq=False, bl_date=None):
    """Assign covariates to key in <cov_dict>, or append covariates to list at key in <cov_dict> if building a sequence.
    """
    if not seq:
        cov_dict[dict_key]['US_machine'] = machine
        if type(sex) == int:
            cov_dict[dict_key]['Sex'] = sex
        else:
            cov_dict[dict_key]['Sex'] = 1 if (sex == "M") else 2
        cov_dict[dict_key]['Age_wks'] = age_wks

        if bl_date is not None:
            cov_dict[dict_key]['BL_date'] = bl_date
    else:
        cov_dict[dict_key]['US_machine'].append(machine)
        if type(sex) == int:
            cov_dict[dict_key]['Sex'].append(sex)
        else:
            cov_dict[dict_key]['Sex'].append(1 if (sex == "M") else 2)
        cov_dict[dict_key]['Age_wks'].append(age_wks)
        if bl_date is not None:
            cov_dict[dict_key]['BL_date'] = bl_date


def flatten(lst):
    # from https://stackoverflow.com/questions/2158395/flatten-an-irregular-list-of-lists
    return eval('[' + str(lst).replace('[', '').replace(']', '') + ']')


def remove_bl_date(cov_dict):
    """Remove baseline date from cov_dict in place, where each key in cov_dict is a patient ID."""
    for u, v in cov_dict.items():
        if 'BL_date' in v.keys():
            v.pop('BL_date')


# ==IMAGE PREPROCESSING FUNCTIONS==:
def pad_img_le(image_in, dim=256, random_pad=False):
    """
    :param image_in: input image
    :param dim: desired dimensions of padded image (should be bigger or as big as all input images)
    :param random_pad: pad random amount (and flip horizontally randomly)
    :return: padded image
    """
    im_shape = image_in.shape
    while im_shape[0] > dim or im_shape[1] > dim:
        image_in = resize(image_in, ((im_shape[0] * 4) // 5, (im_shape[1] * 4) // 5), anti_aliasing=True)
        im_shape = image_in.shape

    if random_pad:
        if random.random() >= 0.5:
            image_in = cv2.flip(image_in, 1)

        rand_h = np.random.uniform(0, 1, 1)
        rand_v = np.random.uniform(0, 1, 1)
        right = math.floor((dim - im_shape[1]) * rand_h)
        left = math.ceil((dim - im_shape[1]) * (1 - rand_h))
        bottom = math.floor((dim - im_shape[0]) * rand_v)
        top = math.ceil((dim - im_shape[0]) * (1 - rand_v))
    else:
        right = math.floor((dim - im_shape[1]) / 2)
        left = math.ceil((dim - im_shape[1]) / 2)
        bottom = math.floor((dim - im_shape[0]) / 2)
        top = math.ceil((dim - im_shape[0]) / 2)

    image_out = cv2.copyMakeBorder(image_in, top, bottom, left, right, cv2.BORDER_CONSTANT, value=0)
    return image_out


def autocrop(image, threshold=0):
    """Crops any edges below or equal to threshold

    from fviktor here: https://stackoverflow.com/questions/13538748/crop-black-edges-with-opencv
    Crops blank image to 1x1.

    Returns cropped image.
    """
    if len(image.shape) == 3:
        flatImage = np.max(image, 2)
    else:
        flatImage = image
    assert len(flatImage.shape) == 2

    rows = np.where(np.max(flatImage, 0) > threshold)[0]
    if rows.size:
        cols = np.where(np.max(flatImage, 1) > threshold)[0]
        image = image[cols[0]: cols[-1] + 1, rows[0]: rows[-1] + 1]
    else:
        image = image[:1, :1]

    return image


def set_contrast(image, contrast=1):
    """Set contrast in image

    :param image: input image
    :param contrast: contrast type
    :return: image with revised contrast
    """
    out_img = None
    if contrast == 0:
        out_img = image
    elif contrast == 1:
        out_img = exposure.equalize_hist(image)
    elif contrast == 2:
        out_img = exposure.equalize_adapthist(image)
    elif contrast == 3:
        out_img = exposure.rescale_intensity(image)
    return out_img


def crop_image(image, random_crop=False):
    # IMAGE_PARAMS = {0: [2.1, 4, 4],
    #                 # 1: [2.5, 3.2, 3.2],
    #                 # 2: [1.7, 4.5, 4.5] # [2.5, 3.2, 3.2] # [1.7, 4.5, 4.5]
    #                 }
    width = image.shape[1]
    height = image.shape[0]

    if random_crop:
        par1 = random.uniform(1.5, 2.8)
        par2 = random.uniform(3, 4.5)
        par3 = random.uniform(3, 4.5)
    else:
        par1 = 2.1
        par2 = 4
        par3 = 4

    new_dim = int(width // par1)  # final image will be square of dim width/2 * width/2

    start_col = int(width // par2)  # column (width position) to start from
    start_row = int(height // par3)  # row (height position) to start from

    cropped_image = image[start_row:start_row + new_dim, start_col:start_col + new_dim]

    return cropped_image


def special_ST_preprocessing(img_file, output_dim=256):
    if "preprocessed" in img_file:
        my_img = process_input_image(img_file)
    else:
        img_name = img_file.split('/')[len(img_file.split('/')) - 1]
        img_folder = "/".join(img_file.split('/')[:-2])
        if not os.path.exists(img_folder + "/Preprocessed/"):
            os.makedirs(img_folder + "/Preprocessed/")
        out_img_filename = img_folder + "/Preprocessed/" + img_name.split('.')[0] + "-preprocessed.png"

        if not os.path.exists(out_img_filename):
            image = np.array(Image.open(img_file).convert('L'))
            image_grey = img_as_float(image)
            cropped_img = image_grey
            resized_img = transform.resize(cropped_img, output_shape=(output_dim, output_dim))
            my_img = set_contrast(resized_img)  # ultimately add contrast variable
            Image.fromarray(my_img * 255).convert('RGB').save(out_img_filename)
        my_img = np.array(Image.open(out_img_filename).convert('L'))
    return my_img


def process_input_image(img_file, crop=None, random_crop=None):
    """Processes image: crop, convert to greyscale and resize
    
    :param img_file: path to image
    :param crop: crop image if true
    :param random_crop: randomly crop
    :return: formatted image
    """
    fit_img = np.array(Image.open(img_file).convert('L'))
    if fit_img.shape[0] != 256 or fit_img.shape[1] != 256:
        print(img_file)
        fit_img = special_ST_preprocessing(img_file)

    return fit_img
