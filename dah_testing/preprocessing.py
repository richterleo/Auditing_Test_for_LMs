import json
import os
import typing
import random
import sys
import numpy as np
import wandb
import orjson  # Using orjson for faster JSON operations


from collections import defaultdict
from pathlib import Path
from typing import Optional
from tqdm import tqdm

# Add the parent directory of utils to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.utils import download_file_from_wandb, time_block, create_run_string
from utils.generate_and_evaluate import eval_on_metric


# def evaluate_single_model(
#     metric: str, run_id: Optional[str] = None, overwrite=True, asynchronously=True
# ):
#     """ """
#     file_path = f"outputs/{run_id}"
#     data = None

#     try:
#         for file_name in os.listdir(file_path):
#             if "continuations.json" in file_name:
#                 with open(os.path.join(file_path, file_name), "r") as file:
#                     data = json.load(file)
#                 break

#         if data is None:
#             raise FileNotFoundError

#     except FileNotFoundError:
#         file_path = download_file_from_wandb(
#             run_id=run_id,
#             project_name="continuations",
#             pattern="continuations",
#             return_file_path=True,
#         )
#         try:
#             with open(os.path.join(file_path), "r") as file:
#                 data = json.load(file)
#         except TypeError as e:
#             raise FileNotFoundError(f"No file found on wandb, error: {e}")

#     filtered_dict = {k: v for k, v in data.items() if k != "metadata"}

#     for epoch, d in filtered_dict.items():
#         concatenated_generations = [
#             f"{prompt} {continuation}"
#             for prompt, continuation in zip(d["prompts"], d["continuations"])
#         ]

#         # if we have a lot of generations, we need to query the API in batches
#         if len(concatenated_generations) > 100:
#             scores = []
#             for i in range(0, len(concatenated_generations), 100):
#                 print(f"Processing batch {i} to {i+100}")
#                 new_scores = eval_on_metric(
#                     metric,
#                     concatenated_generations[i : i + 100],
#                     asynchronously=asynchronously,
#                 )
#                 scores.extend(new_scores)

#             assert (
#                 len(scores) == len(concatenated_generations)
#             ), f"Did not get all scores: only {len(scores)} scores, but {len(concatenated_generations)} generations"
#         else:
#             scores = eval_on_metric(
#                 metric,
#                 concatenated_generations,
#                 asynchronously=asynchronously,
#             )

#         data[epoch][f"{metric}_scores"] = scores

#     file_path = os.path.join(file_path, f"{metric}_scores.json")
#     if overwrite or not os.path.exists(file_path):
#         with open(file_path, "w") as file:
#             json.dump(data, file, indent=4)


def evaluate_single_model(
    model_name: str,
    seed: str,
    metric,
    overwrite=True,
    asynchronously=True,
    use_wandb=True,
    entity="LLM_Accountability",
    save_intermittently=True
):
    """
    Evaluate a single model and save the scores in the same directory as the generations.
    """

    if use_wandb:
        wandb.init(
            project=f"{metric}_evaluation",
            entity=entity,
            name=create_run_string(),
            config={"model_name": model_name, "seed": seed},
        )

    gen_file_path = f"model_outputs/{model_name}_{seed}"
    scores_file_path = os.path.join(gen_file_path, f"{metric}_scores.json")
    if overwrite or not os.path.exists(scores_file_path):
        for file_name in os.listdir(gen_file_path):
            if "continuations" in file_name:
                with open(os.path.join(gen_file_path, file_name), "r") as file:
                    data = json.load(file)
                break

        if data is None:
            raise FileNotFoundError

        filtered_dict = {k: v for k, v in data.items() if k != "metadata"}

        for epoch, d in filtered_dict.items():
            concatenated_generations = [
                f"{prompt} {continuation}"
                for prompt, continuation in zip(d["prompts"], d["continuations"])
            ]
            
            data[epoch][f"{metric}_scores"] = []

            # if we have a lot of generations, we need to query the API in batches
            if len(concatenated_generations) > 100 and metric == "perspective":
                scores = []
                for i in range(0, len(concatenated_generations), 100):
                    print(f"Processing batch {i} to {i+100}")
                    new_scores = eval_on_metric(
                        metric,
                        concatenated_generations[i : i + 100],
                        asynchronously=asynchronously,
                    )
                    scores.extend(new_scores)

                assert (
                    len(scores) == len(concatenated_generations)
                ), f"Did not get all scores: only {len(scores)} scores, but {len(concatenated_generations)} generations"

            # tracking more to see why evaluations are so slow
            elif len(concatenated_generations) > 1000:
                scores = []
                for i in tqdm(range(0, len(concatenated_generations), 1000)):
                    print(f"Processing batch {i} to {i+1000}")
                    new_scores = eval_on_metric(
                        metric,
                        concatenated_generations,
                        asynchronously=asynchronously,
                    )
                    scores.extend(new_scores)
                    
                    if i % 10 == 0 and  save_intermittently:
                        current_scores_path = os.path.join(gen_file_path, f"{metric}_scores_{i}.json")
                        data[epoch][f"{metric}_scores"].extend(scores)
                        with open(current_scores_path, "w") as file:
                            json.dump(data, file, indent=4)
                            
                        if use_wandb:
                            wandb.save(current_scores_path)

            data[epoch][f"{metric}_scores"] = scores

        with open(scores_file_path, "w") as file:
            json.dump(data, file, indent=4)

        if use_wandb:
            wandb.save(scores_file_path)
            wandb.finish()


def create_common_json(
    model_name1,
    seed1,
    model_name2,
    seed2,
    metric,
    epoch=0,
    overwrite=True,
    use_wandb=True,
    entity="LLM_Accountability",
):
    """ """
    file_path1 = f"model_outputs/{model_name1}_{seed1}"
    file_path2 = f"model_outputs/{model_name2}_{seed2}"
    new_folder_path = (
        Path("model_outputs") / f"{model_name1}_{seed1}_{model_name2}_{seed2}"
    )

    if use_wandb:
        wandb.init(
            project=f"{metric}_evaluation",
            entity=entity,
            name=create_run_string(),
            config={
                "model_name1": model_name1,
                "seed": seed1,
                "model_name2": model_name2,
                "seed2": seed2,
            },
        )

    common_scores_file_path = new_folder_path / f"{metric}_scores.json"
    if overwrite or not common_scores_file_path.exists():
        if not new_folder_path.exists():
            new_folder_path.mkdir(parents=True, exist_ok=True)

        with open(
            os.path.join(file_path1, f"{metric}_scores.json"), "r"
        ) as file1, open(
            os.path.join(file_path2, f"{metric}_scores.json"), "r"
        ) as file2:
            data1 = json.load(file1)
            data2 = json.load(file2)

        data = defaultdict(list)
        data["metadata1"] = data1["metadata"]
        data["metadata2"] = data2["metadata"]

        filtered_data1 = {k: v for k, v in data1.items() if k != "metadata"}[str(epoch)]
        filtered_data2 = {k: v for k, v in data2.items() if k != "metadata"}[str(epoch)]

        common_prompts = list(
            set(filtered_data1["prompts"]) & set(filtered_data2["prompts"])
        )

        # Extract data for common prompts
        for prompt in common_prompts:
            data["prompts"].append(prompt)
            index1 = filtered_data1["prompts"].index(prompt)
            index2 = filtered_data2["prompts"].index(prompt)

            data["continuations1"].append(filtered_data1["continuations"][index1])
            data["continuations2"].append(filtered_data2["continuations"][index2])
            data[f"{metric}_scores1"].append(filtered_data1[f"{metric}_scores"][index1])
            data[f"{metric}_scores2"].append(filtered_data2[f"{metric}_scores"][index2])

            with open(common_scores_file_path, "w") as file:
                json.dump(data, file, indent=4)

        if use_wandb:
            wandb.save(common_scores_file_path)
            # wandb.finish()


def create_common_json_fast(
    model_name1, seed1, model_name2, seed2, metric, epoch=0, overwrite=True
):
    file_path1 = f"model_outputs/{model_name1}_{seed1}"
    file_path2 = f"model_outputs/{model_name2}_{seed2}"
    new_folder_path = (
        Path("model_outputs") / f"{model_name1}_{seed1}_{model_name2}_{seed2}"
    )

    new_folder_path.mkdir(parents=True, exist_ok=True)

    with open(os.path.join(file_path1, f"{metric}_scores.json"), "rb") as file1, open(
        os.path.join(file_path2, f"{metric}_scores.json"), "rb"
    ) as file2:
        data1 = orjson.loads(file1.read())
        data2 = orjson.loads(file2.read())

    data = defaultdict(list)
    data["metadata1"] = data1["metadata"]
    data["metadata2"] = data2["metadata"]

    filtered_data1 = data1.get(str(epoch), {})
    filtered_data2 = data2.get(str(epoch), {})

    common_prompts = set(filtered_data1.get("prompts", [])) & set(
        filtered_data2.get("prompts", [])
    )

    prompt_indices1 = {
        prompt: i for i, prompt in enumerate(filtered_data1.get("prompts", []))
    }
    prompt_indices2 = {
        prompt: i for i, prompt in enumerate(filtered_data2.get("prompts", []))
    }

    for prompt in common_prompts:
        data["prompts"].append(prompt)
        index1 = prompt_indices1[prompt]
        index2 = prompt_indices2[prompt]

        data["continuations1"].append(filtered_data1["continuations"][index1])
        data["continuations2"].append(filtered_data2["continuations"][index2])
        data[f"{metric}_scores1"].append(filtered_data1[f"{metric}_scores"][index1])
        data[f"{metric}_scores2"].append(filtered_data2[f"{metric}_scores"][index2])

    file_path = new_folder_path / f"{metric}_scores.json"
    if overwrite or not file_path.exists():
        with open(file_path, "wb") as file:
            file.write(orjson.dumps(data))


def create_folds(
    model_name1, seed1, model_name2, seed2, metric, fold_size=4000, overwrite=True
):
    """ """
    try:
        with open(
            f"model_outputs/{model_name1}_{seed1}_{model_name2}_{seed2}/{metric}_scores.json",
            "r",
        ) as file:
            data = json.load(file)

        # Extract metadata and other lists
        metadata1 = data["metadata1"]
        metadata2 = data["metadata2"]

        # Create batches
        total_num_samples = len(data["prompts"])
        indices = list(range(total_num_samples))
        random.shuffle(indices)
        index_batches = [
            indices[i : i + fold_size] for i in range(0, total_num_samples, fold_size)
        ]

        for i, batch in enumerate(index_batches):
            fold_file_path = f"model_outputs/{model_name1}_{seed1}_{model_name2}_{seed2}/{metric}_scores_fold_{i}.json"
            if overwrite or not os.path.exists(fold_file_path):
                print(f"We're in the {i}th fold now")
                fold_data = defaultdict(list)
                fold_data["metadata1"] = metadata1
                fold_data["metadata2"] = metadata2

                for key, value in data.items():
                    if key in ["prompts", "continuations1", "continuations2"]:
                        fold_data[key] = [value[j] for j in batch]
                    elif key in [f"{metric}_scores1", f"{metric}_scores2"]:
                        fold_data[key] = [value[j] for j in batch]

                with open(fold_file_path, "w") as file:
                    json.dump(fold_data, file, indent=4)

    except FileNotFoundError as e:
        print(f"File not found: {e}")
        return None


def create_folds_from_generations(
    model_name1, seed1, model_name2, seed2, metric, fold_size=4000, overwrite=True
):
    evaluate_single_model(model_name1, seed1, metric, overwrite=overwrite)
    evaluate_single_model(model_name2, seed2, metric, overwrite=overwrite)

    create_common_json(
        model_name1, seed1, model_name2, seed2, metric, overwrite=overwrite
    )
    create_folds(
        model_name1,
        seed1,
        model_name2,
        seed2,
        metric,
        fold_size=fold_size,
        overwrite=overwrite,
    )


def create_folds_from_evaluations(
    model_name1, seed1, model_name2, seed2, metric, fold_size=4000, overwrite=True
):
    create_common_json(
        model_name1, seed1, model_name2, seed2, metric, overwrite=overwrite
    )
    create_folds(
        model_name1,
        seed1,
        model_name2,
        seed2,
        metric,
        fold_size=fold_size,
        overwrite=overwrite,
    )


if __name__ == "__main__":
    # Put json file with generations in folder model_outputs/{model_name}_{seed}
   
    model_name = "LLama-3-8B-ckpt6"  # change this to the checkpoint to evaluate
    # checkpoints still to evaluate: 6,7,8,9,10, all gemma models, base instruct model

    seed = "seed1000"  # change this to the current seed

    evaluate_single_model(model_name, seed, "toxicity", overwrite=True, use_wandb=True)
    #this is a change
    
