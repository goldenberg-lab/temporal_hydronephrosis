import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import umap

from utilities.dataset_prep import load_dataset, get_data_dicts, KidneyDataModule

matplotlib.use("Qt5Agg")


class DataViewer:
    """DataViewer class. Used to describe sequences of images, and their class/covariate distribution.
    """

    def __init__(self, img_dict, label_dict, cov_dict, study_ids):
        """
        Initialize DataViewer class.

        :param img_dict: Mapping of patient ID to images
        :param label_dict: Mapping from patient ID to surgery labels
        :param cov_dict: Mapping from patient ID to covariates
        :param study_ids: list of patient IDs
        """
        self.id_side_to_target = {"_".join(u.split("_")[:-1]): v for u, v in label_dict.items()}

        self.img_dict = img_dict
        self.label_dict = label_dict
        self.df_cov = self.process_cov_dict(cov_dict)
        self.df_examples = self.extract_num_visits(self.df_cov)
        self.study_ids = study_ids

    def process_cov_dict(self, cov_dict) -> pd.DataFrame:
        """Extracts identifier related variables from cov_dict. Returns dataframe."""
        df_cov = pd.DataFrame(cov_dict).T.reset_index()
        df_cov['ID_Side'] = df_cov['index'].map(lambda x: "_".join(x.split("_")[:-1]))
        df_cov['ID'] = df_cov['index'].map(lambda x: x.split("_")[0])
        df_cov['Side_L'] = df_cov['index'].map(lambda x: x.split("_")[1])
        df_cov['surgery'] = df_cov['ID_Side'].map(lambda x: self.id_side_to_target[x])
        return df_cov

    def extract_num_visits(self, df_cov):
        """Preprocess dataframe of covariates to get information on each sample (e.g. patient kidney) over multiple
        visits."""
        def _extract_info_per_patient(df_):
            """Get the age range (max - min) of patient across all their visits, and get their number of visits"""
            age_range = max(df_["Age_wks"]) - min(df_['Age_wks'])
            num_visits = len(df_)
            id_ = df_['ID'].iloc[0]
            side = df_['Side_L'].iloc[0]

            row = pd.DataFrame({'age_range': age_range,
                                'num_visits': num_visits,
                                'ID': id_,
                                'Side_L': side}, index=[0])
            return row

        df_cov = df_cov.copy()
        df_examples = df_cov.groupby(by=['ID_Side']).apply(lambda df_: _extract_info_per_patient(df_))
        df_examples = df_examples.reset_index().drop(columns=['level_1'])
        df_examples['surgery'] = df_examples['ID_Side'].map(lambda x: self.id_side_to_target[x])
        return df_examples

    def get_proportion_positive(self):
        """Returns proportion of samples that are positive."""
        return self.df_cov["surgery"].mean()

    def plot_num_visits(self, title=None):
        fig, ax = plt.subplots()
        sns.histplot(data=self.df_examples, x='num_visits', hue='surgery', discrete=True, multiple='stack',
                     ax=ax)
        plt.xticks(np.arange(min(self.df_examples['num_visits']), max(self.df_examples['num_visits']) + 1, 1))
        plt.xlabel("Number of Visits")
        plt.ylabel("Count")
        plt.tight_layout()

        if title is not None:
            plt.title(title)

        plt.show()

    def plot_age(self, title=None):
        fig, ax = plt.subplots()
        sns.histplot(data=self.df_cov, x='Age_wks', hue='surgery', multiple='stack', ax=ax)
        plt.xlabel("Age (in weeks)")
        plt.ylabel("Count")
        plt.tight_layout()

        if title is not None:
            plt.title(title)

        plt.show()


def plot_umap(embeds, labels, save_dir=None, plot_name="UMAP"):
    """Given 2-dimensional UMAP embeddings and labels for each sample, create UMAP plot."""
    plt.style.use('dark_background')
    sns.scatterplot(x=embeds[:, 0], y=embeds[:, 1],
                    hue=labels,
                    legend="full",
                    alpha=1,
                    palette="tab20",
                    s=7,
                    linewidth=0)
    plt.legend(bbox_to_anchor=(1, 1), loc="upper left")
    plt.xlabel("")
    plt.ylabel("")
    plt.tick_params(left=False,
                    bottom=False,
                    labelleft=False,
                    labelbottom=False)
    plt.tight_layout()

    # Save Figure
    if save_dir:
        plt.savefig(f"{save_dir}/{plot_name}.png", bbox_inches='tight', dpi=400)


def load_data():
    from drivers.model_training_pl import parseArgs, modifyArgs
    args = parseArgs()
    modifyArgs(args)

    data_params = {'batch_size': 1,
                   'shuffle': True,
                   'num_workers': args.num_workers,
                   'pin_memory': True,
                   'persistent_workers': True if args.num_workers else False}

    dm = KidneyDataModule(args, data_params)
    dm.setup()
    dm.fold = 0
    dm.train_dataloader()       # to save training set to object
    dm.val_dataloader()         # to save validation set to object

    return dm


def describe_data(data_dicts, plot_title=None):
    img_dict, label_dict, cov_dict, study_ids = data_dicts

    data_viewer = DataViewer(img_dict, label_dict, cov_dict, study_ids)
    data_viewer.plot_age(title=plot_title)
    plt.show()
    data_viewer.plot_num_visits(title=plot_title)

    return data_viewer, data_viewer.df_cov, data_viewer.df_examples

    # data_viewer.show_num_visits(positive_only=True)
    # data_viewer.show_num_patients()
    # data_viewer.plot_cov_distribution()
    # data_viewer.plot_imaging_date_frequencies()


if __name__ == "__main__":
    dm = load_data()

    train_viewer, df_train_cov, df_train_examples = describe_data(dm.train_dicts, plot_title='Ordered Training Set')
    val_viewer, df_val_cov, df_val_examples = describe_data(dm.val_dicts, plot_title='Ordered Validation Set')
    test_viewer, df_test_cov, df_test_examples = describe_data(dm.test_set, plot_title='Ordered Test Set')

    # describe_data((X_train_, y_train_, cov_train_), (X_test_, y_test_, cov_test_))

    # train_val_generator = make_validation_set(X_train_, y_train_, cov_train_, cv=True, num_folds=5)
    # i = 1
    # for train_val_fold in train_val_generator:  # only iterates once if not cross-fold validation
    #     print(f"Fold {i}/{5}")
    #     X_train, y_train, cov_train, X_val, y_val, cov_val = train_val_fold
    #     describe_data((X_train, y_train, cov_train), (X_val, y_val, cov_val))
    #     i += 1
