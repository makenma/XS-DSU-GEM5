from __future__ import annotations

import torch
from torch import nn


class TinyTransformerBlock(nn.Module):
    def __init__(self):
        super().__init__()
        torch.manual_seed(0)
        self.query = nn.Linear(8, 8)
        self.key = nn.Linear(8, 8)
        self.value = nn.Linear(8, 8)
        self.attention_output = nn.Linear(8, 8)
        self.norm1 = nn.LayerNorm(8)
        self.norm2 = nn.LayerNorm(8)
        self.ffn_input = nn.Linear(8, 16)
        self.ffn_output = nn.Linear(16, 8)

    def forward(self, value):
        normalized = self.norm1(value)
        query = self.query(normalized)
        key = self.key(normalized).transpose(1, 2)
        attention = torch.softmax(torch.bmm(query, key) / 8**0.5, dim=-1)
        residual = self.attention_output(torch.bmm(attention, self.value(normalized))) + value
        hidden = torch.nn.functional.gelu(self.ffn_input(self.norm2(residual)), approximate="tanh")
        return self.ffn_output(hidden) + residual


def create_model() -> nn.Module:
    return TinyTransformerBlock()


def example_args() -> tuple[torch.Tensor, ...]:
    return (torch.arange(128, dtype=torch.float32).reshape(2, 8, 8) / 128,)


def dynamic_shapes():
    batch = torch.export.Dim("batch", min=1, max=4)
    sequence = torch.export.Dim("sequence", min=2, max=16)
    return ({0: batch, 1: sequence},)
