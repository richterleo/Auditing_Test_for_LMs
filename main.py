import argparse
import debugpy
import os
import sys

# imports from other scripts
from arguments import TrainCfg
from utils.utils import load_config

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "deep-anytime-testing"))

from auditing_test.test import (
    AuditingTest,
    CalibratedAuditingTest,
    DefaultEpsilonStrategy,
    CrossValEpsilonStrategy,
    IntervalEpsilonStrategy,
    eval_model,
)

os.environ["PYDEVD_DISABLE_FILE_VALIDATION"] = "1"


def main():
    """ """
    # Create the argument parser
    parser = argparse.ArgumentParser(description="Run experiments")
    parser.add_argument(
        "--exp",
        type=str,
        choices=["generation", "test"],
        required=True,
        help="Select the experiment to run: generating model outputs or auditing test.",
    )

    # TODO: change this when OnlineTrainer is no longer deprecated.
    parser.add_argument(
        "--online",
        action="store_true",
        help="Whether to use the OnlineTrainer instead of the OfflineTrainer. Warning: OnlineTrainer is currently deprecated.",
    )

    parser.add_argument(
        "--config_path",
        type=str,
        default="config.yml",
        help="Path to config file",
    )

    parser.add_argument(
        "--fold_size",
        type=int,
        default=4000,
        help="Fold size when running kfold tests. Default is 4000.",
    )

    parser.add_argument(
        "--model_name1",
        type=str,
        default=None,
        help="Name of first model as it appears in the folder name.",
    )

    parser.add_argument(
        "--model_name2",
        type=str,
        default=None,
        help="Name of second model as it appears in the folder name.",
    )

    parser.add_argument(
        "--seed1",
        type=str,
        default=None,
        help="Generation seed of first model as it appears in the folder name.",
    )

    parser.add_argument(
        "--seed2",
        type=str,
        default=None,
        help="Generation seed of second model as it appears in the folder name.",
    )

    parser.add_argument(
        "--no_wandb",
        action="store_true",
        help="If this is set to true, then no tracking on wandb.",
    )

    parser.add_argument(
        "--no_analysis",
        action="store_true",
        help="If this is set to true, then no analysis after runnning the test.",
    )

    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="If this is set to true, then we run the test with calibrated epsilon",
    )

    parser.add_argument("--debug_mode", action="store_true", help="Run in debug mode")

    parser.add_argument("--high_temp", action="store_true", help="Run with high temperature")

    parser.add_argument("--hf_prefix", type=str, default=None, help="Prefix for huggingface model")

    parser.add_argument("--eval_on_task", action="store_true", help="Whether to evaluate on task")

    parser.add_argument("--few_shot", action="store_true", help="Whether to run few shot experiments")

    args = parser.parse_args()

    if args.debug_mode:
        debugpy.listen(("0.0.0.0", 5678))
        print("waiting for debugger attach...")
        debugpy.wait_for_client()
        print("Debugger attached")

    config = load_config(args.config_path)
    if args.hf_prefix:
        config["tau1"]["hf_prefix"] = args.hf_prefix

    if args.few_shot:
        config["task_metric"]["few_shot"] = True
    else:
        config["task_metric"]["few_shot"] = False

    # Determine which experiment to run based on the argument
    if args.exp == "generation":
        # TODO: make this a bit smoother
        if args.high_temp:
            config["tau1"]["gen_kwargs"] = config["tau1"]["gen_kwargs_high_temp"]
        eval_model(
            config,
            model_id=args.model_name1,
            hf_prefix=args.hf_prefix,
            use_wandb=not args.no_wandb,
            eval_on_task=args.eval_on_task,
        )

    elif args.exp == "test":
        train_cfg = TrainCfg()
        if args.calibrate:
            exp = CalibratedAuditingTest(
                config,
                train_cfg,
                DefaultEpsilonStrategy(config),
                use_wandb=not args.no_wandb,
            )
            exp.run(
                model_name1=args.model_name1,
                seed1=args.seed1,
                model_name2=args.model_name2,
                seed2=args.seed2,
                fold_size=args.fold_size,
            )
        else:
            exp = AuditingTest(
                config,
                train_cfg,
                use_wandb=not args.no_wandb,
            )
            exp.run(
                model_name1=args.model_name1,
                seed1=args.seed1,
                model_name2=args.model_name2,
                seed2=args.seed2,
                fold_size=args.fold_size,
                analyze_distance=not args.no_analysis,
            )


if __name__ == "__main__":
    config = load_config("config.yml")
    train_cfg = TrainCfg()
    model_name1 = "Meta-Llama-3-8B-Instruct"
    model_name2 = "1-Meta-Llama-3-8B-Instruct"
    model_name3 = "2-Meta-Llama-3-8B-Instruct"
    model_name4 = "3-Meta-Llama-3-8B-Instruct"
    model_name5 = "4-Meta-Llama-3-8B-Instruct"
    model_name6 = "5-Meta-Llama-3-8B-Instruct"

    lower_model = "Meta-Llama-3-8B-Instruct-hightemp"
    lower_seed = "seed1000"

    # upper_model = "Llama-3-8B-ckpt1"
    # upper_seed = "seed2000"

    upper_model = "LLama-3-8b-Uncensored"
    upper_seed = "seed1000"

    seed1 = "seed1000"
    seed2 = "seed1000"
    fold_size = 3000

    # tasks = [
    #     "Mistral-7B-Instruct-v0.2",
    #     "gemma-1.1-7b-it",
    #     "Llama-3-8B-ckpt10",
    #     "codealpaca-Meta-Llama-3-8B-Instruct",
    #     "Meta-Llama-3-8B-Instruct-hightemp",
    #     "commonsense_classification-Meta-Llama-3-8B-Instruct",
    #     "program_execution-Meta-Llama-3-8B-Instruct",
    #     "sentence_perturbation-Meta-Llama-3-8B-Instruct",
    #     "text_matching-Meta-Llama-3-8B-Instruct",
    #     "textual_entailment-Meta-Llama-3-8B-Instruct",
    # ]

    # task_seeds = ["seed2000"] + ["seed1000" for _ in range(len(tasks) - 1)]

    # models_and_seeds = [
    #     {"model_name": task, "seed": seed} for task, seed in zip(tasks, task_seeds)
    # ]

    # exp = CalibratedAuditingTest(
    #     config,
    #     train_cfg,
    #     IntervalEpsilonStrategy(lower_model, lower_seed, upper_model, upper_seed, config=config, num_runs=20),
    #     use_wandb=False,
    #     overwrite=False,
    # )

    # exp.run(model_name1=model_name1, seed1="seed1000", model_name2=model_name2, seed2="seed1000", fold_size=fold_size)

    # exp.run(model_name1=model_name1, seed1="seed1000", model_name2=model_name3, seed2="seed1000", fold_size=fold_size)

    # exp.run(model_name1=model_name1, seed1="seed1000", model_name2=model_name4, seed2="seed1000", fold_size=fold_size)

    # exp.run(model_name1=model_name1, seed1="seed1000", model_name2=model_name5, seed2="seed1000", fold_size=fold_size)

    # exp.run(model_name1=model_name1, seed1="seed1000", model_name2=model_name6, seed2="seed1000", fold_size=fold_size)

    # # exp = CalibratedAuditingTest(
    # #     config,
    # #     train_cfg,
    # #     DefaultEpsilonStrategy(config=config, num_runs=5),
    # #     use_wandb=False,
    # # )
    # # exp.run(
    # #     model_name1=model_name1,
    # #     seed1=seed1,
    # #     model_name2=model_name2,
    # #     seed2=seed2,
    # #     fold_size=fold_size,
    # # )

    # exp = AuditingTest(config, train_cfg, use_wandb=False)
    # exp.run(
    #     model_name1=model_name1,
    #     seed1=seed1,
    #     model_name2=model_name2,
    #     seed2=seed2,
    #     fold_size=fold_size,
    # )

    # eval_model(config, use_wandb=False, eval_on_task=True)
    main()
