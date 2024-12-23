import importlib
import logging
import numpy as np
import pandas as pd
import random
import sys
import torch
import wandb

from datasets import load_dataset
from pathlib import Path
from peft import AutoPeftModelForCausalLM
from scipy.stats import wasserstein_distance, ks_2samp
from sklearn.model_selection import train_test_split, KFold
from torch.utils.data import DataLoader, ConcatDataset, Subset, Dataset
from transformers import pipeline, AutoTokenizer
from transformers.utils import is_flash_attn_2_available
from tqdm import tqdm
from typing import Optional, Dict, List

# Add paths to sys.path if not already present
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

# own utilities
from src.test.dataloader import ScoresDataset, collate_fn, load_into_scores_ds

# from arguments import Cfg
from src.evaluation.score import eval_on_metric
from src.utils.utils import translate_model_kwargs, time_block, NestedKeyDataset, terminator

orig_models = importlib.import_module("deep-anytime-testing.trainer.trainer", package="deep-anytime-testing")
Trainer = getattr(orig_models, "Trainer")

logger = logging.getLogger(__name__)


class OfflineTrainer(Trainer):
    def __init__(
        self,
        train_cfg,
        net,
        model_name1,
        seed1,
        model_name2,
        seed2,
        metric="perspective",
        use_wandb=True,
        fold_num: Optional[int] = None,
        verbose=False,
        epsilon=1,
        consistent_bs=True,
        only_continuations=True,
        test_dir="test_outputs",
        score_dir="model_scores",
        gen_dir="model_outputs",
        calc_stats=True,
        noise=0,
        drift=False,
    ):
        super().__init__(
            train_cfg,
            net,
            (model_name1, seed1),
            (model_name2, seed2),
            None,
            None,
            1,
            # train_cfg.seed,
        )

        # remove unnecessary attributes
        del self.datagen

        # this is all just for one fold of the distribution data
        self.fold_num = fold_num
        self.metric = metric
        self.only_continuations = only_continuations

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if not (self.device == "cuda"):
            logger.warning("CUDA is not available. Using CPU.")
        self.net.to(self.device)

        # self.test_on_task = test_on_task
        # self.test_on_waterbirds = test_on_waterbirds

        # if self.test_on_waterbirds:
        #     output_dir = (
        #         "/root/Auditing_test_for_LMs/Auditing_test_for_LMs/Auditing_test_for_LMs/BalancingGroups/outputs"
        #     )
        # elif self.test_on_task:
        #     output_dir = "processed_data/translation_test_outputs"
        # else:
        #     output_dir = "test_outputs"

        self.dataset = load_into_scores_ds(
            model_name1,
            seed1,
            model_name2,
            seed2,
            metric,
            fold_num=fold_num,
            only_continuations=only_continuations,
            test_dir=test_dir,
            score_dir=score_dir,
            gen_dir=gen_dir,
            noise=noise,
        )

        # This is the batch size for the network. Should probably ideally be the same as the overall batch size
        self.net_bs = train_cfg.net_batch_size if not consistent_bs else self.bs
        if not (self.net_bs == self.bs):
            logger.warning(
                f"Using different batch size within betting score network (self.net_bs = {self.net_bs}) than for sequences (self.bs = {self.bs}). Might lead to unexpected behavior."
            )

        self.num_batches = (len(self.dataset) + self.bs - 1) // self.bs
        if self.num_batches * self.bs < len(self.dataset):
            logger.warning(
                f"{len(self.dataset) - self.num_batches * self.bs} samples will be discarded as they don't fit into a full batch."
            )

        self.drift = drift
        self.batches, self.batch_indices = self.get_kfold_sequence_batches()
        logger.info(f"Number of sequence batches created: {len(self.batches)}")

        # Epsilon for tolerance test
        self.epsilon = epsilon

        # for logging/tracking
        self.use_wandb = use_wandb
        self.verbose = verbose
        self.current_total_epoch = 0
        self.columns = [
            "sequence",
            "epoch",
            "samples",
            "train_loss",
            "val_loss",
            "test_loss",
            "betting_score",
            "wealth",
            "epochs_until_end_of_sequence",
            "sequences_until_end_of_experiment",
            "test_positive",
        ]
        self.data = pd.DataFrame(columns=self.columns)

        # for fast analysis
        self.test_positive = False

        self.calc_stats = calc_stats
        if self.calc_stats:
            self.stat_dict = {
                "mean1": [],
                "mean2": [],
                "std1": [],
                "std2": [],
                "ws": [],
                "ks_p-value": [],
                "fold_number": [],
                "sequence": [],
                "num_samples": [],
            }
        else:
            self.stat_dict = None

        self.noise = noise

    def add_epoch_data(self, sequence, epoch, train_loss, val_loss):
        row = {
            "sequence": sequence,
            "epoch": epoch,
            "samples": self.bs * (epoch + 1),
            "train_loss": train_loss,
            "val_loss": val_loss,
            "test_loss": np.nan,
            "betting_score": np.nan,
            "wealth": np.nan,
            "epochs_until_end_of_sequence": np.nan,
            "sequences_until_end_of_experiment": np.nan,
            "test_positive": int(0),
        }
        new_data = pd.DataFrame([row])
        self.data = new_data.copy() if self.data.empty else pd.concat([self.data, new_data], ignore_index=True)

    def add_sequence_data(self, sequence, test_loss, betting_score, wealth):
        """Update test_loss and betting score/wealth for the given sequence and epoch"""
        self.data.loc[
            (self.data["sequence"] == sequence),
            "test_loss",
        ] = test_loss
        self.data.loc[(self.data["sequence"] == sequence), "betting_score"] = betting_score
        self.data.loc[
            (self.data["sequence"] == sequence),
            "wealth",
        ] = wealth

    def update_epochs_until_end_of_sequence(self, sequence):
        max_epoch = self.data[(self.data["sequence"] == sequence)]["epoch"].max()
        self.data.loc[
            (self.data["sequence"] == sequence),
            "epochs_until_end_of_sequence",
        ] = max_epoch

    def update_sequences_until_end_of_experiment(self):
        """
        In case of positive test result, update the number of sequences until the end of the experiment
        """
        max_sequence = self.data["sequence"].max()
        self.data["sequences_until_end_of_experiment"] = max_sequence
        self.data["test_positive"] = int(1)

    def log(self, logs, seq, epoch, total_epoch, new_start_sequence):
        """
        Log metrics for visualization and monitoring.

        Args:
        - logs (dict): Dictionary containing metrics to be logged.
        """

        for key, value in logs.items():
            if self.use_wandb:
                if self.fold_num:
                    wandb.log(
                        {
                            key: value,
                            "sequence": seq,
                            "epoch": epoch,
                            "epoch_total": total_epoch,
                            "new_start_sequence": new_start_sequence,
                            "fold_num": self.fold_num,
                        }
                    )
                else:
                    wandb.log(
                        {
                            key: value,
                            "sequence": seq,
                            "epoch": epoch,
                            "epoch_total": total_epoch,
                            "new_start_sequence": new_start_sequence,
                        }
                    )

            if self.fold_num and self.verbose:
                logger.info(
                    f"Fold_num: {self.fold_num}, Seq: {self.current_seq}, Epoch: {self.current_epoch}, {key}: {value}"
                )

    def get_kfold_sequence_batches(self):
        """
        Responsible for dividing the samples in the current fold into sequences
        """
        kf = KFold(n_splits=self.num_batches, shuffle=True, random_state=self.seed)

        valid_size = self.num_batches * self.bs
        logger.info(f"Size of all batches: {valid_size}")
        if valid_size < len(self.dataset):
            rng = np.random.RandomState(self.seed)
            self.dataset = rng.permutation(self.dataset)[:valid_size]
            logger.info(f"Whole dataset has been trimmed to length: {len(self.dataset)}")

        batches = []
        batch_indices_list = []
        for _, batch_indices in kf.split(self.dataset):
            batches.append(Subset(self.dataset, batch_indices))
            batch_indices_list.append(batch_indices)

        if self.drift:
            # add drift to all batches
            num_batches = len(batches)
            drift_per_batch = 0.2 / num_batches
            for i, batch in enumerate(batches):
                batch = list(batch)
                for j in range(len(batch)):
                    batch[j] = (min(1, batch[j][0] + drift_per_batch * i), min(1, batch[j][1] + drift_per_batch * i))
                batches[i] = ScoresDataset(*zip(*batch))

        return batches, batch_indices_list

    def train(self):
        """ """
        torch.manual_seed(self.seed)
        np.random.seed(self.seed)
        betting_scores = []

        self.current_seq = 0
        self.current_epoch = 0

        # In the first sequence, we don't train our model, directly evaluate
        test_ds = self.batches[0]

        self.num_samples = len(test_ds)
        test_loader = DataLoader(test_ds, batch_size=self.net_bs, shuffle=True, collate_fn=collate_fn)
        test_loss, betting_score = self.train_evaluate_epoch(test_loader, mode="test")
        betting_scores.append(betting_score.item())
        self.log(
            {"aggregated_test_e-value": betting_score},
            self.current_seq,
            self.current_epoch,
            self.current_total_epoch,
            int(self.current_epoch == 0),
        )
        row = {
            "sequence": 0,
            "epoch": 0,
            "samples": self.bs,
            "train_loss": np.nan,
            "val_loss": np.nan,
            "test_loss": test_loss.detach().cpu().item(),
            "betting_score": betting_score.cpu().item(),
            "wealth": betting_score.cpu().item(),  # wealth is the same as betting score in the first sequence
            "epochs_until_end_of_sequence": np.nan,
            "sequences_until_end_of_experiment": np.nan,
            "test_positive": int(0),
        }
        new_data = pd.DataFrame([row])
        self.data = new_data.copy() if self.data.empty else pd.concat([self.data, new_data], ignore_index=True)

        # Log information if wealth exceeds the threshold TODO: not sure we need this for first batch??
        if betting_score > (1.0 / self.alpha):
            logger.info("Reject null at %f", betting_score)
            self.test_positive = True

            self.log(
                {"aggregated_test_e-value": betting_score},
                self.current_seq,
                self.current_epoch,
                self.current_total_epoch,
                int(self.current_epoch == 0),
            )

        else:
            # In first sequence, we need to distribute the data into train and val set
            train_ds, val_ds = train_test_split(self.batches[0], test_size=0.2, random_state=self.seed)
            train_loader = DataLoader(train_ds, batch_size=self.net_bs, shuffle=True, collate_fn=collate_fn)
            val_loader = DataLoader(val_ds, batch_size=self.net_bs, shuffle=True, collate_fn=collate_fn)

            # Iterate over sequences
            for k in tqdm(range(1, min(self.seqs, self.num_batches))):
                self.current_seq = k
                self.current_epoch = 0

                with time_block(f"Sequence {k}/{self.num_batches}"):
                    for i in range(self.epochs):
                        self.current_epoch = i
                        self.current_total_epoch += 1
                        loss_train, _ = self.train_evaluate_epoch(train_loader)
                        loss_val, _ = self.train_evaluate_epoch(val_loader, mode="val")
                        self.add_epoch_data(
                            self.current_seq,
                            self.current_epoch,
                            loss_train.detach().cpu().item(),
                            loss_val.detach().cpu().item(),
                        )

                        # Check for early stopping or end of epochs
                        if self.early_stopper.early_stop(loss_val.detach()) or (i + 1) == self.epochs:
                            # Now define new test data from current batch

                            self.update_epochs_until_end_of_sequence(self.current_seq)
                            test_ds = self.batches[k]
                            self.num_samples += len(test_ds)
                            test_loader = DataLoader(
                                test_ds,
                                batch_size=self.net_bs,
                                shuffle=True,
                                collate_fn=collate_fn,
                            )

                            # Get S_t value on current batch
                            test_loss, betting_score = self.train_evaluate_epoch(test_loader, mode="test")
                            betting_scores.append(betting_score.item())
                            wealth = np.prod(np.array(betting_scores[self.T :])) if k >= self.T else 1
                            self.log(
                                {"wealth": wealth},
                                self.current_seq,
                                self.current_epoch,
                                self.current_total_epoch,
                                int(self.current_epoch == 0),
                            )

                            self.add_sequence_data(
                                self.current_seq,
                                test_loss.detach().cpu().item(),
                                betting_scores[-1],
                                wealth,
                            )

                            # former train_ds and val_ds become the new train set
                            train_ds = ConcatDataset([train_ds, val_ds])
                            train_loader = DataLoader(
                                train_ds,
                                batch_size=self.net_bs,
                                shuffle=True,
                                collate_fn=collate_fn,
                            )

                            # former test_loader (i.e. current batch) becomes validation set
                            val_ds = test_ds
                            val_loader = test_loader

                            break

                # Reset the early stopper for the next sequence
                self.early_stopper.reset()

                # Log information if wealth exceeds the threshold
                if wealth > (1.0 / self.alpha):
                    logger.info("Reject null at %f", wealth)
                    self.test_positive = True

                    self.update_sequences_until_end_of_experiment()
                    self.log(
                        {"steps": k, "total_num_samples": self.num_samples},
                        self.current_seq,
                        self.current_epoch,
                        self.current_total_epoch,
                        int(self.current_epoch == 0),
                    )

                    break

        if not self.test_positive:
            logger.info(f"Null hypothesis not rejected. Final wealth at {wealth}.")

        self.data["fold_number"] = self.fold_num
        self.data["test_positive"] = self.data["test_positive"].astype(int)

        if self.calc_stats:
            self.calculate_statistics()
            stat_df = pd.DataFrame(self.stat_dict)
        else:
            stat_df = None

        return self.data, self.test_positive, stat_df

    def train_evaluate_epoch(self, data_loader, mode="train"):
        """ """

        aggregated_loss = 0
        betting_score = 1  # This does not mean we are calculating wealth from scratch, just functions as blank slate for current betting score
        num_samples = len(data_loader.dataset)

        self.log(
            {"num_samples": num_samples},
            self.current_seq,
            self.current_epoch,
            self.current_total_epoch,
            int(self.current_epoch == 0),
        )

        for batch in data_loader:
            tau1, tau2 = torch.split(batch, 1, dim=1)
            tau1 = tau1.to(self.device)
            tau2 = tau2.to(self.device)
            if mode == "train":
                self.net.train()
                # values for tau1 and tau2
                out = self.net(tau1, tau2)
            else:
                self.net.eval()
                out = self.net(tau1, tau2).detach()

            loss = -out.mean() + self.l1_lambda * self.l1_regularization()
            aggregated_loss += -out.sum()  # we can leave epsilon out for optimization

            # need epsilon here for calculating the tolerant betting score
            num_batch_samples = out.shape[0]
            betting_score *= torch.exp(-self.epsilon * num_batch_samples + out.sum())

            if mode == "train":
                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()

        self.log(
            {
                f"{mode}_betting_score": betting_score.item(),
                f"{mode}_loss": aggregated_loss.item() / num_samples,
            },
            self.current_seq,
            self.current_epoch,
            self.current_total_epoch,
            int(self.current_epoch == 0),
        )
        return aggregated_loss / num_samples, betting_score

    def calculate_statistics(self):
        """ """
        all_scores1 = [self.dataset.data[i][0] for i in range(len(self.dataset))]
        all_scores2 = [self.dataset.data[i][1] for i in range(len(self.dataset))]

        # Calculate the mean and standard deviation of the scores
        mean1 = np.mean(all_scores1)
        mean2 = np.mean(all_scores2)
        std1 = np.std(all_scores1)
        std2 = np.std(all_scores2)

        # Calculate the Wasserstein distance
        ws = wasserstein_distance(all_scores1, all_scores2)

        scores1 = []
        scores2 = []

        for seq_num in range(0, len(self.batches)):
            batch_indices = self.batch_indices[seq_num]
            scores1 += [self.dataset.data[i][0] for i in batch_indices]
            scores2 += [self.dataset.data[i][1] for i in batch_indices]
            assert len(scores1) == len(scores2), "Length of scores1 and scores2 should be the same"
            num_samples = len(scores1)
            p_value = ks_2samp(scores1, scores2)[1]

            self.stat_dict["mean1"].append(mean1)
            self.stat_dict["mean2"].append(mean2)
            self.stat_dict["std1"].append(std1)
            self.stat_dict["std2"].append(std2)
            self.stat_dict["ws"].append(ws)
            self.stat_dict["ks_p-value"].append(p_value)
            self.stat_dict["fold_number"].append(self.fold_num)
            self.stat_dict["sequence"].append(seq_num)
            self.stat_dict["num_samples"].append(num_samples)


class OnlineTrainer(Trainer):
    """deprecated, use OfflineTrainer instead"""

    def __init__(
        self,
        train_cfg,
        net,
        tau1_cfg,
        dataset_name,
        behavior,
        metric,
        use_wandb,
        tau2_cfg=None,
    ):
        super().__init__(
            train_cfg,
            net,
            tau1_cfg,
            tau2_cfg or tau1_cfg,
            dataset_name,
            None,
            train_cfg.seed,
        )

        self.use_same_tau = tau2_cfg is None

        self.pipeline1, self.tokenizer1, self.terminators1 = self.setup_model(self.tau1)
        self.pipeline2, self.tokenizer2, self.terminators2 = (
            (self.pipeline1, self.tokenizer1, self.terminators1) if self.use_same_tau else self.setup_model(self.tau2)
        )

        self.gen1_kwargs = self.tau1["gen_kwargs"]
        self.gen2_kwargs = self.tau1["gen_kwargs"] if self.use_same_tau else self.tau2["gen_kwargs"]

        self.behavior = behavior
        self.metric = metric if metric else behavior

        # Load the dataset
        with time_block("Loading the dataset"):
            self.dataset = load_dataset(self.datagen, split="train")

        self.use_wandb = use_wandb

    def setup_model(self, tau_cfg):
        """ """
        tokenizer = AutoTokenizer.from_pretrained(tau_cfg["model_id"], padding_side="left")
        if tokenizer.pad_token is None:
            tokenizer.pad_token_id = tokenizer.eos_token_id

        model_kwargs = translate_model_kwargs(tau_cfg["model_kwargs"])
        if is_flash_attn_2_available():
            model_kwargs.update({"attn_implementation": "flash_attention_2"})

        terminators = [tokenizer.eos_token_id]
        terminator_key = self.get_terminator_key(tau_cfg["model_id"])
        if terminator_key:
            terminators.append(tokenizer.convert_tokens_to_ids(terminator[terminator_key]))

        model = (
            AutoPeftModelForCausalLM.from_pretrained(tau_cfg["model_id"], **model_kwargs)
            if tau_cfg["model_id"].startswith("LLMAccountability")
            else tau_cfg["model_id"]
        )

        pipeline_obj = pipeline(
            "text-generation",
            model=model,
            model_kwargs=model_kwargs,
            tokenizer=tokenizer,
            pad_token_id=tokenizer.eos_token_id,
        )

        return pipeline_obj, tokenizer, terminators

    def get_terminator_key(self, model_id):
        """ """
        if "Llama-3" in model_id:
            return "llama3"
        elif "Mistral" in model_id:
            return "mistral"
        elif "gemma" in model_id:
            return "gemma"
        return None

    def log(self, logs, seq, epoch, total_epoch, new_start_sequence):
        """
        Log metrics for visualization and monitoring.

        Args:
        - logs (dict): Dictionary containing metrics to be logged.
        """

        for key, value in logs.items():
            if self.use_wandb:
                wandb.log(
                    {
                        key: value,
                        "sequence": seq,
                        "epoch": epoch,
                        "epoch_total": total_epoch,
                        "new_start_sequence": new_start_sequence,
                    }
                )
            logger.info(f"Seq: {self.current_seq}, Epoch: {self.current_epoch}, {key}: {value}")

    def train(self):
        """
        Overwrite method from Trainer class
        """

        torch.manual_seed(self.seed)
        np.random.seed(self.seed)
        davts = []

        self.current_seq = 0
        self.current_epoch = 0
        self.current_total_epoch = 0

        num_batches = (len(self.dataset) + self.bs - 1) // self.bs

        indices = list(range(len(self.dataset)))
        random.shuffle(indices)

        batches = [indices[i * self.bs : min((i + 1) * self.bs, len(self.dataset))] for i in range(num_batches)]

        with time_block("Now evaluating on the first test_ds"):
            # in the first sequence, we don't train our model
            test_ds = self.get_score_ds(batches[0])
            test_loader = DataLoader(test_ds, batch_size=self.net_bs, shuffle=True, collate_fn=collate_fn)
            _, davt = self.train_evaluate_epoch(test_loader, mode="test")
            davts.append(davt.item())
            self.log(
                {"aggregated_test_e-value": davt},
                self.current_seq,
                self.current_epoch,
                self.current_total_epoch,
                int(self.current_epoch == 0),
            )

        # Log information if davt exceeds the threshold TODO: not sure we need this for first batch??
        if davt > (1.0 / self.alpha):
            logging.info("Reject null at %f", davt)
            self.log({"steps": 0}, self.current_seq, self.current_epoch, 0)

        for k in range(1, min(self.seqs, num_batches)):
            # This is the maximum number of mini-batches to sample from the data

            self.current_seq = k

            # If k=1, we still need to define train and val set
            if k == 1:
                # in this case, we need to define val set as fraction of train set
                batch_indices = batches[k - 1]
                train_indices, val_indices = train_test_split(
                    np.array(batch_indices), test_size=0.3, random_state=self.seed
                )
                # TODO: make this smoother
                train_indices = [ti.item() for ti in train_indices]
                val_indices = [vi.item() for vi in val_indices]
                train_ds = self.get_score_ds(train_indices)
                val_ds = self.get_score_ds(val_indices)
                train_loader = DataLoader(
                    train_ds,
                    batch_size=self.net_bs,
                    shuffle=True,
                    collate_fn=collate_fn,
                )
                val_loader = DataLoader(val_ds, batch_size=self.net_bs, shuffle=True, collate_fn=collate_fn)

            # Actual model training
            for i in range(self.epochs):
                self.current_epoch = i
                self.current_total_epoch += 1
                with time_block(f"Training epoch {i} on sequence {k}"):
                    self.train_evaluate_epoch(train_loader)
                with time_block(f"Validation epoch {i} on sequence {k}"):
                    loss_val, _ = self.train_evaluate_epoch(val_loader, mode="val")

                # Check for early stopping or end of epochs
                if self.early_stopper.early_stop(loss_val.detach()) or (i + 1) == self.epochs:
                    # Now define test data from current batch
                    batch_indices = batches[k]
                    test_ds = self.get_score_ds(batch_indices)
                    test_loader = DataLoader(
                        test_ds,
                        batch_size=self.net_bs,
                        shuffle=True,
                        collate_fn=collate_fn,
                    )

                    # Get S_t value on current batch
                    _, conditional_davt = self.train_evaluate_epoch(test_loader, mode="test")
                    davts.append(conditional_davt.item())
                    davt = np.prod(np.array(davts[self.T :])) if k >= self.T else 1
                    self.log(
                        {"aggregated_test_e-value": davt},
                        self.current_seq,
                        self.current_epoch,
                        self.current_total_epoch,
                        int(self.current_epoch == 0),
                    )

                    # former train_ds and val_ds become the new train set
                    train_ds = ConcatDataset([train_ds, val_ds])
                    train_loader = DataLoader(
                        train_ds,
                        batch_size=self.net_bs,
                        shuffle=True,
                        collate_fn=collate_fn,
                    )

                    # former test_loader (i.e. current batch) becomes validation set
                    val_loader = test_loader

                    break

            # Reset the early stopper for the next sequence
            self.early_stopper.reset()

            # Log information if davt exceeds the threshold
            if davt > (1.0 / self.alpha):
                logger.info("Reject null at %f", davt)
                self.log(
                    {"steps": k},
                    self.current_seq,
                    self.current_epoch,
                    self.current_total_epoch,
                    int(self.current_epoch == 0),
                )

    def train_evaluate_epoch(self, data_loader, mode="train"):
        """ """

        aggregated_loss = 0
        davt = 1
        num_samples = len(data_loader.dataset)

        self.log(
            {"num_samples": num_samples},
            self.current_seq,
            self.current_epoch,
            self.current_total_epoch,
            int(self.current_epoch == 0),
        )

        for batch in data_loader:
            if mode == "train":
                self.net.train()
                # values for tau1 and tau2
                tau1, tau2 = torch.split(batch, 1, dim=1)
                out = self.net(tau1, tau2)
            else:
                self.net.eval()
                tau1, tau2 = torch.split(batch, 1, dim=1)
                out = self.net(tau1, tau2).detach()

            loss = -out.mean() + self.l1_lambda * self.l1_regularization()
            aggregated_loss += -out.sum()
            davt *= torch.exp(out.sum())
            if mode == "train":
                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()

        self.log(
            {
                f"{mode}_e-value": davt.item(),
                f"{mode}_loss": aggregated_loss.item() / num_samples,
            },
            self.current_seq,
            self.current_epoch,
            self.current_total_epoch,
            int(self.current_epoch == 0),
        )
        return aggregated_loss / num_samples, davt

    def get_score_ds(self, indices):  # TODO: make this batch_size a param in configuration
        """
        Querying the models for continuations and evaluating them on the metric.
        """
        continuations1 = []
        continuations2 = []

        subset = Subset(self.dataset, indices)

        with time_block(f"Generating continuations for {len(indices)} samples"):
            # Get outputs from first pipeline
            for out in tqdm(
                self.pipeline1(
                    NestedKeyDataset(
                        subset,
                        "prompt",
                        "text",
                        self.tau1["model_id"],
                        self.tokenizer1,
                    ),
                    pad_token_id=self.tokenizer1.eos_token_id,
                    batch_size=self.tau1["gen_batch_size"],
                    **self.gen1_kwargs,
                )
            ):
                cont1 = out[0]["generated_text"]
                continuations1.append(cont1)

            # Get outputs from second pipeline
            for out in tqdm(
                self.pipeline2(
                    NestedKeyDataset(
                        subset,
                        "prompt",
                        "text",
                        self.tau2["model_id"],
                        self.tokenizer2,
                    ),
                    pad_token_id=self.tokenizer2.eos_token_id,
                    batch_size=self.tau2["gen_batch_size"],
                    **self.gen2_kwargs,
                )
            ):
                cont2 = out[0]["generated_text"]
                continuations2.append(cont2)

        # Get metrics for batch
        with time_block(f"Generating metric scores for {len(indices)} samples"):
            scores1 = eval_on_metric(self.metric, continuations1)
            scores2 = eval_on_metric(self.metric, continuations2)

        # Make new dataset
        score_ds = ScoresDataset(scores1, scores2)

        return score_ds

    def get_score_ds_slow(self, indices):  # TODO: remove this, this was pre-batching
        """
        Querying the models for continuations and evaluating them on the metric.
        """
        continuations1 = []
        continuations2 = []

        with time_block(f"Generating continuations for {len(indices)} samples"):
            for sample in list(indices):
                with time_block(f"Generating continuation for sample {sample} out of {len(indices)}"):
                    out1 = self.pipeline1(
                        self.dataset[sample]["prompt"]["text"],
                        pad_token_id=self.tokenizer1.eos_token_id,
                        **self.gen1_kwargs,
                    )
                    out2 = self.pipeline2(
                        self.dataset[sample]["prompt"]["text"],
                        pad_token_id=self.tokenizer2.eos_token_id,
                        **self.gen2_kwargs,
                    )

                    cont1 = out1[0]["generated_text"].replace(self.dataset[sample]["prompt"]["text"], "")
                    cont2 = out2[0]["generated_text"].replace(self.dataset[sample]["prompt"]["text"], "")

                    continuations1.append(cont1)
                    continuations2.append(cont2)

        # Get metrics for batch
        with time_block(f"Generating metric scores for {len(indices)} samples"):
            scores1 = eval_on_metric(self.metric, continuations1)
            scores2 = eval_on_metric(self.metric, continuations2)

        # Make new dataset
        score_ds = ScoresDataset(scores1, scores2)

        return score_ds
