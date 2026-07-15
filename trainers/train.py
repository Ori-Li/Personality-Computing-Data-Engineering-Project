from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
import torch
import yaml
from transformers import AutoTokenizer, Trainer, TrainingArguments, set_seed

from DataSetsCode.collator import RegressionCollator
from DataSetsCode.dataset import PersonalityDataset, load_examples
from models.personality_model import ModernBertRegressor
from trainers.evaluate import regression_metrics


def parse_args():
    parser = argparse.ArgumentParser(description="Fine-tune ModernBERT for personality vectors")
    parser.add_argument("--config", default="configs/model.yaml")
    parser.add_argument("--smoke-test", action="store_true", help="Run one short CPU/GPU step")
    return parser.parse_args()


def split_examples(examples, validation_ratio, seed):
    indices = list(range(len(examples)))
    random.Random(seed).shuffle(indices)
    validation_size = max(1, round(len(indices) * validation_ratio))
    if validation_size >= len(indices):
        validation_size = 1
    validation_indices = set(indices[:validation_size])
    train = [item for i, item in enumerate(examples) if i not in validation_indices]
    validation = [item for i, item in enumerate(examples) if i in validation_indices]
    return train, validation


def main():
    args = parse_args()
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    set_seed(config["seed"])
    examples, label_names = load_examples(
        config["input_path"], config["annotation_path"], config["target_path"]
    )
    train_examples, validation_examples = split_examples(
        examples, config["validation_ratio"], config["seed"]
    )
    if args.smoke_test:
        train_examples = train_examples[:2]
        validation_examples = validation_examples[:1]

    tokenizer = AutoTokenizer.from_pretrained(config["model_name"])
    model = ModernBertRegressor(config["model_name"], len(label_names), config["dropout"])
    if config.get("freeze_encoder"):
        for parameter in model.encoder.parameters():
            parameter.requires_grad = False
    if config.get("gradient_checkpointing") and not config.get("freeze_encoder"):
        model.encoder.gradient_checkpointing_enable()

    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "label_names.json").write_text(
        json.dumps(label_names, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "run_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=1 if args.smoke_test else config["epochs"],
        max_steps=1 if args.smoke_test else -1,
        per_device_train_batch_size=config["train_batch_size"],
        per_device_eval_batch_size=config["eval_batch_size"],
        gradient_accumulation_steps=1 if args.smoke_test else config["gradient_accumulation_steps"],
        learning_rate=float(config["learning_rate"]),
        weight_decay=float(config["weight_decay"]),
        warmup_ratio=float(config["warmup_ratio"]),
        eval_strategy="steps" if args.smoke_test else "epoch",
        save_strategy="no" if args.smoke_test else "epoch",
        logging_steps=1,
        load_best_model_at_end=not args.smoke_test,
        metric_for_best_model="mae",
        greater_is_better=False,
        fp16=bool(config.get("fp16")) and torch.cuda.is_available(),
        bf16=bool(config.get("bf16")) and torch.cuda.is_available(),
        dataloader_num_workers=int(config.get("num_workers", 0)),
        report_to="none",
        seed=config["seed"],
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=PersonalityDataset(train_examples, tokenizer, config["max_length"]),
        eval_dataset=PersonalityDataset(validation_examples, tokenizer, config["max_length"]),
        data_collator=RegressionCollator(tokenizer),
        compute_metrics=regression_metrics,
    )
    trainer.train()
    trainer.save_model(output_dir / "final")
    tokenizer.save_pretrained(output_dir / "final")
    print({"train": len(train_examples), "validation": len(validation_examples), "labels": label_names})


if __name__ == "__main__":
    main()
