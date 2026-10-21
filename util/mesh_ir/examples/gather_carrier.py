import torch
from torch import nn

VOCAB = 32768
HIDDEN = 512


def create_model():
    return LargeVocabEmbedding()


def create_unaligned_model():
    return LargeVocabEmbedding(hidden=519)


class LargeVocabEmbedding(nn.Module):
    def __init__(self, hidden=HIDDEN):
        super().__init__()
        self.weight = nn.Parameter(torch.randn(VOCAB, hidden))

    def forward(self, ids):
        return torch.nn.functional.embedding(ids, self.weight)
