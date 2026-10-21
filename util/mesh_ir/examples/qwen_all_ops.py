import torch
from torch import nn


def create_model():
    return AllQwenOps()


class AllQwenOps(nn.Module):
    """Every frontend gap class in one shape-consistent module."""

    def __init__(self):
        super().__init__()
        self.weight = nn.Parameter(torch.randn(8, 8))

    def forward(self, x, ids):
        y = torch.ops.aten._unsafe_view(x.unsqueeze(1), (2, 8))
        h = x.to(torch.float16).to(torch.float32)
        gathered = torch.nn.functional.embedding(ids, self.weight)
        return y + h + (-h) + torch.pow(h, 2) + torch.sin(h) \
            + torch.cos(h) + gathered
