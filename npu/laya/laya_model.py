"""Laya (convaiinnovations/laya, typed-decisions checkpoint) rebuilt for a static-shape QNN export.

Same weights and math as upstream rl_common.DecisionModel, with the parts HTP dislikes rewritten:
  - fixed shapes: one question per forward, SEQ_LEN tokens, MAX_OPTS option slots
  - head TransformerEncoderLayers unrolled (no PyTorch MHA fast path / nested tensors)
  - torch.gather on marker positions -> one-hot matmul
  - bool masks passed in as float 0/1
Tokenization / question rendering (build_sequence) is ported verbatim so the host side matches upstream.
"""
import json
import math
import os
from typing import Dict, List, Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

SEQ_LEN = 512
MAX_OPTS = 16
QTYPES = {"choice": 0, "score": 1, "noul": 2}


# ----------------------------------------------------------------------------- host-side rendering (from rl_common)
def serialize_state(state) -> str:
    return state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)


def render_options(q: Dict) -> List[str]:
    t, crit = q["t"], q.get("crit")
    if t == "choice":
        return [k if not v else "%s: %s" % (k, v) for k, v in crit.items()]
    if t == "score":
        return ["level %d: %s" % (i, c) for i, c in enumerate(crit)]
    crit = crit or {}
    return ["false: " + (crit.get("false") or "no, the statement does not hold"),
            "true: " + (crit.get("true") or "yes, the statement holds")]


def build_sequence(tok, state, q: Dict, max_len: int, head_max_len: int):
    """[CLS] <type> instructions [SEP] [MASK] opt0 [MASK] opt1 ... [SEP] state [SEP] -> (ids, marker positions)."""
    mask_tok = tok.mask_token
    opts = render_options(q)
    ins = str(q["ins"]).replace(mask_tok, " ")
    head_ids = tok("%s question: %s" % (q["t"], ins), add_special_tokens=False)["input_ids"]
    opt_ids = [[tok.mask_token_id] + tok(" " + o.replace(mask_tok, " "), add_special_tokens=False)["input_ids"][:48]
               for o in opts]
    opt_budget = head_max_len - sum(len(o) for o in opt_ids)
    if opt_budget < 16:
        per = max(4, (head_max_len - 16) // max(1, len(opt_ids)))
        opt_ids = [o[:per] for o in opt_ids]
        opt_budget = head_max_len - sum(len(o) for o in opt_ids)
    head_ids = head_ids[:max(8, opt_budget)]
    ids = [tok.cls_token_id] + head_ids + [tok.sep_token_id]
    markers = []
    for o in opt_ids:
        markers.append(len(ids))
        ids.extend(o)
    ids.append(tok.sep_token_id)
    room = max(0, max_len - len(ids) - 1)
    st = tok(serialize_state(state).replace(mask_tok, " "), add_special_tokens=False)["input_ids"][:room]
    ids = ids + st + [tok.sep_token_id]
    return ids[:max_len], [m for m in markers if m < max_len]


def to_internal(qdef: Dict) -> Dict:
    """Jev/Laya request shape {"type","instructions","criteria"} -> internal {"t","ins","crit"}."""
    crit = qdef.get("criteria")
    if qdef["type"] == "choice" and isinstance(crit, list):
        crit = {c: None for c in crit}
    ins = qdef["instructions"] if isinstance(qdef["instructions"], str) else json.dumps(qdef["instructions"])
    return {"t": qdef["type"], "ins": ins, "crit": crit}


def encode_question(tok, cfg: Dict, state, qdef: Dict, seq_len: int = SEQ_LEN):
    """One question -> fixed-shape int32/float32 numpy inputs for the exported graph."""
    import numpy as np
    q = to_internal(qdef)
    ids, markers = build_sequence(tok, state, q, seq_len, cfg["head_max_len"])
    k = len(render_options(q))
    if len(markers) != k or k > MAX_OPTS:
        raise ValueError("options do not fit (have %d, fit %d, max %d)" % (k, len(markers), MAX_OPTS))
    input_ids = np.full((1, seq_len), tok.pad_token_id, dtype=np.int32)
    input_ids[0, :len(ids)] = ids
    attn = np.zeros((1, seq_len), dtype=np.float32)
    attn[0, :len(ids)] = 1.0
    mpos = np.zeros((1, MAX_OPTS), dtype=np.int32)
    mpos[0, :k] = markers
    mmask = np.zeros((1, MAX_OPTS), dtype=np.float32)
    mmask[0, :k] = 1.0
    qtype = np.array([QTYPES[q["t"]]], dtype=np.int32)
    return [input_ids, attn, mpos, mmask, qtype], q, k, len(ids)


def temp_bucket(qtype: int, k: int) -> str:
    size = "2" if k <= 2 else "3-5" if k <= 5 else "6-10" if k <= 10 else "11+"
    return "%s:%s" % ({0: "choice", 1: "score", 2: "noul"}[qtype], size)


def postprocess(cfg: Dict, q: Dict, k: int, logits, act_logits) -> Dict:
    """Upstream RLAgent.system_one answer formatting (temperature-calibrated softmax)."""
    import numpy as np
    qt = QTYPES[q["t"]]
    z = np.asarray(logits, dtype=np.float64)[:k]
    z = z / cfg.get("temperature_by_options", {}).get(temp_bucket(qt, k), cfg["temperature"][qt])
    p = np.exp(z - z.max())
    p = p / p.sum()
    a = np.asarray(act_logits, dtype=np.float64)
    act = np.exp(a - a.max())
    act = act / act.sum()
    ext = {"act_probability": round(float(act[0]), 4)}
    conf = 1.0 if k < 2 else float(1 - (-(p * np.log(np.clip(p, 1e-12, 1))).sum()) / math.log(k))
    if q["t"] == "choice":
        keys = list(q["crit"].keys())
        return {"type": "choice", "choice": keys[int(p.argmax())],
                "probabilities": {kk: round(float(v), 4) for kk, v in zip(keys, p)},
                "confidence": round(conf, 4), "rl_agent": ext}
    if q["t"] == "score":
        return {"type": "score", "score": round(float((np.arange(k) * p).sum()), 4),
                "legend": {str(i): c for i, c in enumerate(q["crit"])},
                "probabilities": {str(i): round(float(v), 4) for i, v in enumerate(p)},
                "confidence": round(conf, 4), "rl_agent": ext}
    return {"type": "noul", "noul": round(float(p[1]), 4), "rl_agent": ext}


# ----------------------------------------------------------------------------- export-friendly model
class _HeadLayer(nn.Module):
    """nn.TransformerEncoderLayer(norm_first=True, activation=relu), unrolled; same parameter names."""

    def __init__(self, d: int, nhead: int):
        super().__init__()
        self.nhead = nhead
        self.self_attn = nn.MultiheadAttention(d, nhead, batch_first=True)
        self.linear1 = nn.Linear(d, 4 * d)
        self.linear2 = nn.Linear(4 * d, d)
        self.norm1 = nn.LayerNorm(d)
        self.norm2 = nn.LayerNorm(d)

    def forward(self, x, add_mask):  # add_mask: [1, 1, 1, L] additive
        b, n, d = x.shape
        hd = d // self.nhead
        y = self.norm1(x)
        qkv = F.linear(y, self.self_attn.in_proj_weight, self.self_attn.in_proj_bias)
        q, k, v = qkv.split(d, dim=-1)
        q = q.view(b, n, self.nhead, hd).transpose(1, 2)
        k = k.view(b, n, self.nhead, hd).transpose(1, 2)
        v = v.view(b, n, self.nhead, hd).transpose(1, 2)
        att = torch.softmax((q @ k.transpose(-1, -2)) * (1.0 / math.sqrt(hd)) + add_mask, dim=-1)
        o = (att @ v).transpose(1, 2).reshape(b, n, d)
        x = x + self.self_attn.out_proj(o)
        return x + self.linear2(F.relu(self.linear1(self.norm2(x))))


class _Head(nn.Module):
    def __init__(self, d, nhead, n):
        super().__init__()
        self.layers = nn.ModuleList([_HeadLayer(d, nhead) for _ in range(n)])


class LayaStatic(nn.Module):
    def __init__(self, encoder: nn.Module, head_layers: int = 2, n_act: int = 2):
        super().__init__()
        self.encoder = encoder
        d = encoder.config.hidden_size
        self.head = _Head(d, max(1, d // 64), head_layers)
        self.type_emb = nn.Embedding(3, d)
        self.scorer = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, d), nn.GELU(), nn.Linear(d, 1))
        self.act_head = nn.Sequential(nn.Linear(d + 4, 256), nn.GELU(), nn.Linear(256, n_act))
        self.register_buffer("temperature", torch.ones(3))

    def forward(self, input_ids, attention_mask, marker_pos, marker_mask, qtype):
        # input_ids/marker_pos/qtype: int32; attention_mask/marker_mask: float32 0/1
        h = self.encoder(input_ids=input_ids.long(), attention_mask=attention_mask.long()).last_hidden_state
        h = h + self.type_emb(qtype.long())[:, None, :]
        add_mask = ((1.0 - attention_mask) * -1e4)[:, None, None, :]
        for layer in self.head.layers:
            h = layer(h, add_mask)
        L = h.shape[1]
        onehot = (torch.arange(L, dtype=torch.int32)[None, None, :] == marker_pos[:, :, None]).to(h.dtype)
        m = onehot @ h                                            # [1, K, d]
        logits = self.scorer(m).squeeze(-1)
        logits = logits * marker_mask + (1.0 - marker_mask) * -1e4
        p = torch.softmax(logits, -1)
        k = marker_mask.sum(-1).clamp(min=2.0)
        ent = -(p * torch.log(p.clamp_min(1e-9))).sum(-1) / torch.log(k)
        top2 = p.topk(2, -1).values
        feats = torch.stack([top2[:, 0], top2[:, 0] - top2[:, 1], ent, k / 255.0], -1)
        act_logits = self.act_head(torch.cat([h[:, 0], feats], -1))
        return logits, act_logits


def load_cfg(model_dir: str) -> Dict:
    with open(os.path.join(model_dir, "rl_agent_config.json")) as f:
        return json.load(f)


def load_laya(model_dir: str, attn: str = "eager") -> LayaStatic:
    from safetensors.torch import load_file
    from transformers import AutoConfig, AutoModel
    cfg = load_cfg(model_dir)
    ecfg = AutoConfig.from_pretrained(os.path.join(model_dir, "encoder"))
    enc = AutoModel.from_config(ecfg, attn_implementation=attn)
    model = LayaStatic(enc, cfg["head_layers"], len(cfg["act_costs"]) + 1)
    model.load_state_dict(load_file(os.path.join(model_dir, "model.safetensors")), strict=True)
    return model.eval()


def load_tokenizer(model_dir: str):
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(os.path.join(model_dir, "tokenizer"))
