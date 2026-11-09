# ai_evaluator.py
import os
import sys
import json
import numpy as np
import torch
torch.backends.cudnn.enabled = False  # 1手推論では不要な巨大cuDNNエンジンの動的ロードを無効化

import libriichi

# --- exe化（PyInstaller）時の内部リソースパス解決 ---
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = BASE_DIR


def _resolve_resource(rel_path: str) -> str:
    ext_p = os.path.join(BASE_DIR, rel_path)
    if os.path.exists(ext_p):
        return ext_p
    return os.path.join(BUNDLE_DIR, rel_path)


sys.modules["libriichi.consts"] = libriichi.consts
sys.modules["libriichi.state"] = libriichi.state
sys.modules["libriichi.mjai"] = libriichi.mjai
sys.modules["libriichi.arena"] = libriichi.arena
sys.modules["libriichi.dataset"] = libriichi.dataset

sys.path.append(_resolve_resource(os.path.join("Mortal", "mortal")))

from model import Brain, DQN
from engine import MortalEngine

ACTION_SPACE = [
    "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
    "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
    "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
    "E", "S", "W", "N", "P", "F", "C",
    "5mr", "5pr", "5sr",
    "reach", "chi_low", "chi_mid", "chi_high", "pon", "kan", "hora", "ryukyoku", "none"
]


def resolve_torch_device(device_pref: str = "auto") -> torch.device:
    """'auto' | 'cuda' | 'gpu' | 'cpu' から有効な torch.device を返す"""
    pref = (device_pref or os.environ.get("MAHJONG_DEVICE", "auto")).lower().strip()
    if pref in ("cuda", "gpu", "auto"):
        if torch.cuda.is_available():
            return torch.device("cuda")
    return torch.device("cpu")


def get_device_status_info(current_pref: str = "auto") -> dict:
    cuda_ok = torch.cuda.is_available()
    gpu_name = torch.cuda.get_device_name(0) if cuda_ok else "GPU未検出 (CPUのみ)"
    active_dev = resolve_torch_device(current_pref)
    return {
        "cuda_available": cuda_ok,
        "gpu_name": gpu_name,
        "active_device": str(active_dev),
        "preference": current_pref,
    }


def load_mortal_bot(
    player_id: int,
    weight_path: str = "models/mortal_298k.pth",
    device_pref: str = "auto",
):
    """学習済みモデルを読み込み、指定デバイス(GPU/CPU)で動作するMJAI互換Botインスタンスを返す"""
    resolved_weight = _resolve_resource(weight_path)
    dev = resolve_torch_device(device_pref)
    state = torch.load(resolved_weight, map_location=dev)
    config = state.get("config", {})

    conv_channels = config.get("resnet", {}).get("conv_channels", 192)
    num_blocks = config.get("resnet", {}).get("num_blocks", 40)
    version = config.get("control", {}).get("version", 4)

    brain = Brain(version=version, conv_channels=conv_channels, num_blocks=num_blocks).eval()
    dqn = DQN(version=version).eval()
    brain.load_state_dict(state["mortal"])
    dqn.load_state_dict(state["current_dqn"])

    brain.to(dev)
    dqn.to(dev)

    use_amp = (dev.type == "cuda")
    engine = MortalEngine(
        brain,
        dqn,
        is_oracle=False,
        version=version,
        stochastic_latent=False,
        enable_amp=use_amp,
        enable_quick_eval=False,
        enable_rule_based_agari_guard=True,
        name="MortalTrainer",
        device=dev,
    )
    return libriichi.mjai.Bot(engine, player_id)


def parse_evaluations(mjai_resp_str: str) -> list[dict]:
    if not mjai_resp_str:
        return []
    resp = json.loads(mjai_resp_str)
    meta = resp.get("meta")
    if not meta or "q_values" not in meta or "mask_bits" not in meta:
        return []

    q_vals = np.array(meta["q_values"], dtype=np.float64)
    mask_bits = int(meta["mask_bits"])

    legal_actions = [
        ACTION_SPACE[i] for i in range(len(ACTION_SPACE)) if (mask_bits >> i) & 1
    ]

    temperature = 1.2
    exp_q = np.exp((q_vals - np.max(q_vals)) * temperature)
    probs = (exp_q / np.sum(exp_q)) * 100.0
    relative_scores = exp_q * 100.0

    results = []
    for action_name, q, score, prob in zip(legal_actions, q_vals, relative_scores, probs):
        results.append({
            "action": action_name,
            "q_value": round(float(q), 4),
            "score": round(float(score), 1),
            "prob": round(float(prob), 1),
        })

    results.sort(key=lambda x: x["q_value"], reverse=True)
    return results