"""Vendored CLIP BPE tokenizer (the OpenAI/OpenCLIP `SimpleTokenizer`).

Self-contained so Argus pulls in no `tokenizers`/`open_clip`/`transformers` dependency.
Requires the byte-pair vocabulary asset `bpe_simple_vocab_16e6.txt.gz` to be present
alongside the CLIP model weights (shipped/downloaded with the model). Both seeded
OpenCLIP models (ViT-B/32, ViT-L/14) share this same vocabulary.

Adapted from the CLIP/OpenCLIP simple tokenizer (MIT License).
"""
from __future__ import annotations

import gzip
import html
from functools import lru_cache
from pathlib import Path

# CLIP text encoders use a fixed context length of 77 tokens.
CONTEXT_LENGTH = 77


@lru_cache()
def _bytes_to_unicode() -> dict[int, str]:
    """Reversible map of utf-8 bytes to unicode strings, avoiding control/whitespace
    characters the BPE would otherwise choke on."""
    bs = (
        list(range(ord("!"), ord("~") + 1))
        + list(range(ord("¡"), ord("¬") + 1))
        + list(range(ord("®"), ord("ÿ") + 1))
    )
    cs = bs[:]
    n = 0
    for b in range(2 ** 8):
        if b not in bs:
            bs.append(b)
            cs.append(2 ** 8 + n)
            n += 1
    return dict(zip(bs, [chr(c) for c in cs]))


def _get_pairs(word: tuple[str, ...]) -> set[tuple[str, str]]:
    return {(prev, cur) for prev, cur in zip(word, word[1:])}


def _basic_clean(text: str) -> str:
    text = html.unescape(html.unescape(text))
    return text.strip()


def _whitespace_clean(text: str) -> str:
    return " ".join(text.split()).strip()


class SimpleTokenizer:
    """Byte-pair tokenizer matching the CLIP text encoder vocabulary."""

    def __init__(self, bpe_path: str | Path) -> None:
        self.byte_encoder = _bytes_to_unicode()
        self.byte_decoder = {v: k for k, v in self.byte_encoder.items()}

        with gzip.open(str(bpe_path), "rt", encoding="utf-8") as f:
            merges = f.read().split("\n")
        merges = merges[1:49152 - 256 - 2 + 1]
        merge_tuples = [tuple(m.split()) for m in merges]

        vocab = list(_bytes_to_unicode().values())
        vocab = vocab + [v + "</w>" for v in vocab]
        for merge in merge_tuples:
            vocab.append("".join(merge))
        vocab.extend(["<|startoftext|>", "<|endoftext|>"])

        self.encoder = dict(zip(vocab, range(len(vocab))))
        self.bpe_ranks = dict(zip(merge_tuples, range(len(merge_tuples))))
        self.cache = {
            "<|startoftext|>": "<|startoftext|>",
            "<|endoftext|>": "<|endoftext|>",
        }
        self.sot_token = self.encoder["<|startoftext|>"]
        self.eot_token = self.encoder["<|endoftext|>"]

    def _bpe(self, token: str) -> str:
        if token in self.cache:
            return self.cache[token]
        word = tuple(token[:-1]) + (token[-1] + "</w>",)
        pairs = _get_pairs(word)
        if not pairs:
            return token + "</w>"

        while True:
            bigram = min(pairs, key=lambda p: self.bpe_ranks.get(p, float("inf")))
            if bigram not in self.bpe_ranks:
                break
            first, second = bigram
            new_word: list[str] = []
            i = 0
            while i < len(word):
                try:
                    j = word.index(first, i)
                except ValueError:
                    new_word.extend(word[i:])
                    break
                new_word.extend(word[i:j])
                i = j
                if word[i] == first and i < len(word) - 1 and word[i + 1] == second:
                    new_word.append(first + second)
                    i += 2
                else:
                    new_word.append(word[i])
                    i += 1
            word = tuple(new_word)
            if len(word) == 1:
                break
            pairs = _get_pairs(word)
        result = " ".join(word)
        self.cache[token] = result
        return result

    def encode(self, text: str) -> list[int]:
        tokens: list[int] = []
        text = _whitespace_clean(_basic_clean(text)).lower()
        for token in _simple_split(text):
            token = "".join(self.byte_encoder[b] for b in token.encode("utf-8"))
            tokens.extend(self.encoder[bpe_tok] for bpe_tok in self._bpe(token).split(" "))
        return tokens

    def tokenize(self, texts: list[str], context_length: int = CONTEXT_LENGTH) -> list[list[int]]:
        """Return token-id rows padded/truncated to context_length, with sot/eot."""
        result: list[list[int]] = []
        for text in texts:
            ids = [self.sot_token] + self.encode(text) + [self.eot_token]
            if len(ids) > context_length:
                ids = ids[:context_length]
                ids[-1] = self.eot_token
            ids = ids + [0] * (context_length - len(ids))
            result.append(ids)
        return result


# CLIP's reference tokenizer uses a regex pattern. `regex` is not a guaranteed
# dependency, so fall back to a simple splitter that is adequate for short tag
# phrases (the only thing we tokenize) when `regex` is unavailable.
_PAT = (
    r"<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|"
    r"[\p{L}]+|[\p{N}]|[^\s\p{L}\p{N}]+"
)


def _simple_split(text: str) -> list[str]:
    try:
        import regex as re
        return re.findall(_PAT, text)
    except ImportError:
        # Adequate for short vocabulary phrases: split on whitespace, keep punctuation.
        import re as _re
        return _re.findall(r"[a-z0-9]+|[^\sa-z0-9]+", text)
