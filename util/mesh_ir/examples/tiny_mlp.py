from __future__ import annotations

import torch
from torch import nn


class TinyMlp(nn.Module):
    def __init__(self):
        super().__init__()
        torch.manual_seed(0)
        self.input = nn.Linear(16, 32)
        self.output = nn.Linear(32, 8)

    def forward(self, value):
        return self.output(torch.nn.functional.gelu(self.input(value), approximate="tanh"))


def create_model() -> nn.Module:
    return TinyMlp()


def example_args() -> tuple[torch.Tensor, ...]:
    return (torch.arange(32, dtype=torch.float32).reshape(2, 16) / 32,)
