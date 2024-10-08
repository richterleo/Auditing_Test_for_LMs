import pandas as pd
import json
import logging
import numpy as np
import sys
import os

import hydra
from omegaconf import DictConfig
from hydra.utils import instantiate

from pathlib import Path
from scipy.stats import skew, wasserstein_distance
from typing import Union, List, Optional, Dict

import matplotlib.pyplot as plt
import textwrap
from matplotlib.ticker import MultipleLocator
import seaborn as sns
import colorsys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from evaluation.distance import (
    empirical_wasserstein_distance_p1,
    kolmogorov_variation,
    NeuralNetDistance,
    calc_tot_discrete_variation,
)
from utils.utils import load_config
from arguments import TrainCfg


from evaluation.analyze import (
    extract_data_for_models,
    get_power_over_sequences_from_whole_ds,
    get_power_over_sequences_for_models_or_checkpoints,
    get_power_over_sequences,
    get_distance_scores,
    get_matrix_for_models,
    get_power_over_sequences_for_ranked_checkpoints,
    get_power_over_sequences_for_ranked_checkpoints_wrapper,
    extract_power_from_sequence_df,
    get_alpha_wrapper,
    get_mean_and_std_for_nn_distance,
)

pd.set_option("display.max_rows", 1000)
pd.set_option("display.max_columns", 1000)
pd.set_option("display.width", 1000)

logger = logging.getLogger(__name__)

TASK_CLUSTER = [
    ["Program Execution", "Pos Tagging", "Mathematics"],
    ["Gender Classification", "Commonsense Classification", "Translation"],
    ["Code to Text", "Stereotype Detection", "Sentence Perturbation"],
    ["Text to Code", "Linguistic Probing", "Language Identification"],
    ["Data to Text", "Word Semantics", "Question Rewriting"],
]


def distance_box_plot(
    df,
    model_name1,
    seed1,
    seed2,
    model_name2,
    pre_shuffled=False,
    metric="perspective",
    plot_dir: str = "test_outputs",
    overwrite: bool = False,
):
    """ """

    if pre_shuffled:
        file_path = f"{plot_dir}/{model_name1}_{seed1}_{model_name2}_{seed2}/{metric}_distance_box_plot_unpaired.pdf"
    else:
        file_path = f"{plot_dir}/{model_name1}_{seed1}_{model_name2}_{seed2}/{metric}_distance_box_plot.pdf"

    if not overwrite and Path(file_path).exists():
        logger.info(f"File already exists at {file_path}. Skipping...")

    else:
        # Create a new column combining `num_samples` and `Wasserstein` for grouping
        df["Group"] = df["num_train_samples"].astype(str) + " | " + df["Wasserstein_comparison"].astype(str)

        wasserstein_df = df[["Wasserstein_comparison"]].rename(columns={"Wasserstein": "Distance"})
        wasserstein_df["Group"] = "Wasserstein"

        # 2. Two boxes for NeuralNet, split by num_samples
        neuralnet_df = df[["num_train_samples", "NeuralNet"]].rename(columns={"NeuralNet": "Distance"})
        neuralnet_df["Group"] = neuralnet_df["num_train_samples"].astype(str) + " NeuralNet"

        # Concatenate the dataframes
        combined_df = pd.concat([wasserstein_df, neuralnet_df[["Distance", "Group"]]])

        # Plotting the box plot using Seaborn
        plt.figure(figsize=(10, 6))
        sns.boxplot(x="Group", y="Distance", data=combined_df)
        plt.xticks(rotation=45)
        plt.title("Box Plot of Wasserstein and NeuralNet Distances")
        # plt.xlabel("Group")
        plt.ylabel("Distance")
        plt.grid(True)
        plt.savefig(
            file_path,
            bbox_inches="tight",
            format="pdf",
        )

        ## Plotting the box plot using Seaborn
        # plt.figure(figsize=(10, 6))
        # sns.boxplot(x='Group', y='NeuralNet', data=df)
        # plt.xticks(rotation=45)
        # plt.title('Box Plot of Different Methods to calculate Distance')
        # plt.xlabel('num_samples | Wasserstein')
        # plt.ylabel('NeuralNet Distance')
        # plt.grid(True)
        # plt.show()

        # # Create a box plot
        # plt.figure(figsize=(10, 6))
        # df.boxplot()
        # plt.title("Box Plot of Different Methods to calculate Distance")
        # plt.ylabel("Distance")
        # plt.xticks(rotation=45)
        # plt.grid(True)

        # plt.savefig(
        #     file_path,
        #     bbox_inches="tight",
        #     format="pdf",
        # )


def plot_power_over_number_of_sequences(
    base_model_name: str,
    base_model_seed: str,
    seeds: List[str],
    checkpoints: Optional[List[str]] = None,
    model_names: Optional[Union[str, List[str]]] = None,
    checkpoint_base_name: str = "LLama-3-8B-ckpt",
    group_by: str = "Checkpoint",
    marker: str = "X",
    save_as_pdf: bool = True,
    test_dir: str = "test_outputs",
    plot_dir: str = "plots",
    metric: str = "perspective",
    only_continuations=True,
    fold_size: int = 4000,
    epsilon: Union[float, List[float]] = 0,
):
    script_dir = os.path.dirname(__file__)

    # Construct the absolute path to "test_outputs"
    test_dir = os.path.join(script_dir, "..", test_dir)
    plot_dir = os.path.join(script_dir, "..", plot_dir)

    use_models = model_names is not None

    if group_by == "Checkpoint" or use_models:
        if use_models:
            result_df = get_power_over_sequences(
                base_model_name,
                base_model_seed,
                seeds,
                model_names=model_names,
                only_continuations=only_continuations,
                fold_size=fold_size,
                epsilon=epsilon,
            )
        else:
            result_df = get_power_over_sequences(
                base_model_name,
                base_model_seed,
                seeds=seeds,
                checkpoints=checkpoints,
                checkpoint_base_name=checkpoint_base_name,
                only_continuations=only_continuations,
                fold_size=fold_size,
                epsilon=epsilon,
            )
    elif group_by == "Rank based on Wasserstein Distance" or group_by == "Empirical Wasserstein Distance":
        result_df = get_power_over_sequences_for_ranked_checkpoints(
            base_model_name,
            base_model_seed,
            seeds,
            checkpoints,
            checkpoint_base_name=checkpoint_base_name,
            metric="perspective",
            only_continuations=only_continuations,
            fold_size=fold_size,
            epsilon=epsilon,
        )

        result_df["Empirical Wasserstein Distance"] = result_df["Empirical Wasserstein Distance"].round(3)

    # Create the plot
    plt.figure(figsize=(12, 6))
    if not use_models:
        unique_groups = result_df[group_by].unique()
        num_groups = len(unique_groups)
    else:
        num_groups = len(model_names)

    palette = sns.color_palette("viridis", num_groups)
    palette = palette[::-1]

    sns.lineplot(
        data=result_df,
        x="Samples",
        y="Power",
        hue=group_by if not group_by == "model" else "model_name2",
        marker=marker,
        markersize=10,
        palette=palette,
    )

    plt.xticks(fontsize=14)
    plt.yticks(fontsize=14)

    # Customize the plot
    plt.xlabel("samples", fontsize=16)
    plt.ylabel("detection frequency", fontsize=16)
    if group_by == "Checkpoint":
        title = "checkpoints"
    elif group_by == "Rank based on Wasserstein Distance":
        title = "rank"
    elif group_by == "Empirical Wasserstein Distance":
        title = "distance"
    elif group_by == "epsilon":
        title = "neural net distance"
    else:
        title = group_by
    plt.legend(
        title=title,
        loc="lower right",
        fontsize=14,
        title_fontsize=16,
        # bbox_to_anchor=(1, 1),
    )
    plt.grid(True, linewidth=0.5, color="#ddddee")

    # Making the box around the plot thicker
    plt.gca().spines["top"].set_linewidth(1.5)
    plt.gca().spines["right"].set_linewidth(1.5)
    plt.gca().spines["bottom"].set_linewidth(1.5)
    plt.gca().spines["left"].set_linewidth(1.5)

    if use_models:
        directory = f"{plot_dir}/{base_model_name}_{base_model_seed}_models"
        if not Path(directory).exists():
            Path(directory).mkdir(parents=True, exist_ok=True)
        else:
            for model_name in model_names:
                directory += f"_{model_name}"
            if not Path(directory).exists():
                Path(directory).mkdir(parents=True, exist_ok=True)

    else:
        directory = f"{plot_dir}/{base_model_name}_{base_model_seed}_{checkpoint_base_name}_checkpoints"
        if not Path(directory).exists():
            Path(directory).mkdir(parents=True, exist_ok=True)
        else:
            for seed in seeds:
                directory += f"_{seed}"
            if not Path(directory).exists():
                Path(directory).mkdir(parents=True, exist_ok=True)

    if save_as_pdf:
        if use_models:
            file_name = "power_over_number_of_sequences.pdf"
        else:
            file_name = f"power_over_number_of_sequences_grouped_by_{group_by}_{base_model_name}_{base_model_seed}.pdf"
        plt.savefig(
            f"{directory}/{file_name}",
            bbox_inches="tight",
            format="pdf",
        )
    else:
        if use_models:
            file_name = "power_over_number_of_sequences.png"
        else:
            file_name = f"power_over_number_of_sequences_grouped_by_{group_by}_{base_model_name}_{base_model_seed}.png"

        plt.savefig(
            f"{directory}/{file_name}",
            dpi=300,
            bbox_inches="tight",
        )


def plot_power_over_epsilon(
    base_model_name,
    base_model_seed,
    checkpoints,
    seeds,
    checkpoint_base_name="LLama-3-8B-ckpt",
    epoch1=0,
    epoch2=0,
    metric="toxicity",
    distance_measure="Wasserstein",
    fold_sizes: Union[int, List[int]] = [1000, 2000, 3000, 4000],
    marker="X",
    palette=["#E49B0F", "#C46210", "#B7410E", "#A81C07"],
    save_as_pdf=True,
    plot_dir: str = "plots",
    epsilon=0,
    only_continuations=True,
):
    """
    This plots power over distance measure, potentially for different fold_sizes and models.
    """

    script_dir = os.path.dirname(__file__)

    # Construct the absolute path to "test_outputs"
    plot_dir = os.path.join(script_dir, "..", plot_dir)

    if isinstance(fold_sizes, list):
        result_df = get_power_over_sequences_for_ranked_checkpoints_wrapper(
            base_model_name,
            base_model_seed,
            checkpoints,
            seeds,
            checkpoint_base_name=checkpoint_base_name,
            metric=metric,
            distance_measure=distance_measure,
            fold_sizes=fold_sizes,
            epsilon=epsilon,
            only_continuations=only_continuations,
        )
    else:
        result_df = get_power_over_sequences_for_ranked_checkpoints(
            base_model_name,
            base_model_seed,
            checkpoints,
            seeds,
            checkpoint_base_name=checkpoint_base_name,
            metric=metric,
            distance_measure=distance_measure,
            epsilon=epsilon,
            only_continuatinos=only_continuations,
        )

    smaller_df = extract_power_from_sequence_df(result_df, distance_measure=distance_measure, by_checkpoints=True)

    # in case we have less folds
    palette = palette[-len(fold_sizes) :]

    plt.figure(figsize=(10, 6))

    pd.set_option("display.max_rows", 1000)
    pd.set_option("display.max_columns", 1000)
    pd.set_option("display.width", 1000)

    print(f"This is the smaller df inside plot_power_over_epsilon: {smaller_df} for fold_sizes {fold_sizes}")

    sns.lineplot(
        x=f"Empirical {distance_measure} Distance",
        y="Power",
        hue="Samples per Test" if "Samples per Test" in smaller_df.columns else None,
        # style="Samples per Test" if "Samples per Test" in smaller_df.columns else None,
        marker=marker,
        data=smaller_df,
        markersize=10,
        palette=palette,
    )

    # plt.xlabel(f"{distance_measure.lower()} distance", fontsize=14)
    plt.xlabel(f"distance to aligned model", fontsize=16)
    plt.ylabel("detection frequency", fontsize=16)
    plt.grid(True, linewidth=0.5, color="#ddddee")
    plt.xticks(fontsize=14)
    plt.yticks(fontsize=14)

    plt.legend(
        title="samples per test",
        loc="lower right",
        fontsize=14,
        title_fontsize=16,
        # bbox_to_anchor=(
        #     1.05,
        #     1,
        # ),  # Adjusted position to ensure the legend is outside the plot area
    )

    # Make the surrounding box thicker
    ax = plt.gca()
    for spine in ax.spines.values():
        spine.set_linewidth(1.5)

    directory = f"{plot_dir}/{base_model_name}_{base_model_seed}_{checkpoint_base_name}_checkpoints"
    if not Path(directory).exists():
        Path(directory).mkdir(parents=True, exist_ok=True)
    else:
        for seed in seeds:
            directory += f"_{seed}"
        if not Path(directory).exists():
            Path(directory).mkdir(parents=True, exist_ok=True)

    if "Samples per Test" in smaller_df.columns:
        if save_as_pdf:
            plt.savefig(
                f"{directory}/power_over_{distance_measure.lower()}_distance_grouped_by_fold_size_{base_model_name}_{base_model_seed}.pdf",
                bbox_inches="tight",
                format="pdf",
            )

        else:
            plt.savefig(
                f"{directory}/power_over_{distance_measure.lower()}_distance_grouped_by_fold_size_{base_model_name}_{base_model_seed}.png",
                dpi=300,
                bbox_inches="tight",
            )
    else:
        if save_as_pdf:
            plt.savefig(
                f"{directory}/power_over_{distance_measure.lower()}_distance_{base_model_name}_{base_model_seed}.pdf",
                bbox_inches="tight",
                format="pdf",
            )
        else:
            plt.savefig(
                f"{directory}/power_over_{distance_measure.lower()}_distance_{base_model_name}_{base_model_seed}.png",
                dpi=300,
                bbox_inches="tight",
            )

    plt.figure(figsize=(10, 6))
    sns.lineplot(
        x=f"Rank based on {distance_measure} Distance",
        y="Power",
        hue="Samples per Test" if "Samples per Test" in smaller_df.columns else None,
        # style="Samples per Test" if "Samples per Test" in smaller_df.columns else None,
        marker=marker,
        data=smaller_df,
        markersize=10,
        palette=palette,
    )
    # plt.xlabel(f"rank based on {distance_measure.lower()} distance", fontsize=14)
    plt.xlabel(f"rank based on distance to aligned model", fontsize=16)
    plt.ylabel("detection frequency", fontsize=16)
    plt.grid(True, linewidth=0.5, color="#ddddee")
    plt.xticks(fontsize=14)
    plt.yticks(fontsize=14)

    plt.legend(
        title="samples per test",
        loc="lower right",
        fontsize=14,
        title_fontsize=16,
        # bbox_to_anchor=(
        #     1.05,
        #     1,
        # ),  # Adjusted position to ensure the legend is outside the plot area
    )

    # Make the surrounding box thicker
    ax = plt.gca()
    for spine in ax.spines.values():
        spine.set_linewidth(1.5)

    directory = f"{plot_dir}/{base_model_name}_{base_model_seed}_{checkpoint_base_name}_checkpoints"
    if not Path(directory).exists():
        Path(directory).mkdir(parents=True, exist_ok=True)
    else:
        for seed in seeds:
            directory += f"_{seed}"
        if not Path(directory).exists():
            Path(directory).mkdir(parents=True, exist_ok=True)
    if "Samples per Test" in smaller_df.columns:
        if save_as_pdf:
            plt.savefig(
                f"{directory}/power_over_{distance_measure.lower()}_rank_grouped_by_fold_size_{base_model_name}_{base_model_seed}.pdf",
                bbox_inches="tight",
                format="pdf",
            )
        else:
            plt.savefig(
                f"{directory}/power_over_{distance_measure.lower()}_rank_grouped_by_fold_size_{base_model_name}_{base_model_seed}.png",
                dpi=300,
                bbox_inches="tight",
            )
    else:
        if save_as_pdf:
            plt.savefig(
                f"{directory}/power_over_{distance_measure.lower()}_rank_{base_model_name}_{base_model_seed}.pdf",
                bbox_inches="tight",
                format="pdf",
            )
        else:
            plt.savefig(
                f"{directory}/power_over_{distance_measure.lower()}_rank_{base_model_name}_{base_model_seed}.png",
                dpi=300,
                bbox_inches="tight",
            )


def plot_alpha_over_sequences(
    model_names,
    seeds1,
    seeds2,
    save_as_pdf=True,
    markers=["X", "o", "s"],
    palette=["#94D2BD", "#EE9B00", "#BB3E03"],
    fold_size=4000,
    plot_dir: str = "plots",
    epsilon: float = 0,
    only_continuations=True,
):
    script_dir = os.path.dirname(__file__)

    # Construct the absolute path to "test_outputs"
    plot_dir = os.path.join(script_dir, "..", plot_dir)

    result_df = get_alpha_wrapper(
        model_names, seeds1, seeds2, fold_size=fold_size, epsilon=epsilon, only_continuations=only_continuations
    )
    group_by_model = "model_id" in result_df.columns

    # Create the plot
    plt.figure(figsize=(12, 6))

    if group_by_model:
        unique_models = result_df["model_id"].unique()
        for i, model in enumerate(unique_models):
            sns.lineplot(
                data=result_df[result_df["model_id"] == model],
                x="Samples",
                y="Power",
                marker=markers[i % len(markers)],
                dashes=False,  # No dashes, solid lines
                color=palette[i % len(palette)],
                label=model,
            )
    else:
        sns.lineplot(
            data=result_df,
            x="Samples",
            y="Power",
            marker="o",
            dashes=False,  # No dashes, solid lines
            color="black",
        )

    # Customize the plot
    plt.xlabel("samples", fontsize=16)
    plt.ylabel("false positive rate", fontsize=16)
    plt.xticks(fontsize=14)
    plt.yticks(fontsize=14)
    # plt.tight_layout(rect=[0, 0, 0.85, 1])

    # Adjust the spines (box) thickness
    ax = plt.gca()
    ax.spines["top"].set_linewidth(1.5)
    ax.spines["right"].set_linewidth(1.5)
    ax.spines["bottom"].set_linewidth(1.5)
    ax.spines["left"].set_linewidth(1.5)

    if group_by_model:
        plt.legend(
            title="models",
            # loc="lower right",
            loc="lower right",
            fontsize=14,
            title_fontsize=16,
            # bbox_to_anchor=(
            #     1.05,
            #     1,
            # ),  # Adjusted position to ensure the legend is outside the plot area
        )
    plt.grid(True, linewidth=0.5, color="#ddddee")

    directory = f"{plot_dir}/alpha_plots"
    if not Path(directory).exists():
        Path(directory).mkdir(parents=True, exist_ok=True)
    fig_path = f"{directory}/alpha_error_over_number_of_sequences"
    if isinstance(model_names, str):
        fig_path += f"_{model_names}"
    elif isinstance(model_names, list):
        for model_name in model_names:
            fig_path += f"_{model_name}"

    if save_as_pdf:
        fig_path += ".pdf"
        plt.savefig(fig_path, bbox_inches="tight", format="pdf")
    else:
        fig_path += ".png"
        plt.savefig(fig_path, dpi=300, bbox_inches="tight")


def plot_rejection_rate_matrix(
    model_names1,
    seeds1,
    model_names2: Optional[List[str]] = None,
    seeds2: Optional[List[str]] = None,
    fold_size=4000,
    distance_measure: Optional[str] = "Wasserstein",
    metric: Optional[str] = "toxicity",
    epoch1: Optional[int] = 0,
    epoch2: Optional[int] = 0,
    save_as_pdf: bool = True,
    plot_dir: str = "plots",
):
    """ """
    assert (model_names2 is None and seeds2 is None) or (
        model_names2 is not None and seeds2 is not None
    ), "Either give full list of test models or expect to iterate over all combinations"

    script_dir = os.path.dirname(__file__)

    # Construct the absolute path to "test_outputs"
    plot_dir = os.path.join(script_dir, "..", plot_dir)

    results_df = []
    if not model_names2:
        for i, (model_name1, seed1) in enumerate(zip(model_names1[:-1], seeds1[:-1])):
            for model_name2, seed2 in zip(model_names1[i + 1 :], seeds1[i + 1 :]):
                print(f"Checking model {model_name1}, {seed1} against {model_name2}, {seed2}")
                result_df = get_power_over_sequences_for_models_or_checkpoints(
                    model_name1,
                    seed1,
                    seed2,
                    model_name2=model_name2,
                    fold_size=fold_size,
                )
                if distance_measure:
                    dist = get_distance_scores(
                        model_name1,
                        seed1,
                        seed2,
                        model_name2=model_name2,
                        metric=metric,
                        distance_measure=distance_measure,
                        epoch1=epoch1,
                        epoch2=epoch2,
                    )
                    result_df[f"Empirical {distance_measure} Distance"] = dist
                small_df = extract_power_from_sequence_df(
                    result_df, distance_measure=distance_measure, by_checkpoints=False
                )

                results_df.append(small_df)

        results_df = pd.concat(results_df, ignore_index=True)

    pivot_table = results_df.pivot_table(values="Power", index="seed1", columns="seed2")

    # Create the heatmap
    plt.figure(figsize=(10, 8))
    heatmap = sns.heatmap(
        pivot_table,
        annot=True,
        cmap="viridis",
        cbar_kws={"label": "Frequency of Positive Test Result"},
    )
    heatmap.set_title(f"Positive Test Rates for model {model_names1[0]}")

    directory = f"{plot_dir}/power_heatmaps"
    if not Path(directory).exists():
        Path(directory).mkdir(parents=True, exist_ok=True)
    file_name = "power_heatmap"
    for model_name, seed in zip(model_names1, seeds1):
        file_name += f"_{model_name}_{seed}"

    if save_as_pdf:
        file_name += ".pdf"
        output_path = os.path.join(directory, file_name)
        plt.savefig(output_path, format="pdf", bbox_inches="tight")
    else:
        file_name += ".png"
        output_path = os.path.join(directory, file_name)
        plt.savefig(output_path, dpi=300, bbox_inches="tight")

    if distance_measure:
        distance_pivot_table = results_df.pivot_table(
            values=f"Empirical {distance_measure} Distance",
            index="seed1",
            columns="seed2",
        )

        # Create the heatmap
        plt.figure(figsize=(10, 8))
        heatmap = sns.heatmap(
            distance_pivot_table,
            annot=True,
            cmap="viridis",
            cbar_kws={"label": "Distance"},
        )
        heatmap.set_title(f"Distance Heatmap for model {model_names1[0]}")

        directory = f"{plot_dir}/power_heatmaps"
        if not Path(directory).exists():
            Path(directory).mkdir(parents=True, exist_ok=True)

        file_name = "distance_heatmap"
        for model_name, seed in zip(model_names1, seeds1):
            file_name += f"_{model_name}_{seed}"

        if save_as_pdf:
            file_name += ".pdf"
            output_path = os.path.join(directory, file_name)
            plt.savefig(output_path, format="pdf", bbox_inches="tight")
        else:
            file_name += ".png"
            output_path = os.path.join(directory, file_name)
            plt.savefig(output_path, dpi=300, bbox_inches="tight")


def plot_calibrated_detection_rate(
    model_name1: str,
    seed1: str,
    model_name2: str,
    seed2: str,
    true_epsilon: Optional[float] = None,
    std_epsilon: Optional[float] = None,
    result_file: Optional[Union[str, Path]] = None,
    num_runs: int = 20,
    multiples_of_epsilon: Optional[int] = None,
    test_dir: str = "test_outputs",
    save_as_pdf: bool = True,
    overwrite: bool = False,
    draw_in_std: bool = False,
    fold_size: int = 4000,
    bs: int = 100,
    only_continuations=True,
):
    """ """

    script_dir = os.path.dirname(__file__)
    test_dir = os.path.join(script_dir, "..", test_dir)
    plot_dir = os.path.join(test_dir, f"{model_name1}_{seed1}_{model_name2}_{seed2}")

    if result_file is not None:
        result_file_path = Path(result_file)
    else:
        multiples_str = f"_{multiples_of_epsilon}" if multiples_of_epsilon else ""
        continuations_str = "_continuations" if only_continuations else ""
        result_file_path = os.path.join(
            plot_dir,
            f"power_over_epsilon{continuations_str}_{fold_size-bs}_{num_runs}{multiples_str}.csv",
        )

    if not true_epsilon:
        distance_path = os.path.join(plot_dir, f"distance_scores_{fold_size-bs}_{num_runs}.csv")
        try:
            distance_df = pd.read_csv(distance_path)
            true_epsilon, std_epsilon = get_mean_and_std_for_nn_distance(distance_df)
            logger.info(
                f"True epsilon for {model_name1}_{seed1} and {model_name2}_{seed2}: {true_epsilon}, std epsilon: {std_epsilon}"
            )
        except FileNotFoundError:
            logger.error(f"Could not find file at {distance_path}")
            sys.exit(1)

    df = pd.read_csv(result_file_path)
    df_sorted = df.sort_values(by="epsilon")

    # Plotting
    plt.figure(figsize=(8, 6))
    plt.plot(
        df_sorted["epsilon"],
        df_sorted["power"],
        marker="o",
        label="Detection Rate over Epsilon",
    )

    # Adding the vertical line
    plt.axvline(x=true_epsilon, color="red", linestyle="--", label="Fine-Tuned Model Distance")

    # Adding the standard deviation range
    if draw_in_std:
        plt.axvspan(
            true_epsilon - std_epsilon, true_epsilon + std_epsilon, alpha=0.2, color="green", label="Std Dev Range"
        )

    # Adding label to the vertical line
    plt.text(
        true_epsilon + 0.0001,
        0.3,  # Changed from 0.5 to 0.1 to move the label lower
        "Fine-Tuned Model Distance",
        color="red",
        verticalalignment="center",
        rotation=90,  # Added rotation for better readability
    )

    ax = plt.gca()
    for spine in ax.spines.values():
        spine.set_linewidth(1.5)

    plot_path = os.path.join(plot_dir, f"calibrated_detection_rate_{fold_size}.pdf")

    if not overwrite and Path(plot_path).exists():
        logger.info(f"File already exists at {plot_path}. Skipping...")

    else:
        # Titles and labels
        plt.title("Detection Rate vs Epsilon")
        plt.xlabel("Test Epsilon")
        plt.ylabel("\% of Tests That Detect Changed Model")
        plt.legend()
        plt.grid(True)
        plt.savefig(plot_path, format="pdf", bbox_inches="tight")


def plot_multiple_calibrated_detection_rates(
    model_names: List[str],
    seeds: List[str],
    true_epsilon: Optional[List[float]] = None,
    base_model: str = "Meta-Llama-3-8B-Instruct",
    base_seed: str = "seed1000",
    num_runs: int = 20,
    multiples_of_epsilon: Optional[int] = None,
    test_dir: str = "test_outputs",
    save_as_pdf: bool = True,
    overwrite: bool = False,
    draw_in_std: bool = False,
    fold_size: int = 4000,
    bs: int = 100,
    only_continuations=True,
):
    """
    Plot calibrated detection rates for multiple models on the same graph using seaborn lineplot.
    """
    script_dir = os.path.dirname(__file__)
    test_dir = os.path.join(script_dir, "..", test_dir)
    plot_dir = os.path.join(script_dir, "..", "plots")

    plt.figure(figsize=(18, 8))

    custom_palette = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]

    # Prepare data for seaborn plotting
    all_data = []

    for i, (model_name, seed) in enumerate(zip(model_names, seeds)):
        result_dir = os.path.join(test_dir, f"{base_model}_{base_seed}_{model_name}_{seed}")
        multiples_str = f"_{multiples_of_epsilon}" if multiples_of_epsilon else ""
        continuations_str = "_continuations" if only_continuations else ""
        result_file_path = os.path.join(
            result_dir,
            f"power_over_epsilon{continuations_str}_{fold_size-bs}_{num_runs}{multiples_str}.csv",
        )
        distance_path = os.path.join(result_dir, f"distance_scores_{fold_size-bs}_{num_runs}.csv")

        try:
            distance_df = pd.read_csv(distance_path)
            true_epsilon, std_epsilon = get_mean_and_std_for_nn_distance(distance_df)
            logger.info(f"True epsilon for {model_name}_{seed}: {true_epsilon}, std epsilon: {std_epsilon}")
        except FileNotFoundError:
            logger.error(f"Could not find file at {distance_path}")
            continue

        try:
            df = pd.read_csv(result_file_path)
            df_sorted = df.sort_values(by="epsilon")

            # Use task cluster name for the legend
            task_cluster_name = ", ".join(TASK_CLUSTER[i]) if i < len(TASK_CLUSTER) else f"Model {i+1}"

            df_sorted["Model"] = task_cluster_name
            all_data.append(df_sorted)
        except FileNotFoundError:
            logger.error(f"Could not find file for {model_name}_{seed}")

    # Combine all data
    combined_data = pd.concat(all_data, ignore_index=True)

    # Plot using seaborn
    ax = sns.lineplot(
        data=combined_data,
        x="epsilon",
        y="power",
        hue="Model",
        marker="x",
        markersize=12,
        linewidth=2.5,
        palette=custom_palette,
    )

    # Ensure markers are visible
    for line in ax.lines:
        line.set_markerfacecolor(line.get_color())
        line.set_markeredgecolor(line.get_color())

    plt.xticks(fontsize=14)
    plt.yticks(fontsize=14)

    ax.yaxis.set_major_locator(MultipleLocator(0.1))
    ax.yaxis.set_minor_locator(MultipleLocator(0.05))

    # Add vertical lines for true epsilon
    for i, (model_name, seed) in enumerate(zip(model_names, seeds)):
        true_epsilon, _ = get_mean_and_std_for_nn_distance(
            pd.read_csv(
                os.path.join(
                    test_dir,
                    f"{base_model}_{base_seed}_{model_name}_{seed}",
                    f"distance_scores_{fold_size-bs}_{num_runs}.csv",
                )
            )
        )
        plt.axvline(x=true_epsilon, linestyle="--", color=sns.color_palette()[i], alpha=0.7, linewidth=2)

    # Titles and labels
    plt.xlabel("Test Epsilon", fontsize=24)
    plt.ylabel("% of Tests That Detect Changed Model", fontsize=24)

    handles, labels = ax.get_legend_handles_labels()
    wrapped_labels = [textwrap.fill(label, width=40) for label in labels]  # Adjust width as needed
    legend = plt.legend(
        handles,
        wrapped_labels,
        title="models fine-tuned on ...",
        loc="lower left",
        fontsize=12,
        title_fontsize=14,
        bbox_to_anchor=(0.02, 0.02),  # Adjust these values to fine-tune position
        ncol=1,
        frameon=True,
        fancybox=True,
        shadow=True,
        borderaxespad=0.0,
    )

    # Move legend to bottom left corner
    # plt.legend(
    #     # title="models fine-tuned on ...",
    #     loc="lower left",
    #     # title_fontsize=18,
    #     fontsize=14,
    #     bbox_to_anchor=(0, 0),
    #     ncol=1,
    # )

    legend.get_frame().set_alpha(0.8)
    legend.get_frame().set_edgecolor("gray")

    # Make the grid less noticeable
    plt.grid(True, color="#ddddee", linewidth=0.5)

    # Add a box around the plot
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1.5)

    # Save the plot
    plot_path = os.path.join(plot_dir, f"multi_model_calibrated_detection_rate_{fold_size}.pdf")
    if not overwrite and Path(plot_path).exists():
        logger.info(f"File already exists at {plot_path}. Skipping...")
    else:
        logger.info(f"Saving plot to {plot_path}")
        plt.tight_layout()
        plt.savefig(plot_path, format="pdf", bbox_inches="tight")


def darken_color(color, factor=0.7):
    """
    Darken the given color by multiplying RGB values by the factor.
    """
    h, l, s = colorsys.rgb_to_hls(*color)
    return colorsys.hls_to_rgb(h, max(0, min(1, l * factor)), s)


def plot_scores(
    model_name,
    seed,
    metric="toxicity",
    save=True,
    epoch=0,
    use_log_scale=True,
    color="blue",
    save_as_pdf=True,
    plot_dir: str = "plots",
):
    """ """
    script_dir = os.path.dirname(__file__)

    # Construct the absolute path to "test_outputs"
    plot_dir = os.path.join(script_dir, "..", plot_dir)

    directory = f"{plot_dir}/{model_name}_{seed}"
    file_path = f"{directory}/{metric}_scores.json"
    with open(file_path, "r") as f:
        data = json.load(f)

    scores = data[str(epoch)][f"{metric}_scores"]
    # Calculate statistics
    mean_score = np.mean(scores)
    array_scores = np.array(scores)

    # Calculate the skewness using scipy.stats.skew
    skewness = skew(array_scores)
    print(f"skewness: {skewness:.3f}")

    plt.figure(figsize=(14, 7))

    # Plot histogram with adjusted bins and density plot
    sns.histplot(
        scores,
        bins=50,
        # kde=True,
        color=color,
        edgecolor=None,
        alpha=0.7,
    )

    # # Add mean and std lines
    plt.axvline(
        mean_score,
        color="red",
        linestyle="--",
        linewidth=1.5,
        label=f"Mean: {mean_score:.3f}",
        # label_size=16,
    )
    # plt.axvline(
    #     mean_score + std_score,
    #     color="green",
    #     linestyle="--",
    #     linewidth=1.5,
    #     label=f"+1 Std Dev: {mean_score + std_score:.2f}",
    # )
    # plt.axvline(
    #     mean_score - std_score,
    #     color="green",
    #     linestyle="--",
    #     linewidth=1.5,
    #     label=f"-1 Std Dev: {mean_score - std_score:.2f}",
    # )

    # Set plot limits
    plt.xlim(0, 1)
    if use_log_scale:
        plt.yscale("log")

    # plt.title(
    #     f"Distribution of {metric.capitalize()} Scores for {model_name} (Seed: {seed})",
    #     fontsize=16,
    # )
    plt.xlabel(f"{metric.lower()} score", fontsize=16)
    plt.ylabel("log frequency" if use_log_scale else "frequency", fontsize=16)
    plt.xticks(fontsize=14)
    plt.yticks(fontsize=14)
    plt.legend(fontsize=16)

    plt.gca().xaxis.set_major_locator(MultipleLocator(0.1))
    plt.gca().xaxis.set_minor_locator(MultipleLocator(0.05))
    plt.grid(True, "minor", color="#ddddee")

    if save:
        if use_log_scale:
            if save_as_pdf:
                output_path = os.path.join(directory, f"{metric}_scores_{model_name}_{seed}_log.pdf")
                plt.savefig(output_path, format="pdf", bbox_inches="tight")
            else:
                output_path = os.path.join(directory, f"{metric}_scores_{model_name}_{seed}_log.png")
                plt.savefig(output_path, dpi=300, bbox_inches="tight")
        else:
            if save_as_pdf:
                output_path = os.path.join(directory, f"{metric}_scores_{model_name}_{seed}.pdf")
                plt.savefig(output_path, format="pdf", bbox_inches="tight")
            else:
                output_path = os.path.join(directory, f"{metric}_{model_name}_{seed}_scores.png")
                plt.savefig(output_path, dpi=300, bbox_inches="tight")
    else:
        plt.show()

    plt.close()


def plot_scores_base_most_extreme(
    base_model_name,
    base_model_seed,
    checkpoints,
    checkpoint_seeds,
    checkpoint_base_name,
    save=True,
    use_log_scale=True,
    metric="toxicity",
    base_model_epoch=0,
    epochs=None,
    color="blue",
    darker_color="blue",
    dark=False,
    corrupted_color="red",
    darker_corrupted_color="red",
    save_as_pdf=True,
    plot_dir: str = "plots",
):
    if not epochs:
        epochs = [0 for i in checkpoints]

    script_dir = os.path.dirname(__file__)

    # Construct the absolute path to "test_outputs"
    plot_dir = os.path.join(script_dir, "..", plot_dir)

    directory = f"{plot_dir}/{base_model_name}_{base_model_seed}"
    file_path = f"{directory}/{metric}_scores.json"
    print(f"This is the original model: {file_path}")
    with open(file_path, "r") as f:
        data = json.load(f)

    scores = data[str(base_model_epoch)][f"{metric}_scores"]
    scores_dict = {}
    wasserstein_distances = {}

    for ckpt, seed, epoch in zip(checkpoints, checkpoint_seeds, epochs):
        checkpoint_directory = f"{plot_dir}/{checkpoint_base_name}{ckpt}_{seed}"
        file_path = f"{checkpoint_directory}/{metric}_scores.json"
        with open(file_path, "r") as f:
            checkpoint_data = json.load(f)
            scores_ckpt = checkpoint_data[str(epoch)][f"{metric}_scores"]
            scores_dict[(ckpt, seed, epoch)] = scores_ckpt
            wasserstein_distances[(ckpt, seed, epoch)] = empirical_wasserstein_distance_p1(scores, scores_ckpt)

    max_distance_ckpt, max_distance_seed, max_distance_epoch = max(wasserstein_distances, key=wasserstein_distances.get)
    print(f"This is the max distance checkpoint: {max_distance_ckpt} with seed: {max_distance_seed}")
    max_distance = wasserstein_distances[(max_distance_ckpt, max_distance_seed, max_distance_epoch)]
    print(f"This is the max distance: {max_distance:.4f}")

    ckpt_scores = scores_dict[(max_distance_ckpt, max_distance_seed, max_distance_epoch)]

    array_ckpt_scores = np.array(ckpt_scores)
    skewness = skew(array_ckpt_scores)
    print(f"skewness for model {checkpoint_base_name}{max_distance_ckpt}: {skewness:.3f}")

    print(
        f"This is the max score of the base model {base_model_name}: {max(scores)} and this is the max score of the corrupted model {max(ckpt_scores)}"
    )

    df = pd.DataFrame(
        {
            "scores": scores + ckpt_scores,
            "model": [base_model_name] * len(scores) + [f"Checkpoint {max_distance_ckpt}"] * len(ckpt_scores),
            "seed": [base_model_seed] * len(scores) + [max_distance_seed] * len(ckpt_scores),
        }
    )

    mean_score = np.mean(scores)
    mean_ckpt_score = np.mean(ckpt_scores)

    plt.figure(figsize=(14, 7))

    sns.histplot(
        data=df,
        x="scores",
        hue="model",
        bins=50,
        edgecolor=None,
        alpha=0.7,
        # palette=[color, "#CD5C5C"],
        palette=[color, corrupted_color],
    )

    plt.axvline(
        mean_score,
        color=darker_color,
        linestyle="--",
        linewidth=1.5,
        label=f"aligned model mean {metric.lower()}: {mean_score:.3f}",
        # label_size=16,
    )

    plt.axvline(
        mean_ckpt_score,
        color=darker_corrupted_color,
        linestyle="--",
        linewidth=1.5,
        label=f"corrupted model mean {metric.lower()}: {mean_ckpt_score:.3f}",
        # label_size=16,
    )

    plt.xlim(0, 1)
    if use_log_scale:
        plt.yscale("log")

    plt.xlabel(f"{metric.lower()} score", fontsize=16)
    plt.ylabel("log frequency" if use_log_scale else "frequency", fontsize=16)
    plt.xticks(fontsize=14)
    plt.yticks(fontsize=14)
    plt.legend(fontsize=14)

    plt.gca().xaxis.set_major_locator(MultipleLocator(0.1))
    plt.gca().xaxis.set_minor_locator(MultipleLocator(0.05))
    plt.grid(True, "minor", color="#ddddee")

    if save:
        if use_log_scale:
            if save_as_pdf:
                output_path = os.path.join(
                    directory,
                    f"{metric}_scores_{base_model_name}_{base_model_seed}_checkpoint{max_distance_ckpt}_{max_distance:.3f}_log.pdf",
                )
                plt.savefig(output_path, bbox_inches="tight", format="pdf")
            else:
                output_path = os.path.join(
                    directory,
                    f"{metric}_scores_{base_model_name}_{base_model_seed}_checkpoint{max_distance_ckpt}_{max_distance:.3f}_log.png",
                )
                plt.savefig(output_path, dpi=300, bbox_inches="tight")
        else:
            if save_as_pdf:
                output_path = os.path.join(
                    directory,
                    f"{metric}_scores_{base_model_name}_{base_model_seed}_checkpoint{max_distance_ckpt}_{max_distance:.3f}.pdf",
                )
                plt.savefig(output_path, bbox_inches="tight", format="pdf")
            else:
                output_path = os.path.join(
                    directory,
                    f"{metric}_scores_{base_model_name}_{base_model_seed}_checkpoint{max_distance_ckpt}_{max_distance:.3f}.png",
                )
                plt.savefig(output_path, dpi=300, bbox_inches="tight")
    else:
        plt.show()

    plt.close()


def plot_scores_two_models(
    model_name1,
    seed1,
    model_name2,
    seed2,
    save=True,
    use_log_scale=True,
    metric="toxicity",
    epoch1=0,
    epoch2=0,
    color="blue",
    darker_color="blue",
    dark=False,
    corrupted_color="red",
    darker_corrupted_color="red",
    save_as_pdf=True,
    score_dir: str = "model_scores",
    plot_dir: str = "plots",
):
    script_dir = os.path.dirname(__file__)

    # Construct the absolute path to "test_outputs"
    score_dir = os.path.join(script_dir, "..", score_dir)
    plot_dir = os.path.join(script_dir, "..", plot_dir)

    score_dir1 = f"{score_dir}/{model_name1}_{seed1}"
    score_path1 = f"{score_dir1}/{metric}_scores.json"
    score_dir2 = f"{score_dir}/{model_name2}_{seed2}"
    score_path2 = f"{score_dir2}/{metric}_scores.json"
    save_dir1 = f"{plot_dir}/{model_name1}_{seed1}"
    save_dir2 = f"{plot_dir}/{model_name2}_{seed2}"

    with open(score_path1, "r") as f:
        data1 = json.load(f)

    with open(score_path2, "r") as f:
        data2 = json.load(f)

    scores1 = data1[str(epoch1)][f"{metric}_scores"]
    scores2 = data2[str(epoch2)][f"{metric}_scores"]

    dist = empirical_wasserstein_distance_p1(scores1, scores2)

    print(f"This is the distance: {dist} between {model_name1}, {seed1} and {model_name2}, {seed2}")
    skewness1 = skew(scores1)
    skewness2 = skew(scores2)
    print(f"skewness for model {model_name1}, {seed1}: {skewness1:.3f}")
    print(f"skewness for model {model_name2}, {seed2}: {skewness2:.3f}")

    df = pd.DataFrame(
        {
            "scores": scores1 + scores2,
            "model 1": [f"{model_name1}_{seed1}"] * len(scores1) + [f"{model_name2}_{seed2}"] * len(scores2),
        }
    )

    mean_score1 = np.mean(scores1)
    mean_score2 = np.mean(scores2)

    plt.figure(figsize=(14, 7))

    sns.histplot(
        data=df,
        x="scores",
        hue="model 1",
        bins=50,
        edgecolor=None,
        alpha=0.7,
        # palette=[color, "#CD5C5C"],
        palette=[color, corrupted_color],
    )

    plt.axvline(
        mean_score1,
        color=darker_color,
        linestyle="--",
        linewidth=1.5,
        label=f"model 1 mean {metric.lower()}: {mean_score1:.3f}",
        # label_size=16,
    )

    plt.axvline(
        mean_score2,
        color=darker_corrupted_color,
        linestyle="--",
        linewidth=1.5,
        label=f"model 2 mean {metric.lower()}: {mean_score2:.3f}",
        # label_size=16,
    )

    plt.xlim(0, 1)
    if use_log_scale:
        plt.yscale("log")

    plt.xlabel(f"{metric.lower()} score", fontsize=16)
    plt.ylabel("log frequency" if use_log_scale else "frequency", fontsize=16)
    plt.xticks(fontsize=14)
    plt.yticks(fontsize=14)
    plt.legend(fontsize=14)

    plt.gca().xaxis.set_major_locator(MultipleLocator(0.1))
    plt.gca().xaxis.set_minor_locator(MultipleLocator(0.05))
    plt.grid(True, "minor", color="#ddddee")

    if save:
        if use_log_scale:
            if save_as_pdf:
                output_path = os.path.join(
                    save_dir1,
                    f"{metric}_scores_{model_name1}_{seed1}_{model_name2}_{seed2}_log.pdf",
                )
                plt.savefig(output_path, bbox_inches="tight", format="pdf")
            else:
                output_path = os.path.join(
                    save_dir1,
                    f"{metric}_scores_{model_name1}_{seed1}_{model_name2}_{seed2}_log.png",
                )
                plt.savefig(output_path, dpi=300, bbox_inches="tight")
        else:
            if save_as_pdf:
                output_path = os.path.join(
                    save_dir1,
                    f"{metric}_scores_{model_name1}_{seed1}_{model_name2}_{seed2}.pdf",
                )
                plt.savefig(output_path, bbox_inches="tight", format="pdf")
            else:
                output_path = os.path.join(
                    save_dir1,
                    f"{metric}_scores_{model_name1}_{seed1}_{model_name2}_{seed2}.png",
                )
                plt.savefig(output_path, dpi=300, bbox_inches="tight")
    else:
        plt.show()

    plt.close()


@hydra.main(
    config_path=".",
    config_name="plotting_config.yml",
)
def plot_all(cfg: DictConfig):  # TODO: add alternative seeds
    base_models = []
    base_seeds = []
    base_seeds2 = []

    # Loop over all base models
    for bm in cfg.models:
        base_models.append(bm.name)
        base_seeds.append(bm.seed)
        base_seeds2.append(bm.seed2)

        checkpoints = [i for i in range(1, int(bm.checkpoint_range))]
        if "llama" in bm.name.lower():
            seeds = [
                "seed2000",
                "seed2000",
                "seed2000",
                "seed2000",
                "seed1000",
                "seed1000",
                "seed1000",
                "seed1000",
                "seed1000",
                "seed1000",
            ]
        else:
            seeds = ["seed1000" for i in range(1, int(bm.checkpoint_range))]

        # Create power plot over sequences:
        plot_power_over_number_of_sequences(
            bm.name,
            bm.seed,
            seeds,
            checkpoints,
            checkpoint_base_name=bm.checkpoint_base_name,
            fold_size=2000,
            group_by="Empirical Wasserstein Distance",
            only_continuations=True,  # TODO make this changeable
            marker=bm.marker,
        )

        # Create power plot over distance:
        plot_power_over_epsilon(
            bm.name,
            "seed1000",
            checkpoints,
            seeds,
            checkpoint_base_name=bm.checkpoint_base_name,
            fold_sizes=list(cfg.fold_sizes),
            marker=bm.marker,
            metric=cfg.metric,
            only_continuations=True,
        )

    plot_alpha_over_sequences(base_models, base_seeds, base_seeds2)


if __name__ == "__main__":
    model_name1 = "Meta-Llama-3-8B-Instruct"
    model_name2 = "1-Meta-Llama-3-8B-Instruct"
    model_name3 = "LLama-3-8b-Uncensored"
    seed1 = "seed1000"
    seed2 = "seed1000"
    metric = "perspective"
    epsilon1 = 0.0043025975821365135
    epsilon2 = 0.06611211877316236

    model_names = [
        "1-Meta-Llama-3-8B-Instruct",
        "2-Meta-Llama-3-8B-Instruct",
        "3-Meta-Llama-3-8B-Instruct",
        "4-Meta-Llama-3-8B-Instruct",
        "5-Meta-Llama-3-8B-Instruct",
    ]

    seeds = ["seed1000" for i in model_names]

    # net_cfg = load_config("config.yml")["net"]
    # train_cfg = TrainCfg()

    # num_samples = 10000
    # pre_shuffle = True

    # data = []

    # full_dict = []
    # for rand_val in range(10):
    #     dist_dict = get_distance_scores(
    #         model_name1,
    #         seed1,
    #         seed2,
    #         model_name2=model_name2,
    #         metric=metric,
    #         net_cfg=net_cfg,
    #         train_cfg=train_cfg,
    #         pre_shuffle=pre_shuffle,
    #         random_seed=rand_val,
    #         num_samples=num_samples,
    #     )
    #     full_dict.append(dist_dict)
    #     print(dist_dict)

    # # averages = {}
    # # for key in dist_dict.keys():
    # #     values = [d[key] for d in full_dict]
    # #     average = sum(values) / len(values)
    # #     averages[key] = average
    # # print(averages)

    # df = pd.DataFrame(full_dict)

    # # Create the plot
    # distance_box_plot(
    #     df,
    #     model_name1,
    #     seed1,
    #     seed2,
    #     model_name2,
    #     num_samples,
    #     metric="perspective",
    #     pre_shuffled=pre_shuffle,
    # )

    # for i in range(5):
    #     dist_dict = get_distance_scores(
    #         model_name1,
    #         seed1,
    #         seed2,
    #         model_name2=model_name2,
    #         metric=metric,
    #         net_cfg=net_cfg,
    #         train_cfg=train_cfg,
    #         pre_shuffle=pre_shuffle,
    #         random_seed=i,
    #         num_samples=num_samples,
    #     )
    #     data.append(dist_dict)

    # # Convert the list of dictionaries to a DataFrame
    # df = pd.DataFrame(data)

    # # Create the plot
    # distance_box_plot(
    #     df,
    #     model_name1,
    #     seed1,
    #     seed2,
    #     model_name2,
    #     num_samples,
    #     metric="perspective",
    #     pre_shuffled=pre_shuffle,
    # )

    # ns_data = []

    # nn = 0
    # ws = 0
    # nn_shuffled = 0
    # ws_scipy = 0

    # for i in range(1, 11):
    #     dist_dict = get_distance_scores(
    #         model_name1,
    #         seed1,
    #         seed2,
    #         model_name2=model_name2,
    #         metric=metric,
    #         net_cfg=net_cfg,
    #         train_cfg=train_cfg,
    #         distance_measures=["NeuralNet", "Wasserstein"],
    #         pre_shuffle=pre_shuffle,
    #         random_seed=i,
    #         num_samples=num_samples,
    #     )

    #     nn += dist_dict["NeuralNet"]
    #     nn_shuffled += dist_dict["NeuralNet_shuffled"]
    #     ws += dist_dict["Wasserstein"]
    #     ws_scipy += dist_dict["Wasserstein_scipy"]

    # ns_data.append(
    #     {
    #         # "num_samples": ns * 10000,
    #         "NeuralNet": nn / 10,
    #         "NeuralNet_shuffled": nn_shuffled / 10,
    #         "Wasserstein": ws / 10,
    #         "Wasserstein_scipy": ws_scipy / 10,
    #     }
    # )

    # ns_df = pd.DataFrame(ns_data)

    # # # Create the plot
    # distance_box_plot(
    #     ns_df,
    #     model_name1,
    #     seed1,
    #     seed2,
    #     model_name2,
    #     num_samples,
    #     metric="perspective",
    #     pre_shuffled=pre_shuffle,
    # )

    # Create the plot

    # Sample data generation for demonstration purposes (replace with your actual data collection code)
    # Melt the dataframe for easier plotting with seaborn
    # ns_df_melted = ns_df.melt(
    #     id_vars="num_samples",
    #     value_vars=[
    #         "NeuralNet",
    #         "NeuralNet_shuffled",
    #         "Wasserstein",
    #         "Wasserstein_scipy",
    #     ],
    #     var_name="Distance Type",
    #     value_name="Distance",
    # )

    # # Plotting with seaborn
    # plt.figure(figsize=(10, 6))
    # sns.lineplot(
    #     data=ns_df_melted,
    #     x="num_samples",
    #     y="Distance",
    #     hue="Distance Type",
    #     marker="o",
    # )

    # plt.xlabel("Number of Samples")
    # plt.ylabel("Distance")
    # plt.title("Distances over Number of Samples")
    # plt.grid(True)
    # plt.savefig(
    #     "Wasserstein_neural_net_distance_vs_num_samples.pdf",
    #     bbox_inches="tight",
    #     format="pdf",
    # )

    # ws_data = []

    # for ns in range(1, 101):
    #     wasserstein = 0
    #     wasserstein_scipy = 0
    #     for i in range(5, 10):
    #         dist_dict = get_distance_scores(
    #             model_name1,
    #             seed1,
    #             seed2,
    #             model_name2=model_name2,
    #             metric=metric,
    #             net_cfg=net_cfg,
    #             train_cfg=train_cfg,
    #             distance_measures=["Wasserstein"],
    #             random_seed=i,
    #             num_samples=ns * 1000,
    #         )

    #         wasserstein += dist_dict["Wasserstein"]
    #         wasserstein_scipy += dist_dict["Wasserstein_scipy"]

    #     ws_data.append(
    #         {
    #             "num_samples": ns * 1000,
    #             "Wasserstein": wasserstein / 5,
    #             "Wasserstein_scipy": wasserstein_scipy / 5,
    #         }
    #     )

    # ws_df = pd.DataFrame(ws_data)
    # ws_df.to_json("wasserstein_distance_vs_num_samples.json", orient="records")

    # # Create the plot
    # plt.figure(figsize=(10, 6))
    # plt.plot(ws_df["num_samples"], ws_df["Wasserstein"], marker="o")
    # plt.xlabel("Number of Samples")
    # plt.ylabel("Wasserstein Distance")
    # plt.title("Wasserstein Distance vs Number of Samples")
    # plt.grid(True)
    # plt.savefig(
    #     "wasserstein_distance_vs_num_samples.pdf", format="pdf", bbox_inches="tight"
    # )

    # res_df = get_power_over_sequences_for_models_or_checkpoints(
    #     model_name1, seed1, seed2, model_name2=model_name2, epsilon=0.02
    # )

    # power_df = extract_power_from_sequence_df(
    #     res_df, distance_measure=None, by_checkpoints=False
    # )
    # print(power_df)

    # plot_power_over_number_of_sequences(
    #     model_name1,
    #     seed1,
    #     [seed2, seed2],
    #     model_names=[model_name2, model_name2],
    #     epsilons=[epsilon1, epsilon2],
    #     group_by="epsilon",
    #     only_continuations=False,
    # )

    # plot_power_over_number_of_sequences(
    #     model_name1,
    #     seed1,
    #     [seed2, seed2],
    #     model_names=[model_name2, model_name3],
    #     epsilons=[epsilon1, epsilon1],
    #     group_by="model",
    #     only_continuations=True,
    # )

    # plot_all()

    # plot_calibrated_detection_rate(model_name1, seed1, model_name2, seed2, overwrite=True)

    plot_multiple_calibrated_detection_rates(model_names, seeds, overwrite=True)
