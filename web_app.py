import os
import sys
import time
import json
import random
import itertools
import threading
import subprocess
import webbrowser
from datetime import datetime
from collections import Counter, deque
from functools import lru_cache
from threading import Timer
from flask import Flask, jsonify, request, send_file

# --- exe化（PyInstaller）時のパス解決 ＆ コンソール非表示時のクラッシュ防止 ---
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
    os.chdir(BASE_DIR)
    # --noconsole ビルド時に sys.stdout / sys.stderr が None になって落ちるのを防止
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = BASE_DIR

from riichienv import RiichiEnv, GameRule
from ai_evaluator import load_mortal_bot, parse_evaluations
from teachers import (
    get_teacher_ai,
    get_teachers_metadata,
    get_search_presets_metadata,
    get_roman_presets_metadata,  # ← 
    get_speed_presets_metadata,  # ← 追加
)

app = Flask(__name__)

_shutdown_lock = threading.Lock()
_shutdown_token = 0
_session_lock = threading.Lock()


def cancel_pending_shutdown():
    global _shutdown_token
    with _shutdown_lock:
        _shutdown_token += 1


def get_resource_path(filename: str) -> str:
    ext_path = os.path.join(BASE_DIR, filename)
    if os.path.exists(ext_path):
        return ext_path
    return os.path.join(BUNDLE_DIR, filename)


TILES = {
    "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
    "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
    "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
    "E", "S", "W", "N", "P", "F", "C", "5mr", "5pr", "5sr"
}

ALL_34_TILES = [
    "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
    "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
    "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
    "E", "S", "W", "N", "P", "F", "C"
]

YAOCHU_TILES = {"1m", "9m", "1p", "9p", "1s", "9s", "E", "S", "W", "N", "P", "F", "C"}
HONOR_TILES = {"E", "S", "W", "N", "P", "F", "C"}
MIDDLE_TILES = {
    "3m", "4m", "5m", "6m", "7m",
    "3p", "4p", "5p", "6p", "7p",
    "3s", "4s", "5s", "6s", "7s"
}

SORT_ORDER = {
    **{f"{i}m": i for i in range(1, 10)}, "5mr": 5.5,
    **{f"{i}p": i + 10 for i in range(1, 10)}, "5pr": 15.5,
    **{f"{i}s": i + 20 for i in range(1, 10)}, "5sr": 25.5,
    "E": 31, "S": 32, "W": 33, "N": 34, "P": 35, "F": 36, "C": 37
}

ACTION_LABELS = {
    "none": "スルー (パス)",
    "reach": "リーチ",
    "pon": "ポン",
    "chi_low": "チー (小)",
    "chi_mid": "チー (中)",
    "chi_high": "チー (大)",
    "kan": "カン",
    "hora": "🎉 アガリ (ツモ/ロン)",
    "ryukyoku": "九種九牌流局"
}

TRAINING_THEMES = {
    "normal": {
        "name": "通常対局 (ランダム配牌)",
        "desc": "偏りのない通常のランダム配牌で総合力を鍛えます。"
    },
    "honitsu": {
        "name": "混一色・清一色 (染め手特訓)",
        "desc": "特定の数牌スート＋字牌が10枚以上集まった配牌で、染め手の牌効率と多面張を特訓します。"
    },
    "toitsu": {
        "name": "七対子・対々和 (対子手特訓)",
        "desc": "対子・暗刻が4組以上ある配牌で、門前七対子とポンして対々和に向かう分岐判断を特訓します。"
    },
    "tanyao_pinfu": {
        "name": "断幺九・平和 (好形・牌効率特訓)",
        "desc": "中張牌(2〜8)が11枚以上集まった配牌で、複合ターツの捌き方や良形変化を特訓します。"
    },
    "yakuhai": {
        "name": "役牌・速度仕掛け (鳴き判断特訓)",
        "desc": "役牌(白・發・中・風牌)の対子以上が確定した配牌で、仕掛けて速攻するか門前で行くかを特訓します。"
    },
    "dora_heavy": {
        "name": "ドラ3以上 (高打点・押し引き特訓)",
        "desc": "最初からドラ・赤ドラを3枚以上保有した勝負手配牌で、最短のアガリ手順と押し引きを特訓します。"
    },
    "yaochu": {
        "name": "国士・チャンタ・混老頭 (端牌特訓)",
        "desc": "1・9・字牌が9枚以上集まった配牌で、国士無双やチャンタ・混老頭の手作りを特訓します。"
    }
}

SEAT_NAMES = ["自家 (あなた)", "下家 (AI)", "対面 (AI)", "上家 (AI)"]
SEAT_SHORT_NAMES = ["自家(あなた)", "下家", "対面", "上家"]
WIND_CYCLE = ["E", "S", "W", "N"]
WIND_KANJI = {"E": "東", "S": "南", "W": "西", "N": "北", "P": "白", "F": "發", "C": "中"}
SUIT_JA = {"m": "萬", "p": "筒", "s": "索"}


def norm_tile(tile: str) -> str:
    if not tile or not isinstance(tile, str):
        return ""
    if tile in ("5mr", "5pr", "5sr"):
        return tile[:2]
    return tile


def tile_ja(tile: str) -> str:
    if not tile or not isinstance(tile, str):
        return ""
    if tile in ("5mr", "5pr", "5sr"):
        return f"赤5{SUIT_JA[tile[1]]}"
    n = norm_tile(tile)
    if n in WIND_KANJI:
        return WIND_KANJI[n]
    if len(n) == 2 and n[1] in SUIT_JA:
        return f"{n[0]}{SUIT_JA[n[1]]}"
    return ACTION_LABELS.get(tile, tile)


def get_actual_dora(marker: str) -> str:
    m = norm_tile(marker)
    if len(m) == 2 and m[1] in ("m", "p", "s"):
        num = int(m[0])
        next_num = 1 if num == 9 else num + 1
        return f"{next_num}{m[1]}"
    if m in ("E", "S", "W", "N"):
        idx = ["E", "S", "W", "N"].index(m)
        return ["E", "S", "W", "N"][(idx + 1) % 4]
    if m in ("P", "F", "C"):
        idx = ["P", "F", "C"].index(m)
        return ["P", "F", "C"][(idx + 1) % 3]
    return m


def action_to_mjai_dict(act) -> dict:
    if hasattr(act, "to_mjai"):
        try:
            raw = act.to_mjai()
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, str):
                return json.loads(raw)
        except Exception:
            pass
    return {}


def classify_mjai_to_action_code(mj: dict) -> str:
    mtype = mj.get("type", "")
    if mtype == "dahai":
        return mj.get("pai", "")
    if mtype in ("reach", "hora", "ryukyoku", "none", "pon"):
        return mtype
    if mtype in ("daiminkan", "ankan", "kakan"):
        return "kan"
    if mtype == "chi":
        pai = norm_tile(mj.get("pai", ""))
        consumed = [norm_tile(c) for c in mj.get("consumed", [])]
        if len(pai) == 2 and pai[0].isdigit() and len(consumed) == 2:
            p_num = int(pai[0])
            c_nums = sorted(int(c[0]) for c in consumed if len(c) == 2 and c[0].isdigit())
            if len(c_nums) == 2:
                if c_nums == [p_num + 1, p_num + 2]:
                    return "chi_low"
                if c_nums == [p_num - 1, p_num + 1]:
                    return "chi_mid"
                if c_nums == [p_num - 2, p_num - 1]:
                    return "chi_high"
        return "chi_low"
    return ""


def _is_riichi_attached_action(act, mj: dict) -> bool:
    """Actionオブジェクトが暗黙のリーチ宣言を含んでいるか判定する"""
    if mj.get("type") == "reach" or mj.get("reach") or mj.get("is_riichi"):
        return True
    s = f"{act} {getattr(act, 'action_type', '')}".lower()
    return ("riichi" in s or "reach" in s)


def extract_legal_action_map(obs) -> dict[str, list]:
    """obs.legal_actions() から Mortalアクション名 -> [(Action, mjai_dict), ...] の辞書を構築する"""
    mapping: dict[str, list] = {}
    if obs is None:
        return mapping
    for act in obs.legal_actions():
        mj = action_to_mjai_dict(act)
        code = classify_mjai_to_action_code(mj)
        if code:
            mapping.setdefault(code, []).append((act, mj))
    # 通常の打牌コード(TILES)では、リーチが付随していない純粋な打牌(ダマ)を必ず先頭にソートする
    for code, lst in mapping.items():
        if code in TILES and len(lst) > 1:
            lst.sort(key=lambda pair: _is_riichi_attached_action(pair[0], pair[1]))
    return mapping

def find_hora_action_from_obs(obs, actor_pid: int, tracker):
    if obs is None:
        return None
    lmap = extract_legal_action_map(obs)
    if "hora" in lmap and lmap["hora"]:
        return lmap["hora"][0][0]

    drawn = tracker.drawn_tiles.get(actor_pid)
    if drawn is not None:
        for p in [drawn, norm_tile(drawn), f"{norm_tile(drawn)}r"]:
            act = obs.select_action_from_mjai({"type": "hora", "actor": actor_pid, "target": actor_pid, "pai": p})
            if act is not None:
                return act

    last_d = tracker.player_last_discard.get(actor_pid) or tracker.last_discard
    if last_d:
        target = last_d["actor"]
        pai = last_d["pai"]
        for p in [pai, norm_tile(pai), f"{norm_tile(pai)}r"]:
            act = obs.select_action_from_mjai({"type": "hora", "actor": actor_pid, "target": target, "pai": p})
            if act is not None:
                return act

    for a in obs.legal_actions():
        s = f"{a} {getattr(a, 'action_type', '')}".lower()
        if any(k in s for k in ("hora", "tsumo", "ron", "agari")) and "tsumogiri" not in s:
            return a

    return None


@lru_cache(maxsize=32768)
def _analyze_suit_mt(c: tuple[int, ...], idx: int = 0) -> tuple[tuple[int, int], ...]:
    while idx < 9 and c[idx] == 0:
        idx += 1
    if idx >= 9:
        return ((0, 0),)

    results = set()

    if c[idx] >= 3:
        lst = list(c)
        lst[idx] -= 3
        for m, t in _analyze_suit_mt(tuple(lst), idx):
            results.add((m + 1, t))

    if idx <= 6 and c[idx + 1] > 0 and c[idx + 2] > 0:
        lst = list(c)
        lst[idx] -= 1
        lst[idx + 1] -= 1
        lst[idx + 2] -= 1
        for m, t in _analyze_suit_mt(tuple(lst), idx):
            results.add((m + 1, t))

    if c[idx] >= 2:
        lst = list(c)
        lst[idx] -= 2
        for m, t in _analyze_suit_mt(tuple(lst), idx):
            results.add((m, t + 1))

    if idx <= 7 and c[idx + 1] > 0:
        lst = list(c)
        lst[idx] -= 1
        lst[idx + 1] -= 1
        for m, t in _analyze_suit_mt(tuple(lst), idx):
            results.add((m, t + 1))

    if idx <= 6 and c[idx + 2] > 0:
        lst = list(c)
        lst[idx] -= 1
        lst[idx + 2] -= 1
        for m, t in _analyze_suit_mt(tuple(lst), idx):
            results.add((m, t + 1))

    lst = list(c)
    lst[idx] -= 1
    for m, t in _analyze_suit_mt(tuple(lst), idx):
        results.add((m, t))

    best_t_for_m = {}
    for m, t in results:
        if m not in best_t_for_m or t > best_t_for_m[m]:
            best_t_for_m[m] = t
    return tuple(sorted(best_t_for_m.items()))


def _calc_standard_shanten_from_counts(counts: Counter, num_melds: int) -> int:
    target_sets = 4 - num_melds

    def eval_no_pair(cnts: Counter) -> int:
        cm = tuple(cnts[f"{i}m"] for i in range(1, 10))
        cp = tuple(cnts[f"{i}p"] for i in range(1, 10))
        cs = tuple(cnts[f"{i}s"] for i in range(1, 10))
        mt_m = _analyze_suit_mt(cm)
        mt_p = _analyze_suit_mt(cp)
        mt_s = _analyze_suit_mt(cs)

        mz = sum(1 for z in HONOR_TILES if cnts[z] >= 3)
        tz = sum(1 for z in HONOR_TILES if cnts[z] == 2)

        best_val = 0
        for mm, tm in mt_m:
            for mp, tp in mt_p:
                for ms, ts in mt_s:
                    tot_m = min(target_sets, mm + mp + ms + mz)
                    tot_t = tm + tp + ts + tz
                    eff_t = min(tot_t, target_sets - tot_m)
                    val = 2 * tot_m + eff_t
                    if val > best_val:
                        best_val = val
        return best_val

    min_shanten = 8 - 2 * num_melds - eval_no_pair(counts)

    for pair_t, c in list(counts.items()):
        if c >= 2:
            counts[pair_t] -= 2
            val = eval_no_pair(counts)
            counts[pair_t] += 2
            sh = 8 - 2 * num_melds - val - 1
            if sh < min_shanten:
                min_shanten = sh

    return min_shanten


def calc_shanten_all(norm_tiles: list[str], num_melds: int) -> dict:
    counts = Counter(norm_tiles)
    std_s = _calc_standard_shanten_from_counts(counts, num_melds)
    chiitoi_s = 99
    kokushi_s = 99

    if num_melds == 0:
        pairs = sum(1 for v in counts.values() if v >= 2)
        uniq = sum(1 for v in counts.values() if v > 0)
        chiitoi_s = 6 - pairs + max(0, 7 - uniq)

        yc_uniq = sum(1 for t in YAOCHU_TILES if counts[t] > 0)
        yc_pair = 1 if any(counts[t] >= 2 for t in YAOCHU_TILES) else 0
        kokushi_s = 13 - yc_uniq - yc_pair

    min_s = min(std_s, chiitoi_s, kokushi_s)
    return {
        "shanten": min_s,
        "std_shanten": std_s,
        "chiitoi_shanten": chiitoi_s,
        "kokushi_shanten": kokushi_s,
    }


def evaluate_theme_hand_fitness(
    theme: str,
    hand_raw: list[str],
    dora_marker: str,
    bakaze: str,
    oya: int,
    human_pid: int = 0
) -> tuple[bool, float]:
    if theme == "normal" or theme not in TRAINING_THEMES:
        return True, 100.0

    hand_norm = [norm_tile(t) for t in hand_raw]
    counts = Counter(hand_norm)

    if theme == "honitsu":
        honor_cnt = sum(1 for t in hand_norm if t in HONOR_TILES)
        best_suit_cnt = max(
            sum(1 for t in hand_norm if len(t) == 2 and t[1] == s)
            for s in ("m", "p", "s")
        )
        total_honitsu = best_suit_cnt + honor_cnt
        score = total_honitsu * 10.0 + best_suit_cnt * 4.0
        qualified = (best_suit_cnt >= 9) or (best_suit_cnt >= 7 and total_honitsu >= 10)
        return qualified, score

    if theme == "toitsu":
        pairs_cnt = sum(1 for v in counts.values() if v >= 2)
        triplets_cnt = sum(1 for v in counts.values() if v >= 3)
        score = pairs_cnt * 20.0 + triplets_cnt * 15.0
        qualified = (pairs_cnt >= 4) or (pairs_cnt >= 3 and triplets_cnt >= 1)
        return qualified, score

    if theme == "tanyao_pinfu":
        tanyao_cnt = sum(1 for t in hand_norm if t not in YAOCHU_TILES)
        honor_cnt = sum(1 for t in hand_norm if t in HONOR_TILES)
        std_s = _calc_standard_shanten_from_counts(counts, 0)
        score = tanyao_cnt * 10.0 - honor_cnt * 8.0 - std_s * 5.0
        qualified = (tanyao_cnt >= 11 and honor_cnt <= 1 and std_s <= 3)
        return qualified, score

    if theme == "yakuhai":
        jikaze = WIND_CYCLE[(human_pid - oya) % 4]
        yakuhai_set = {"P", "F", "C", bakaze, jikaze}
        yh_pairs = sum(1 for y in yakuhai_set if counts[y] >= 2)
        yh_trips = sum(1 for y in yakuhai_set if counts[y] >= 3)
        std_s = _calc_standard_shanten_from_counts(counts, 0)
        score = yh_pairs * 25.0 + yh_trips * 15.0 - std_s * 6.0
        qualified = (yh_pairs >= 1 and std_s <= 2) or (yh_pairs >= 2)
        return qualified, score

    if theme == "dora_heavy":
        actual_dora = get_actual_dora(dora_marker)
        dora_cnt = sum(1 for t in hand_norm if t == actual_dora)
        red_cnt = sum(1 for t in hand_raw if t in ("5mr", "5pr", "5sr"))
        total_dora = dora_cnt + red_cnt
        std_s = _calc_standard_shanten_from_counts(counts, 0)
        score = total_dora * 30.0 - std_s * 4.0
        qualified = (total_dora >= 3 and std_s <= 3)
        return qualified, score

    if theme == "yaochu":
        yaochu_cnt = sum(1 for t in hand_norm if t in YAOCHU_TILES)
        yaochu_types = sum(1 for t in YAOCHU_TILES if counts[t] > 0)
        score = yaochu_cnt * 8.0 + yaochu_types * 10.0
        qualified = (yaochu_types >= 8) or (yaochu_cnt >= 9)
        return qualified, score

    return True, 100.0


def is_standard_agari(norm_tiles: list[str], num_melds: int) -> tuple[bool, list]:
    needed_sets = 4 - num_melds
    if len(norm_tiles) != needed_sets * 3 + 2:
        return False, []
    counts = Counter(norm_tiles)

    def solve_sets(rem_counts: Counter, sets_left: int, acc: list):
        if sets_left == 0:
            return True, acc
        first = min((t for t, c in rem_counts.items() if c > 0), key=lambda x: SORT_ORDER.get(x, 99))
        if rem_counts[first] >= 3:
            rem_counts[first] -= 3
            ok, res = solve_sets(rem_counts, sets_left - 1, acc + [("koutsu", (first, first, first))])
            if ok:
                return True, res
            rem_counts[first] += 3
        if len(first) == 2 and first[1] in ("m", "p", "s"):
            n = int(first[0])
            s = first[1]
            if n <= 7:
                t2, t3 = f"{n+1}{s}", f"{n+2}{s}"
                if rem_counts[t2] > 0 and rem_counts[t3] > 0:
                    rem_counts[first] -= 1
                    rem_counts[t2] -= 1
                    rem_counts[t3] -= 1
                    ok, res = solve_sets(rem_counts, sets_left - 1, acc + [("shuntsu", (first, t2, t3))])
                    if ok:
                        return True, res
                    rem_counts[first] += 1
                    rem_counts[t2] += 1
                    rem_counts[t3] += 1
        return False, []

    for pair_tile, c in list(counts.items()):
        if c >= 2:
            counts[pair_tile] -= 2
            ok, decomp = solve_sets(counts, needed_sets, [("pair", (pair_tile, pair_tile))])
            counts[pair_tile] += 2
            if ok:
                return True, decomp
    return False, []


def get_visible_tile_counts(tracker) -> Counter:
    counts = Counter()
    for pid in range(4):
        for d in tracker.discards[pid]:
            counts[norm_tile(d["pai"])] += 1
    for pid in range(4):
        for m in tracker.melds[pid]:
            mtype = m.get("type", "")
            tiles = m.get("tiles", [])
            if mtype in ("ポン", "チー", "明槓", "カン", "加槓"):
                for t in tiles[1:]:
                    counts[norm_tile(t)] += 1
            else:
                for t in tiles:
                    counts[norm_tile(t)] += 1
    for d in tracker.dora_markers:
        counts[norm_tile(d)] += 1
    h_pid = tracker.human_pid
    for t in tracker.hands[h_pid]:
        counts[norm_tile(t)] += 1
    if tracker.drawn_tiles[h_pid] is not None:
        counts[norm_tile(tracker.drawn_tiles[h_pid])] += 1
    return counts


def calc_ukeire_for_13(
    norm_13: list[str],
    num_melds: int,
    visible_counts: Counter,
    my_discard_set: set[str]
) -> dict:
    s_info = calc_shanten_all(norm_13, num_melds)
    cur_s = s_info["shanten"]
    hand_counts = Counter(norm_13)

    ukeire_tiles = []
    for cand in ALL_34_TILES:
        if hand_counts[cand] >= 4:
            continue
        next_s = calc_shanten_all(norm_13 + [cand], num_melds)["shanten"]
        if next_s < cur_s:
            rem = max(0, 4 - visible_counts[cand])
            in_river = cand in my_discard_set
            ukeire_tiles.append({
                "tile": cand,
                "remaining": rem,
                "in_river": in_river
            })

    furiten_tiles = [w["tile"] for w in ukeire_tiles if w["in_river"]] if cur_s == 0 else []
    return {
        "shanten": cur_s,
        "std_shanten": s_info["std_shanten"],
        "chiitoi_shanten": s_info["chiitoi_shanten"],
        "kokushi_shanten": s_info["kokushi_shanten"],
        "is_tenpai": (cur_s == 0),
        "waits": ukeire_tiles,
        "ukeire_types": len(ukeire_tiles),
        "total_remaining": sum(w["remaining"] for w in ukeire_tiles),
        "is_furiten": (cur_s == 0 and len(furiten_tiles) > 0),
        "furiten_tiles": furiten_tiles,
    }


def analyze_hand_shanten_and_ukeire(tracker) -> dict:
    pid = tracker.human_pid
    num_melds = len(tracker.melds[pid])
    my_discard_set = {norm_tile(d["pai"]) for d in tracker.discards[pid]}
    visible_counts = get_visible_tile_counts(tracker)

    hand_13_raw = list(tracker.hands[pid])
    hand_13_norm = [norm_tile(t) for t in hand_13_raw]
    drawn_raw = tracker.drawn_tiles[pid]

    base_13 = calc_ukeire_for_13(hand_13_norm, num_melds, visible_counts, my_discard_set)

    full_raw = hand_13_raw + ([drawn_raw] if drawn_raw is not None else [])
    expected_14_len = (4 - num_melds) * 3 + 2

    discard_candidates = []
    discard_ukeire_map = {}

    if len(full_raw) == expected_14_len:
        seen_raw = set()
        ordered_cands = sorted(full_raw, key=lambda x: SORT_ORDER.get(x, 99))
        for cand_raw in ordered_cands:
            if cand_raw in seen_raw:
                continue
            seen_raw.add(cand_raw)

            after_raw = list(full_raw)
            after_raw.remove(cand_raw)
            after_norm = [norm_tile(t) for t in after_raw]

            after_discard_set = my_discard_set | {norm_tile(cand_raw)}
            res = calc_ukeire_for_13(after_norm, num_melds, visible_counts, after_discard_set)

            entry = {
                "discard": cand_raw,
                "discard_norm": norm_tile(cand_raw),
                "shanten": res["shanten"],
                "std_shanten": res["std_shanten"],
                "chiitoi_shanten": res["chiitoi_shanten"],
                "is_tenpai": res["is_tenpai"],
                "waits": res["waits"],
                "ukeire_types": res["ukeire_types"],
                "total_remaining": res["total_remaining"],
                "is_furiten": res["is_furiten"],
                "furiten_tiles": res["furiten_tiles"],
            }
            discard_candidates.append(entry)
            discard_ukeire_map[cand_raw] = entry
            if norm_tile(cand_raw) not in discard_ukeire_map:
                discard_ukeire_map[norm_tile(cand_raw)] = entry

        min_shanten_after = min(d["shanten"] for d in discard_candidates) if discard_candidates else base_13["shanten"]
        for d in discard_candidates:
            d["is_shanten_back"] = (d["shanten"] > min_shanten_after)

        discard_candidates.sort(
            key=lambda x: (-x["shanten"], not x["is_furiten"], x["total_remaining"], x["ukeire_types"]),
            reverse=True
        )
    else:
        min_shanten_after = base_13["shanten"]

    tenpai_discards = [d for d in discard_candidates if d["is_tenpai"]]
    best_ukeire_discards = [d for d in discard_candidates if not d["is_shanten_back"]]

    return {
        "current_shanten": min_shanten_after,
        "std_shanten": base_13["std_shanten"],
        "chiitoi_shanten": base_13["chiitoi_shanten"],
        "kokushi_shanten": base_13["kokushi_shanten"],
        "is_13_tenpai": base_13["is_tenpai"],
        "base_waits": base_13["waits"],
        "base_total_remaining": base_13["total_remaining"],
        "base_is_furiten": base_13["is_furiten"],
        "base_furiten_tiles": base_13["furiten_tiles"],
        "has_tenpai_discards": len(tenpai_discards) > 0,
        "tenpai_discards": tenpai_discards,
        "best_ukeire_discards": best_ukeire_discards[:6],
        "discard_ukeire_map": discard_ukeire_map,
    }


def shanten_ja(s: int) -> str:
    if s <= -1:
        return "和了形"
    if s == 0:
        return "聴牌（テンパイ）"
    return f"{s}シャンテン"


def _format_wait_tiles_summary(waits: list[dict], max_items: int = 6) -> str:
    if not waits:
        return "なし"
    parts = [f"{tile_ja(w['tile'])}(残{w['remaining']})" for w in waits[:max_items]]
    if len(waits) > max_items:
        parts.append(f"他{len(waits) - max_items}種")
    return "・".join(parts)

# --- 465行目〜486行目付近を以下に差し替え ---
def format_hand_tenhou_style(tiles_raw: list[str]) -> str:
    """手牌リストを 123m 45r6p 789s 東東白 のような1行テキストに整形する"""
    m_part, p_part, s_part, z_part = [], [], [], []
    for t in sorted(tiles_raw, key=lambda x: SORT_ORDER.get(x, 99)):
        if t.endswith("m") or t == "5mr":
            m_part.append("0" if t == "5mr" else t[0])
        elif t.endswith("p") or t == "5pr":
            p_part.append("0" if t == "5pr" else t[0])
        elif t.endswith("s") or t == "5sr":
            s_part.append("0" if t == "5sr" else t[0])
        else:
            z_part.append(tile_ja(t))
    blocks = []
    if m_part:
        blocks.append("".join(m_part) + "m")
    if p_part:
        blocks.append("".join(p_part) + "p")
    if s_part:
        blocks.append("".join(s_part) + "s")
    if z_part:
        blocks.append(" ".join(z_part))
    return " ".join(blocks) + (" (※0は赤5)" if any(x in ("5mr", "5pr", "5sr") for x in tiles_raw) else "")


def build_llm_question_prompt(
    tracker,
    evals: list[dict],
    tenpai_info: dict,
    danger_info: dict,
    potential_yakus: list[dict],
    user_choice: str = None,
    teacher_name: str = "標準型 (Mortal 純粋評価)",  # ← 引数追加
) -> str:
    """現在の盤面状態とMortal評価値を網羅した、生成AI質問用のプロンプト文字列を構築する"""
    if not evals:
        return "現在評価対象の手番データがありません。"

    bakaze_ja = WIND_KANJI.get(tracker.bakaze, tracker.bakaze)
    turn_num = len(tracker.discards[tracker.human_pid]) + 1
    dora_markers_ja = "・".join(tile_ja(d) for d in tracker.dora_markers)
    actual_doras_ja = "・".join(tile_ja(get_actual_dora(d)) for d in tracker.dora_markers)

    # 1. 4人の点数・親子・リーチ・副露・捨て牌（河）の整形
    players_lines = []
    for pid in range(4):
        w_code = WIND_CYCLE[(pid - tracker.oya) % 4]
        w_ja = WIND_KANJI.get(w_code, w_code)
        oya_str = "親" if pid == tracker.oya else "子"
        riichi_str = "【🔥リーチ中】" if tracker.riichi[pid] else "門前"
        melds_list = tracker.melds[pid]
        if melds_list:
            m_str = "副露: " + " / ".join(
                f"{m['type']}[{','.join(tile_ja(x) for x in m['tiles'])}]" for m in melds_list
            )
        else:
            m_str = "副露なし"

        discards_list = tracker.discards[pid]
        if discards_list:
            d_items = []
            for d in discards_list:
                mark = ""
                if d.get("is_riichi"):
                    mark += "(リーチ宣言)"
                elif d.get("tsumogiri"):
                    mark += "(ツモ切)"
                d_items.append(f"{tile_ja(d['pai'])}{mark}")
            river_str = " → ".join(d_items)
        else:
            river_str = "なし"

        players_lines.append(
            f"- {SEAT_NAMES[pid]} [{w_ja}家・{oya_str}] 持ち点:{tracker.scores[pid]:,}点 | {riichi_str} | {m_str}\n"
            f"  河({len(discards_list)}枚): {river_str}"
        )

    # 2. 自家の手牌・ツモ牌・鳴き対象牌の整形
    h_pid = tracker.human_pid
    sorted_hand = sorted(tracker.hands[h_pid], key=lambda x: SORT_ORDER.get(x, 99))
    drawn_now = tracker.drawn_tiles[h_pid]
    valid_names = [m["action"] for m in evals]
    is_discard_phase = any(name in TILES for name in valid_names) or (drawn_now is not None)

    hand_ja_str = " ".join(tile_ja(t) for t in sorted_hand)
    full_tiles = sorted_hand + ([drawn_now] if drawn_now else [])
    tenhou_str = format_hand_tenhou_style(full_tiles)

    if is_discard_phase:
        tsumo_str = f"ツモ [{tile_ja(drawn_now)}]" if drawn_now else "（副露直後の打牌選択）"
        situation_type_str = f"自家の手番（打牌・リーチ等の選択） / {tsumo_str}"
    else:
        last_d = tracker.last_discard
        call_target_str = f"{SEAT_NAMES[last_d['actor']]}の打 [{tile_ja(last_d['pai'])}]" if last_d else "他家の打牌"
        situation_type_str = f"他家の打牌に対する鳴き（副露）・アガリ・スルー判断 / 対象: {call_target_str}"

    # 3. シャンテン数・狙えそうな役
    cur_sh = tenpai_info.get("current_shanten", 0)
    sh_detail = (
        f"現在 {shanten_ja(cur_sh)} "
        f"（一般手:{shanten_ja(tenpai_info.get('std_shanten', 99))} / "
        f"七対子:{shanten_ja(tenpai_info.get('chiitoi_shanten', 99))} / "
        f"国士:{shanten_ja(tenpai_info.get('kokushi_shanten', 99))}）"
    )
    yaku_str = "、".join(f"{y['name']}({y['han']})" for y in (potential_yakus or [])[:5]) or "特になし"

    # 4. Mortal AIの上位候補（最大5候補）の詳細データ
    u_map = tenpai_info.get("discard_ukeire_map", {})
    d_map = danger_info.get("tiles", {}) if danger_info.get("active") else {}

    eval_lines = []
    for idx, m in enumerate(evals[:5]):
        act_code = m["action"]
        act_ja = f"打 {tile_ja(act_code)}" if act_code in TILES else tile_ja(act_code)
        u_entry = u_map.get(act_code) or u_map.get(norm_tile(act_code))
        d_entry = d_map.get(act_code)

        extra_parts = []
        if u_entry:
            w_summary = _format_wait_tiles_summary(u_entry.get("waits", []), max_items=8)
            back_tag = " [※シャンテン戻し]" if u_entry.get("is_shanten_back") else ""
            furiten_tag = " [🚨フリテン]" if u_entry.get("is_furiten") else ""
            extra_parts.append(
                f"打牌後:{shanten_ja(u_entry['shanten'])}{back_tag}{furiten_tag} / "
                f"有効牌:{u_entry['ukeire_types']}種{u_entry['total_remaining']}枚({w_summary})"
            )
        if d_entry:
            extra_parts.append(f"守備危険度:{d_entry['badge']}(リスク値:{d_entry['risk_score']})")
        # ▼ 以下3行を追加：教師AI固有のMCTS探索結果や元のMortal順位をプロンプトに含める
        if m.get("teacher_note"):
            m_rank_str = f"(Mortal元順位:{m['mortal_rank']}位/{m['mortal_score']}点) " if m.get("mortal_rank") else ""
            extra_parts.append(f"{m_rank_str}{m['teacher_note']}")

        extra_str = " ｜ " + " ｜ ".join(extra_parts) if extra_parts else ""
        eval_lines.append(
            f"  第{idx+1}位: 【{act_ja}】 (評価点:{m['score']}点 / AI推奨度:{m['prob']}% / Q値:{m['q_value']:+.4f}){extra_str}"
        )

    best_act_code = evals[0]["action"]
    best_act_ja = f"打 {tile_ja(best_act_code)}" if best_act_code in TILES else tile_ja(best_act_code)

    # 5. ユーザーの選択手がある場合の比較質問文
    if user_choice:
        user_act_ja = f"打 {tile_ja(user_choice)}" if user_choice in TILES else tile_ja(user_choice)
        user_eval = next((m for m in evals if m["action"] == user_choice), None)
        user_rank = (evals.index(user_eval) + 1) if user_eval else "?"
        user_score = user_eval["score"] if user_eval else "?"
        if user_choice == best_act_code:
            question_focus = (
                f"私はAI第1位と同じ【{best_act_ja}】を選択しましたが、なぜこの手が第2位以下の候補よりも優れているのか、"
                f"その麻雀理論上の根拠をより深く理解したいです。"
            )
        else:
            question_focus = (
                f"私はこの局面で【{user_act_ja}】（AI評価 第{user_rank}位・{user_score}点）を選びましたが、"
                f"Mortal AIの最善手（第1位）は【{best_act_ja}】（100点）でした。\n"
                f"なぜ私の選んだ【{user_act_ja}】よりも【{best_act_ja}】の方が優れているのでしょうか？"
            )
    else:
        question_focus = (
            f"この局面でMortal AIは第1位として【{best_act_ja}】を推奨しています。\n"
            f"なぜ第2位以下の候補ではなく【{best_act_ja}】が最善となるのでしょうか？"
        )

    prompt_text = f"""以下の四人麻雀（天鳳ルール：赤あり東南戦・25000点持ち30000点返し）の局面において、現在の教師AI「{teacher_name}」が第1位の手を推奨した理由を詳しく解説してください。
■ 1. 場況・局情報
- 局: {bakaze_ja}{tracker.kyoku}局 {tracker.honba}本場 （供託リーチ棒: {tracker.kyotaku}本）
- 巡目: {turn_num}巡目 （残りツモ山: {tracker.wall_remaining}枚）
- ドラ表示牌: {dora_markers_ja} （実ドラ: {actual_doras_ja}）
- 局面種別: {situation_type_str}

■ 2. 各プレイヤーの点数・副露・捨て牌（河）
{chr(10).join(players_lines)}

■ 3. 自家（あなた）の手牌と現在の状態
- 純手牌: {hand_ja_str}
- ツモ牌: {tile_ja(drawn_now) if drawn_now else "なし"}
- 1行表記: {tenhou_str}
- シャンテン数: {sh_detail}
- 狙えそうな手役・ドラ: {yaku_str}
- 他家への守備警戒: {danger_info.get("summary_text", "なし")}

■ 4. Mortal AIの評価ランキング（上位候補）
{chr(10).join(eval_lines)}

■ 5. 質問したいこと
{question_focus}

以下の観点から、中級者にもわかりやすく論理的に解説をお願いします：
1. 【牌効率・受け入れ・最終形の質】（シャンテン数、受け入れ枚数、次巡以降の良形変化や複合ターツの扱い）
2. 【打点・手役・ドラの活用】（リーチ・タンヤオ・平和・役牌・ドラ等の打点期待値）
3. 【場況・押し引き・守備（安全度）】（巡目、点数状況、他家の河やリーチ・副露に対する危険度や安牌温存のバランス）"""

    return prompt_text

def generate_ai_comparison_explanation(
    user_choice: str,
    evals: list[dict],
    tracker,
    tenpai_info: dict,
    danger_info: dict
) -> list[str]:
    """
    自分の選択とAI最善手を5軸（①評価値・確信度、②牌効率・有効牌内訳、③リーチ/副露判断、④打点・手役構想、⑤守備・押し引き）で詳細比較する
    """
    if not evals:
        return []

    best_item = evals[0]
    best_action = best_item["action"]
    user_item = next((m for m in evals if m["action"] == user_choice), best_item)
    user_rank = evals.index(user_item) + 1 if user_item in evals else 1

    u_map = tenpai_info.get("discard_ukeire_map", {})
    d_map = danger_info.get("tiles", {}) if danger_info.get("active") else {}
    dora_set = {get_actual_dora(m) for m in tracker.dora_markers}
    turn_num = len(tracker.discards[tracker.human_pid]) + 1

    u_user = u_map.get(user_choice) or u_map.get(norm_tile(user_choice))
    u_best = u_map.get(best_action) or u_map.get(norm_tile(best_action))

    d_user = d_map.get(user_choice)
    d_best = d_map.get(best_action)

    reasons = []

    # ▼ 追加：カスタム教師AI（探索型など）の固有コメント・Mortal順位との比較を先頭に表示
    if best_item.get("teacher_note"):
        user_t_note = user_item.get("teacher_note", "")
        if user_choice == best_action:
            reasons.append(f"🎓 【教師AI解析】 最善手 [{tile_ja(best_action)}] — {best_item['teacher_note']}")
        else:
            reasons.append(
                f"🎓 【教師AI解析比較】\n"
                f" ・AI第1位 [{tile_ja(best_action)}]: {best_item['teacher_note']}\n"
                f" ・あなたの選択 [{tile_ja(user_choice)}]: {user_t_note or '探索対象外'}"
            )

    # --- 1. AI第1位と完全一致したケースの詳細解説 ---
    if user_choice == best_action:
        second_gap_str = ""
        if len(evals) >= 2:
            sec = evals[1]
            score_gap = round(best_item["score"] - sec["score"], 1)
            q_gap = round(best_item["q_value"] - sec["q_value"], 3)
            if score_gap >= 35:
                second_gap_str = f"（2候補 [{tile_ja(sec['action'])}] にスコア差 {score_gap}点・Q値差 +{q_gap} をつける圧倒的な一手です）"
            elif score_gap >= 12:
                second_gap_str = f"（2候補 [{tile_ja(sec['action'])}]({sec['score']}点) に対して明確な優位があります）"
            else:
                second_gap_str = f"（2候補 [{tile_ja(sec['action'])}]({sec['score']}点) との微差の比較を制した最善手です）"

        if user_choice == "hora":
            reasons.append(f"🎉 【和了判断】ツモまたはロンで確実にアガリを決める最善の選択です！{second_gap_str}")
        elif user_choice in TILES and u_best:
            max_rem = max((x["total_remaining"] for x in u_map.values() if not x.get("is_shanten_back")), default=0)
            waits_str = _format_wait_tiles_summary(u_best.get("waits", []))
            if not u_best.get("is_shanten_back") and u_best["total_remaining"] >= max_rem:
                reasons.append(
                    f"📐 【牌効率・最大受け入れ】{shanten_ja(u_best['shanten'])}を維持しつつ、"
                    f"有効牌が最大の「{u_best['ukeire_types']}種 {u_best['total_remaining']}枚（{waits_str}）」となる正着打です。{second_gap_str}"
                )
            elif u_best.get("is_shanten_back"):
                reasons.append(
                    f"📐 【高度なシャンテン戻し】一時的に{shanten_ja(u_best['shanten'])}（有効牌 {u_best['ukeire_types']}種 {u_best['total_remaining']}枚）へ戻しますが、"
                    f"愚形解消・手役（打点）・守備のバランスを重視したMortal AI推奨の柔軟な構想です。{second_gap_str}"
                )
            else:
                reasons.append(
                    f"📐 【良形・打点・守備バランス】瞬間的な最大枚数（{max_rem}枚）ではありませんが、"
                    f"有効牌 {u_best['ukeire_types']}種 {u_best['total_remaining']}枚（{waits_str}）を確保しつつ、"
                    f"次巡以降の良形変化や打点・安全度を加味した最善打です。{second_gap_str}"
                )
            if d_best:
                reasons.append(f"🛡️ 【守備評価】警戒相手に対して「{d_best['badge']}（危険度:{d_best['risk_score']}）」の牌を選択し、攻守のバランスが取れています。")
        elif user_choice == "reach":
            w_cnt = tenpai_info.get("base_total_remaining", 0)
            reasons.append(
                f"🔥 【リーチ判断（{turn_num}巡目）】先制・打点上昇（リーチ・ツモ・裏ドラ・一発）と他家の押し返し抑止の観点から即リーチが最善です"
                f"（現在の待ち残り計 {w_cnt}枚）。{second_gap_str}"
            )
        elif user_choice == "none":
            reasons.append(
                f"🛑 【スルー判断（{turn_num}巡目）】ここで安易に仕掛けず門前を維持することで、"
                f"リーチによる高打点化と守備力（手牌枚数）を保つ最善のスルー判断です。{second_gap_str}"
            )
        else:
            reasons.append(
                f"⚡ 【副露・仕掛け判断（{turn_num}巡目）】「{tile_ja(user_choice)}」で手を進め、"
                f"シャンテン数を短縮してアガリ率・局収支を最大化する最善の判断です。{second_gap_str}"
            )
        return reasons

    # --- 2. AI最善手と異なる手を選んだケース：まず評価差・確信度を明示 ---
    score_diff = round(best_item["score"] - user_item["score"], 1)
    q_diff = round(best_item["q_value"] - user_item["q_value"], 3)
    prob_diff = round(best_item["prob"] - user_item["prob"], 1)

    if score_diff < 12:
        severity = "【微差・どちらも有力】"
        sev_desc = "局収支（Q値）の差は小さく、実戦ではどちらも十分に成立するバランス判断の範囲内です。"
    elif score_diff < 35:
        severity = "【明確な優劣あり】"
        sev_desc = "AI最善手の方が受け入れ・打点・安全度の総合期待値で明確に上回っています。"
    else:
        severity = "【大差・要改善ポイント】"
        sev_desc = "局収支が大きく損なわれる選択です。以下の理由からAI最善手の手順を強く推奨します。"

    reasons.append(
        f"📊 {severity} あなたの選択 [{tile_ja(user_choice)}]（{user_rank}位 / {user_item['score']}点 / 推奨度{user_item['prob']}%）に対し、"
        f"AI最善は [{tile_ja(best_action)}]（1位 / 100点 / 推奨度{best_item['prob']}% / Q値差 +{q_diff}）です。{sev_desc}"
    )

    if best_action == "hora" and user_choice != "hora":
        reasons.append("🎉 【和了見逃し注意】現在ツモまたはロンで和了（アガリ）が可能です！「🎉 アガリ」ボタン（またはツモ牌）を選択してください。")
        return reasons

    # --- 3. リーチ判断・副露（鳴き）判断の詳細解説 ---
    if best_action == "reach" and user_choice != "reach":
        w_str = _format_wait_tiles_summary(tenpai_info.get("base_waits", []))
        w_rem = tenpai_info.get("base_total_remaining", 0)
        reasons.append(
            f"🔥 【リーチ判断の差（{turn_num}巡目）】あなたはダマテン（または手直し）を選びましたが、"
            f"現在の待ち（{w_str} / 残り計{w_rem}枚）であれば、「リーチ宣言」による打点上昇（裏ドラ・一発・ツモ）と"
            f"他家をオリに向かわせる封殺メリットの方がはるかに大きいとAIは判断しています。"
        )
    elif user_choice == "reach" and best_action != "reach":
        reasons.append(
            f"🔥 【リーチ判断の差（{turn_num}巡目）】あなたはリーチを選びましたが、AIは現在の待ち枚数・打点・良形への手替わり・他家の危険度を総合し、"
            f"リーチで手を固定せず「打 {tile_ja(best_action)}」（ダマテン維持またはテンパイ外し）とする方を高く評価しています。"
        )
    elif (best_action == "none" and user_choice in ("pon", "chi_low", "chi_mid", "chi_high", "kan")):
        cur_sh = tenpai_info.get("current_shanten", 1)
        reasons.append(
            f"🛑 【鳴き判断の差（{turn_num}巡目・現在{shanten_ja(cur_sh)}）】あなたは「{tile_ja(user_choice)}」で仕掛けましたが、"
            f"この牌から鳴くと「打点の大幅低下（リーチ・ツモ消滅）」「手牌が短くなることによる守備力低下」「残る愚形ターツの苦しさ」というデメリットが勝るため、"
            f"AIは「スルー（門前維持）」を1位に推奨しています。"
        )
    elif (user_choice == "none" and best_action in ("pon", "chi_low", "chi_mid", "chi_high", "kan")):
        cur_sh = tenpai_info.get("current_shanten", 1)
        reasons.append(
            f"⚡ 【鳴き判断の差（{turn_num}巡目・現在{shanten_ja(cur_sh)}）】あなたはスルーしましたが、この急所牌は門前で自力ツモを待つよりも"
            f"「{tile_ja(best_action)}」で積極的に仕掛けてシャンテン数を進め、速度（アガリ率・連荘や他家潰し）を最優先すべき局面です。"
        )

    # --- 4. 牌効率・シャンテン数・具体的な受け入れ牌の比較 ---
    if u_user and u_best:
        best_waits_str = _format_wait_tiles_summary(u_best.get("waits", []))
        user_waits_str = _format_wait_tiles_summary(u_user.get("waits", []))

        if u_user["shanten"] > u_best["shanten"]:
            reasons.append(
                f"📐 【シャンテン数の差】あなたの打 {tile_ja(user_choice)} は {shanten_ja(u_user['shanten'])} に後退（シャンテン戻し）してしまいますが、"
                f"AI最善の打 {tile_ja(best_action)} なら {shanten_ja(u_best['shanten'])} を維持できます"
                f"（有効牌 {u_best['ukeire_types']}種 {u_best['total_remaining']}枚：{best_waits_str}）。"
            )
        elif u_user["shanten"] < u_best["shanten"]:
            reasons.append(
                f"📐 【AIのシャンテン戻し構想】目先のシャンテン数はあなたの打 {tile_ja(user_choice)}（{shanten_ja(u_user['shanten'])} / 残{u_user['total_remaining']}枚）の方が進んでいますが、"
                f"愚形ターツや役なし・低打点で手が詰まるのを防ぐため、AIはあえて打 {tile_ja(best_action)}（{shanten_ja(u_best['shanten'])} / 有効牌:{best_waits_str}）として好形・高打点への組み替えや守備を狙っています。"
            )
        else:
            diff_rem = u_best["total_remaining"] - u_user["total_remaining"]
            diff_types = u_best["ukeire_types"] - u_user["ukeire_types"]
            if diff_rem > 0:
                reasons.append(
                    f"📐 【受け入れ枚数の差（+{diff_rem}枚）】同じ {shanten_ja(u_best['shanten'])} ですが、"
                    f"あなたの打 {tile_ja(user_choice)}（{u_user['ukeire_types']}種 {u_user['total_remaining']}枚：{user_waits_str}）よりも、"
                    f"AI最善の打 {tile_ja(best_action)}（{u_best['ukeire_types']}種 {u_best['total_remaining']}枚：{best_waits_str}）の方が"
                    f"受け入れが {diff_types:+}種・+{diff_rem}枚 広く、次巡の進行率が明確に高くなります！"
                )
            elif diff_rem < 0:
                reasons.append(
                    f"📐 【枚数より良形変化・打点・安全度を優先】直後の有効牌枚数はあなたの打 {tile_ja(user_choice)}（{u_user['ukeire_types']}種 {u_user['total_remaining']}枚）の方が"
                    f"AI最善の打 {tile_ja(best_action)}（{u_best['ukeire_types']}種 {u_best['total_remaining']}枚：{best_waits_str}）より {abs(diff_rem)}枚 多いですが、"
                    f"AIは「最終形が両面待ちになる確率」「ドラや手役（タンヤオ・三色等）の打点期待値」「他家への安全度」から打 {tile_ja(best_action)} を上位に評価しています。"
                )
            else:
                reasons.append(
                    f"📐 【同枚数での牌理・価値比較】どちらも {shanten_ja(u_best['shanten'])}（有効牌 {u_best['ukeire_types']}種 {u_best['total_remaining']}枚）で直接の枚数は同じですが、"
                    f"AIは「周辺牌を引いた際の両面変化（くっつき性能）」「場に見えている枚数」「他家に対する安全度」の差から 打 {tile_ja(best_action)} を最善としています。"
                )

            if u_user.get("is_furiten") and not u_best.get("is_furiten"):
                reasons.append(
                    f"🚨 【フリテン回避】あなたの打 {tile_ja(user_choice)} は自分の河にある牌（{','.join(tile_ja(x) for x in u_user.get('furiten_tiles', []))}）が絡むフリテン聴牌（ロン不可）になりますが、"
                    f"AI最善の打 {tile_ja(best_action)} ならフリテンにならずに出アガリ（ロン）が可能です！"
                )

    # --- 5. 打点・ドラ・手役・牌の価値の比較 ---
    if user_choice in TILES and best_action in TILES:
        user_is_dora = (norm_tile(user_choice) in dora_set) or (user_choice in ("5mr", "5pr", "5sr"))
        best_is_dora = (norm_tile(best_action) in dora_set) or (best_action in ("5mr", "5pr", "5sr"))
        if user_is_dora and not best_is_dora:
            reasons.append(
                f"💰 【ドラ・赤ドラの保持】打 {tile_ja(user_choice)} は貴重なドラを手放すため打点が1飜（約1.5〜2倍）低下し、他家に鳴かれるリスクもあります。"
                f"AIは打 {tile_ja(best_action)} としてドラを使い切る構想を採っています。"
            )
        elif best_is_dora and not user_is_dora:
            reasons.append(
                f"💰 【リスクの高い浮きドラ処理】AIはドラである {tile_ja(best_action)} をあえて先に処理しています。"
                f"手牌に組み込みにくい孤立ドラを抱えて形を崩したり、終盤に他家のリーチへ放銃するリスクを回避する判断です。"
            )
        elif norm_tile(user_choice) in MIDDLE_TILES and norm_tile(best_action) in YAOCHU_TILES:
            reasons.append(
                f"💰 【中張牌のくっつき・タンヤオ/ピンフ意識】中張牌（3〜7）の {tile_ja(user_choice)} は両面ターツを作りやすい要の牌です。"
                f"AIは端・字牌の {tile_ja(best_action)} を先に処理することで、良形変化とタンヤオ・ピンフの可能性を最大化しています。"
            )
        elif norm_tile(user_choice) in HONOR_TILES and norm_tile(best_action) not in HONOR_TILES:
            reasons.append(
                f"🛡️ 【安全牌（字牌）の温存とスリム化】あなたは字牌の {tile_ja(user_choice)} を切りましたが、"
                f"AIは手牌進行に不要な数牌 {tile_ja(best_action)} を先に処理し、字牌の {tile_ja(user_choice)} を他家リーチに備えた守備駒（または重なりでの役牌対子候補）として温存しています。"
            )

    # --- 6. 守備・放銃リスクと押し引きの比較 ---
    if d_user and d_best:
        risk_diff = d_user["risk_score"] - d_best["risk_score"]
        threat_str = "・".join(danger_info.get("threat_names", ["他家"]))
        if risk_diff >= 10:
            reasons.append(
                f"🛡️ 【守備・放銃リスクの差（対 {threat_str}）】あなたの打 {tile_ja(user_choice)} は「{d_user['badge']}（危険度:{d_user['risk_score']}）」と放銃リスクが高い牌ですが、"
                f"AI最善の打 {tile_ja(best_action)} は「{d_best['badge']}（危険度:{d_best['risk_score']}）」であり、放銃失点リスクを大幅に抑えられます！\n"
                f" ・打 {tile_ja(best_action)} の守備根拠：{d_best['reason']}"
            )
        elif risk_diff <= -10:
            reasons.append(
                f"⚔️ 【押し引き（攻め）の判断（対 {threat_str}）】安全度自体はあなたの打 {tile_ja(user_choice)}（{d_user['badge']} / 危険度:{d_user['risk_score']}）の方が高いですが、"
                f"AIは自分の手牌価値（シャンテン数・受け入れ枚数・打点）が放銃リスクに見合うと判断し、危険度:{d_best['risk_score']}（{d_best['badge']}）の打 {tile_ja(best_action)} で真っ直ぐ押し返す手順を選んでいます。"
            )

    return reasons


def evaluate_single_tile_danger(
    tile_raw: str,
    target_pid: int,
    tracker,
    visible_counts: Counter,
    dora_set: set[str]
) -> dict:
    t = norm_tile(tile_raw)
    t_name = tile_ja(tile_raw)
    target_name = SEAT_NAMES[target_pid]
    gen = tracker.genbutsu[target_pid]
    is_dora = (t in dora_set) or (tile_raw in ("5mr", "5pr", "5sr"))
    dora_note = " ⚠️さらにこの牌自身がドラ（赤ドラ）のため、放銃すると高打点になる危険があります！" if is_dora else ""

    if t in gen:
        return {
            "level": "safe",
            "badge": "現物",
            "risk_score": 0,
            "reason": f"【現物・安全】{target_name}の河にある牌（またはリーチ後に通った牌）のため、100%放銃しません。"
        }

    vis = visible_counts[t]
    if vis >= 4 and t in HONOR_TILES:
        return {
            "level": "safe",
            "badge": "完全安",
            "risk_score": 0,
            "reason": f"【完全安全牌】{t_name}は場と自分の手牌で4枚すべて見えているため、単騎待ちやシャンポン待ちが物理的に存在せず100%安全です。"
        }

    if t in HONOR_TILES:
        if vis == 3:
            return {
                "level": "quasi_safe",
                "badge": "字・安",
                "risk_score": 12,
                "reason": f"【字牌・ラス1】{t_name}は既に3枚見えています。{target_name}の地獄単騎待ち以外に当たらず、現物に次いで極めて安全です。"
            }
        elif vis == 2:
            return {
                "level": "quasi_safe",
                "badge": "字1切",
                "risk_score": 25 + (10 if is_dora else 0),
                "reason": f"【1枚切れ字牌】{t_name}は場に1枚出ています。両面待ちには当たらず、シャンポンか単騎にしか当たらないため比較的切りやすい牌です。{dora_note}"
            }
        else:
            return {
                "level": "caution",
                "badge": "生牌字",
                "risk_score": 45 + (15 if is_dora else 0),
                "reason": f"【生牌（しょっぱい）の字牌】{t_name}はまだ場に1枚も出ていません。数牌の無筋よりは通りやすいですが、シャンポン待ちや単騎待ちには警戒が必要です。{dora_note}"
            }

    num = int(t[0])
    suit = t[1]
    s_ja = SUIT_JA[suit]

    ryammen_list = []
    if num >= 4:
        ryammen_list.append({
            "wait_name": f"{num-3}-{num}{s_ja}",
            "suji_tile": f"{num-3}{suit}",
            "taatsu": (f"{num-2}{suit}", f"{num-1}{suit}")
        })
    if num <= 6:
        ryammen_list.append({
            "wait_name": f"{num}-{num+3}{s_ja}",
            "suji_tile": f"{num+3}{suit}",
            "taatsu": (f"{num+1}{suit}", f"{num+2}{suit}")
        })

    active_waits = []
    cleared_reasons = []
    one_chance_notes = []

    for r in ryammen_list:
        st = r["suji_tile"]
        t1, t2 = r["taatsu"]
        if st in gen:
            cleared_reasons.append(f"{tile_ja(st)}が通っている筋（{r['wait_name']}否定）")
        elif visible_counts[t1] >= 4:
            cleared_reasons.append(f"{tile_ja(t1)}が4枚見えのノーチャンス（壁で{r['wait_name']}否定）")
        elif visible_counts[t2] >= 4:
            cleared_reasons.append(f"{tile_ja(t2)}が4枚見えのノーチャンス（壁で{r['wait_name']}否定）")
        else:
            active_waits.append(r["wait_name"])
            if visible_counts[t1] == 3:
                one_chance_notes.append(f"{tile_ja(t1)}が3枚見えのワンチャンス")
            elif visible_counts[t2] == 3:
                one_chance_notes.append(f"{tile_ja(t2)}が3枚見えのワンチャンス")

    if len(active_waits) == 0:
        reason_joined = "、かつ ".join(cleared_reasons)
        if num in (4, 5, 6):
            return {
                "level": "suji",
                "badge": "中筋/壁",
                "risk_score": 22 + (12 if is_dora else 0),
                "reason": f"【中筋・ノーチャンス】{target_name}に対して {reason_joined} のため、両面待ちには一切当たりません！（カンチャン・シャンポン・単騎のみ注意）{dora_note}"
            }
        else:
            is_wall = any("ノーチャンス" in cr for cr in cleared_reasons) and not any("通っている筋" in cr for cr in cleared_reasons)
            badge_lbl = "壁(NC)" if is_wall else "筋(スジ)"
            return {
                "level": "suji",
                "badge": badge_lbl,
                "risk_score": 28 + (12 if is_dora else 0),
                "reason": f"【{badge_lbl}】{target_name}に対して {reason_joined} のため、両面待ちには当たりません。（ペンチャン・カンチャン・シャンポン待ちには注意）{dora_note}"
            }

    if num in (4, 5, 6) and len(active_waits) == 1:
        w_str = active_waits[0]
        c_str = cleared_reasons[0] if cleared_reasons else ""
        oc_str = f"（ただし{one_chance_notes[0]}）" if one_chance_notes else ""
        return {
            "level": "warning",
            "badge": "片筋",
            "risk_score": 68 + (12 if is_dora else 0),
            "reason": f"【片筋（かたすじ）・警戒】{c_str}ですが、反対側の『{w_str}待ち』が否定されていない片筋です{oc_str}。両面に刺さるリスクが残っています！{dora_note}"
        }

    if one_chance_notes:
        w_str = "・".join(active_waits)
        oc_str = " / ".join(one_chance_notes)
        return {
            "level": "warning",
            "badge": "ワンチャンス",
            "risk_score": 56 + (12 if is_dora else 0),
            "reason": f"【ワンチャンス（無筋）】{target_name}に無筋（{w_str}待ち対象）ですが、{oc_str}のため通常の無筋よりは若干通りやすい牌です。{dora_note}"
        }

    if num in (4, 5, 6):
        w_str = " と ".join(active_waits)
        return {
            "level": "danger",
            "badge": "無筋・高危",
            "risk_score": 92 + (8 if is_dora else 0),
            "reason": f"【無筋の真ん中（4・5・6）・最大危険！】{target_name}に対して『{w_str}』の2種類の両面待ちに刺さる、最も放銃率が高い危険牌です！オリる場合は絶対に切ってはいけません。{dora_note}"
        }
    elif num in (2, 3, 7, 8):
        w_str = active_waits[0]
        return {
            "level": "danger",
            "badge": "無筋・危",
            "risk_score": 78 + (10 if is_dora else 0),
            "reason": f"【無筋（2・3・7・8）・危険】{target_name}に対して『{w_str}』の両面待ちやカンチャン・シャンポンに当たる危険な無筋です。{dora_note}"
        }
    else:
        w_str = active_waits[0]
        return {
            "level": "warning",
            "badge": "端無筋",
            "risk_score": 62 + (10 if is_dora else 0),
            "reason": f"【端牌（1・9）の無筋】{target_name}に対して『{w_str}』の両面待ちが残っていますが、端牌のためペンチャンには当たらず、中張牌の無筋よりは放銃率が低めです。{dora_note}"
        }


def analyze_danger_tiles(tracker) -> dict:
    riichi_opponents = [pid for pid in (1, 2, 3) if tracker.riichi[pid]]
    meld_opponents = [pid for pid in (1, 2, 3) if not tracker.riichi[pid] and len(tracker.melds[pid]) >= 2]

    threat_pids = riichi_opponents if riichi_opponents else meld_opponents
    has_riichi_threat = len(riichi_opponents) > 0
    has_any_threat = len(threat_pids) > 0

    if not has_any_threat:
        return {
            "active": False,
            "has_riichi": False,
            "threat_names": [],
            "summary_text": "現在リーチ者や高副露の相手はいません（自分の手牌のシャンテン数・受け入れ枚数を優先して打牌できます）。",
            "tiles": {}
        }

    visible_counts = get_visible_tile_counts(tracker)
    dora_set = {get_actual_dora(m) for m in tracker.dora_markers}

    my_tiles = list(tracker.hands[tracker.human_pid])
    if tracker.drawn_tiles[tracker.human_pid] is not None:
        my_tiles.append(tracker.drawn_tiles[tracker.human_pid])

    tiles_result = {}
    for tile_raw in set(my_tiles):
        evals_per_target = [
            evaluate_single_tile_danger(tile_raw, tp, tracker, visible_counts, dora_set)
            for tp in threat_pids
        ]
        worst = max(evals_per_target, key=lambda x: x["risk_score"])
        combined_reasons = " ／ ".join(e["reason"] for e in evals_per_target)
        tiles_result[tile_raw] = {
            "level": worst["level"],
            "badge": worst["badge"],
            "risk_score": worst["risk_score"],
            "reason": combined_reasons
        }

    threat_names = [SEAT_NAMES[p] for p in threat_pids]
    mode_label = "リーチ" if has_riichi_threat else "副露テンパイ気配"
    return {
        "active": True,
        "has_riichi": has_riichi_threat,
        "threat_names": threat_names,
        "summary_text": f"⚠️ {', '.join(threat_names)} が{mode_label}中です！手牌にマウスを乗せると放銃リスクと筋・壁の解説が表示されます。",
        "tiles": tiles_result
    }


def ceil100(val: int) -> int:
    return ((val + 99) // 100) * 100


def calc_points_from_fu_han(fu: int, han: int, is_oya: bool, is_tsumo: bool) -> dict:
    """符と飜数から基本点・満貫以上のランク・支払点数・計算式テキストを算出する"""
    if han <= 0:
        return {
            "fu": fu, "han": 0, "base_points": 0, "rank_label": "役なし",
            "total_points": 0, "payment_text": "0点", "formula_text": "役なし"
        }

    is_mangan_up = (han >= 5) or (han == 4 and fu >= 40) or (han == 3 and fu >= 70)
    if han >= 13:
        base_p = 8000
        rank_label = "役満"
        base_formula = f"{han}飜 (役満固定) → 基本点 8,000点"
    elif han >= 11:
        base_p = 6000
        rank_label = "三倍満"
        base_formula = f"{han}飜 (三倍満) → 基本点 6,000点"
    elif han >= 8:
        base_p = 4000
        rank_label = "倍満"
        base_formula = f"{han}飜 (倍満) → 基本点 4,000点"
    elif han >= 6:
        base_p = 3000
        rank_label = "跳満"
        base_formula = f"{han}飜 (跳満) → 基本点 3,000点"
    elif is_mangan_up:
        base_p = 2000
        rank_label = "満貫"
        base_formula = f"{fu}符 {han}飜 (満貫) → 基本点 2,000点"
    else:
        base_p = fu * (2 ** (han + 2))
        rank_label = f"{fu}符 {han}飜"
        base_formula = f"基本点: {fu}符 × 2^({han}飜+2) = {base_p}点"

    if is_oya:
        if is_tsumo:
            each_p = ceil100(base_p * 2)
            total_p = each_p * 3
            pay_txt = f"{each_p:,}点オール (計 {total_p:,}点)"
            form_txt = f"{base_formula} → 親ツモ(基本点×2切上): 子3人から各 {each_p:,}点"
        else:
            total_p = ceil100(base_p * 6)
            pay_txt = f"{total_p:,}点 (親ロン)"
            form_txt = f"{base_formula} → 親ロン(基本点×6切上): {total_p:,}点"
    else:
        if is_tsumo:
            ko_p = ceil100(base_p * 1)
            oya_p = ceil100(base_p * 2)
            total_p = ko_p * 2 + oya_p
            pay_txt = f"{ko_p:,} / {oya_p:,}点 (計 {total_p:,}点)"
            form_txt = f"{base_formula} → 子ツモ: 子2人 {ko_p:,}点(×1切上) / 親 {oya_p:,}点(×2切上)"
        else:
            total_p = ceil100(base_p * 4)
            pay_txt = f"{total_p:,}点 (子ロン)"
            form_txt = f"{base_formula} → 子ロン(基本点×4切上): {total_p:,}点"

    return {
        "fu": fu,
        "han": han,
        "base_points": base_p,
        "rank_label": rank_label,
        "total_points": total_p,
        "payment_text": pay_txt,
        "formula_text": form_txt,
    }


def calculate_fu_detail(
    ok_std: bool,
    decomp: list,
    is_chiitoi: bool,
    is_menzen: bool,
    is_tsumo: bool,
    is_pinfu: bool,
    melds: list[dict],
    win_pai: str,
    bakaze: str,
    jikaze: str
) -> dict:
    """符計算の詳細内訳リスト・切り上げ前後の符を算出する"""
    if is_chiitoi:
        return {
            "fu_raw": 25,
            "fu_total": 25,
            "fu_summary": "25符 (七対子固定)",
            "fu_breakdown": [{"name": "七対子（固定符）", "fu": 25}],
        }

    if not ok_std:
        return {
            "fu_raw": 30,
            "fu_total": 30,
            "fu_summary": "30符 (特殊形)",
            "fu_breakdown": [{"name": "基本符", "fu": 30}],
        }

    if is_pinfu and is_menzen and is_tsumo:
        return {
            "fu_raw": 20,
            "fu_total": 20,
            "fu_summary": "20符 (平和・門前ツモ)",
            "fu_breakdown": [{"name": "副底（平和ツモ特例）", "fu": 20}],
        }

    if is_pinfu and is_menzen and not is_tsumo:
        return {
            "fu_raw": 30,
            "fu_total": 30,
            "fu_summary": "30符 (副底20 + 門前ロン10)",
            "fu_breakdown": [
                {"name": "副底（基本符）", "fu": 20},
                {"name": "門前ロン加符", "fu": 10},
            ],
        }

    breakdown = [{"name": "副底（基本符）", "fu": 20}]
    if is_menzen and not is_tsumo:
        breakdown.append({"name": "門前ロン加符", "fu": 10})
    if is_tsumo:
        breakdown.append({"name": "ツモ符", "fu": 2})

    win_norm = norm_tile(win_pai)
    shuntsu_list = [b[1] for b in decomp if b[0] == "shuntsu"]
    koutsu_list = [b[1] for b in decomp if b[0] == "koutsu"]
    pair_tile = next((b[1][0] for b in decomp if b[0] == "pair"), "")

    # 1. 雀頭（アタマ）符
    if pair_tile in ("P", "F", "C"):
        breakdown.append({"name": f"雀頭（役牌:{WIND_KANJI[pair_tile]}）", "fu": 2})
    elif pair_tile == bakaze and pair_tile == jikaze:
        breakdown.append({"name": f"雀頭（連風牌:ダブ{WIND_KANJI[pair_tile]}）", "fu": 4})
    else:
        if pair_tile == bakaze:
            breakdown.append({"name": f"雀頭（場風:{WIND_KANJI[bakaze]}）", "fu": 2})
        if pair_tile == jikaze:
            breakdown.append({"name": f"雀頭（自風:{WIND_KANJI[jikaze]}）", "fu": 2})

    # 2. 手の内の刻子符
    win_in_shuntsu = any(win_norm in s for s in shuntsu_list)
    ron_koutsu_marked = False
    for k_tuple in koutsu_list:
        kt = k_tuple[0]
        is_yc = (kt in YAOCHU_TILES)
        yc_lbl = "幺九" if is_yc else "中張"
        # シャンポン待ちのロンで完成した刻子は明刻扱い
        if (not is_tsumo) and (not win_in_shuntsu) and (pair_tile != win_norm) and (kt == win_norm) and (not ron_koutsu_marked):
            ron_koutsu_marked = True
            fu_val = 4 if is_yc else 2
            breakdown.append({"name": f"明刻（ロン完成:{tile_ja(kt)}[{yc_lbl}]）", "fu": fu_val})
        else:
            fu_val = 8 if is_yc else 4
            breakdown.append({"name": f"暗刻（手の内:{tile_ja(kt)}[{yc_lbl}]）", "fu": fu_val})

    # 3. 副露（鳴き面子・暗槓）符
    for m in melds:
        mtype = m.get("type", "")
        mtiles = m.get("tiles", [])
        if not mtiles or mtype == "チー":
            continue
        t0 = norm_tile(mtiles[0])
        is_yc = (t0 in YAOCHU_TILES)
        yc_lbl = "幺九" if is_yc else "中張"
        if mtype == "ポン":
            fu_val = 4 if is_yc else 2
            breakdown.append({"name": f"明刻（ポン:{tile_ja(t0)}[{yc_lbl}]）", "fu": fu_val})
        elif mtype in ("カン", "明槓", "加槓"):
            fu_val = 16 if is_yc else 8
            breakdown.append({"name": f"明槓（{mtype}:{tile_ja(t0)}[{yc_lbl}]）", "fu": fu_val})
        elif mtype == "暗槓":
            fu_val = 32 if is_yc else 16
            breakdown.append({"name": f"暗槓（{tile_ja(t0)}[{yc_lbl}]）", "fu": fu_val})

    # 4. 待ち符（単騎・カンチャン・ペンチャンは+2符）
    wait_fu_added = False
    if pair_tile == win_norm and not win_in_shuntsu:
        breakdown.append({"name": f"単騎待ち（{tile_ja(win_norm)}）", "fu": 2})
        wait_fu_added = True
    else:
        for s_tuple in shuntsu_list:
            if win_norm in s_tuple:
                if s_tuple[1] == win_norm:
                    breakdown.append({"name": f"カンチャン待ち（{tile_ja(win_norm)}）", "fu": 2})
                    wait_fu_added = True
                    break
                elif (s_tuple[0][0] == "1" and s_tuple[2] == win_norm) or (s_tuple[2][0] == "9" and s_tuple[0] == win_norm):
                    breakdown.append({"name": f"ペンチャン待ち（{tile_ja(win_norm)}）", "fu": 2})
                    wait_fu_added = True
                    break
    if not wait_fu_added:
        breakdown.append({"name": "両面／シャンポン待ち", "fu": 0})

    fu_raw = sum(x["fu"] for x in breakdown)
    if (not is_menzen) and fu_raw == 20:
        breakdown.append({"name": "喰い平和形（最低30符補正）", "fu": 10})
        fu_raw = 30

    fu_total = ((fu_raw + 9) // 10) * 10
    parts_str = " + ".join(f"{x['name']}{x['fu']}" for x in breakdown if x["fu"] > 0)
    if fu_total != fu_raw:
        fu_summary = f"{parts_str} = 計{fu_raw}符 ➡ 切上 {fu_total}符"
    else:
        fu_summary = f"{parts_str} = {fu_total}符"

    return {
        "fu_raw": fu_raw,
        "fu_total": fu_total,
        "fu_summary": fu_summary,
        "fu_breakdown": breakdown,
    }


def analyze_hora_yakus(tracker, actor: int, target: int, win_pai: str, raw_ev: dict) -> tuple[list[dict], str, dict]:
    is_tsumo = (actor == target)
    melds = tracker.melds[actor]
    is_menzen = all(m.get("type") == "暗槓" for m in melds)

    hand_tiles = [t for t in tracker.hands[actor] if t and t != "?"]
    drawn_t = tracker.drawn_tiles[actor]
    safe_win_pai = win_pai if (win_pai and win_pai != "?") else (drawn_t if (drawn_t and drawn_t != "?") else "")

    if is_tsumo and drawn_t and drawn_t != "?":
        all_closed = hand_tiles + [drawn_t]
    elif safe_win_pai:
        all_closed = hand_tiles + [safe_win_pai]
    else:
        all_closed = list(hand_tiles)

    meld_tiles = []
    for m in melds:
        meld_tiles.extend([t for t in m.get("tiles", []) if t and t != "?"])

    full_tiles_raw = [t for t in (all_closed + meld_tiles) if t and t != "?"]
    full_tiles_norm = [norm_tile(t) for t in full_tiles_raw if norm_tile(t)]
    closed_norm = [norm_tile(t) for t in all_closed if norm_tile(t)]

    jikaze = WIND_CYCLE[(actor - tracker.oya) % 4]
    bakaze = tracker.bakaze
    yakuhai_set = {"P", "F", "C", bakaze, jikaze}

    yakus = []
    if tracker.riichi[actor]:
        yakus.append({"name": "立直 (リーチ)", "han": 1})
    if is_menzen and is_tsumo:
        yakus.append({"name": "門前清自摸和 (ツモ)", "han": 1})
    if full_tiles_norm and all(t not in YAOCHU_TILES for t in full_tiles_norm):
        yakus.append({"name": "断幺九 (タンヤオ)", "han": 1})

    counts_all = Counter(full_tiles_norm)
    for yh in ["P", "F", "C"]:
        if counts_all[yh] >= 3:
            yakus.append({"name": f"役牌 ({WIND_KANJI[yh]})", "han": 1})
    if counts_all[bakaze] >= 3:
        yakus.append({"name": f"場風牌 ({WIND_KANJI[bakaze]})", "han": 1})
    if counts_all[jikaze] >= 3:
        yakus.append({"name": f"自風牌 ({WIND_KANJI[jikaze]})", "han": 1})

    ok_std, decomp = is_standard_agari(closed_norm, len(melds))
    closed_counts = Counter(closed_norm)

    is_chiitoi = is_menzen and len(closed_norm) == 14 and sum(1 for v in closed_counts.values() if v == 2) == 7
    if is_chiitoi:
        yakus.append({"name": "七対子 (チートイツ)", "han": 2})

    is_pinfu = False
    if ok_std:
        shuntsu_list = [b[1] for b in decomp if b[0] == "shuntsu"]
        koutsu_list = [b[1] for b in decomp if b[0] == "koutsu"]
        pair_tile = next((b[1][0] for b in decomp if b[0] == "pair"), "")

        for m in melds:
            m_norm = sorted([norm_tile(x) for x in m["tiles"] if norm_tile(x)], key=lambda x: SORT_ORDER.get(x, 99))
            if not m_norm:
                continue
            if m["type"] == "チー" and len(m_norm) >= 3:
                shuntsu_list.append(tuple(m_norm[:3]))
            else:
                koutsu_list.append((m_norm[0], m_norm[0], m_norm[0]))

        win_n = norm_tile(safe_win_pai)
        has_ryammen_wait = False
        for s_tuple in [b[1] for b in decomp if b[0] == "shuntsu"]:
            if win_n in s_tuple:
                if s_tuple[1] != win_n and not (s_tuple[0][0] == "1" and s_tuple[2] == win_n) and not (s_tuple[2][0] == "9" and s_tuple[0] == win_n):
                    has_ryammen_wait = True
                    break

        if is_menzen and len(shuntsu_list) == 4 and pair_tile not in yakuhai_set and has_ryammen_wait:
            is_pinfu = True
            yakus.append({"name": "平和 (ピンフ)", "han": 1})

        if is_menzen:
            shuntsu_counts = Counter(shuntsu_list)
            pairs_of_shuntsu = sum(v // 2 for v in shuntsu_counts.values())
            if pairs_of_shuntsu == 2:
                yakus.append({"name": "二盃口 (リャンペーコー)", "han": 3})
            elif pairs_of_shuntsu == 1:
                yakus.append({"name": "一盃口 (イーペーコー)", "han": 1})

        if len(koutsu_list) == 4:
            yakus.append({"name": "対々和 (トイトイ)", "han": 2})

        shuntsu_nums = {}
        for s_tuple in shuntsu_list:
            first_t = s_tuple[0]
            num_part, suit_part = first_t[0], first_t[1]
            shuntsu_nums.setdefault(num_part, set()).add(suit_part)
        if any(len(suits) == 3 for suits in shuntsu_nums.values()):
            yakus.append({"name": "三色同順", "han": 2 if is_menzen else 1})

        for suit in ("m", "p", "s"):
            s_firsts = {s_tuple[0] for s_tuple in shuntsu_list if s_tuple[0][1] == suit}
            if {f"1{suit}", f"4{suit}", f"7{suit}"}.issubset(s_firsts):
                yakus.append({"name": "一気通貫", "han": 2 if is_menzen else 1})

        all_blocks = shuntsu_list + koutsu_list + [(pair_tile, pair_tile)]
        if all(any(t in YAOCHU_TILES for t in blk) for blk in all_blocks):
            if all(t in YAOCHU_TILES for t in full_tiles_norm):
                yakus.append({"name": "混老頭 (ホンロートー)", "han": 2})
            elif any(t in HONOR_TILES for t in full_tiles_norm):
                yakus.append({"name": "混全帯幺九 (チャンタ)", "han": 2 if is_menzen else 1})
            else:
                yakus.append({"name": "純全帯幺九 (ジュンチャン)", "han": 3 if is_menzen else 2})

    suits_present = {t[1] for t in full_tiles_norm if len(t) == 2}
    has_honors = any(t in HONOR_TILES for t in full_tiles_norm)
    if len(suits_present) == 1:
        if has_honors:
            yakus.append({"name": "混一色 (ホンイツ)", "han": 3 if is_menzen else 2})
        else:
            yakus.append({"name": "清一色 (チンイツ)", "han": 6 if is_menzen else 5})

    if not yakus:
        yakus.append({"name": "アガリ役成立 (海底/嶺上/一発等)", "han": 1})

    dora_tiles = [get_actual_dora(m) for m in tracker.dora_markers]
    dora_count = sum(full_tiles_norm.count(d) for d in dora_tiles)
    if dora_count > 0:
        yakus.append({"name": f"ドラ ({dora_count}枚)", "han": dora_count})

    red_count = sum(1 for t in full_tiles_raw if t in ("5mr", "5pr", "5sr"))
    if red_count > 0:
        yakus.append({"name": f"赤ドラ ({red_count}枚)", "han": red_count})

    uradora_markers = raw_ev.get("uradora_markers") or []
    if tracker.riichi[actor] and uradora_markers:
        ura_tiles = [get_actual_dora(m) for m in uradora_markers]
        ura_count = sum(full_tiles_norm.count(u) for u in ura_tiles)
        if ura_count > 0:
            yakus.append({"name": f"裏ドラ ({ura_count}枚)", "han": ura_count})

    fu_info = calculate_fu_detail(
        ok_std=ok_std,
        decomp=decomp,
        is_chiitoi=is_chiitoi,
        is_menzen=is_menzen,
        is_tsumo=is_tsumo,
        is_pinfu=is_pinfu,
        melds=melds,
        win_pai=safe_win_pai,
        bakaze=bakaze,
        jikaze=jikaze,
    )
    fu_total = fu_info["fu_total"]
    total_han = sum(y["han"] for y in yakus)
    is_oya = (actor == tracker.oya)

    deltas = raw_ev.get("deltas", [0, 0, 0, 0])
    won_points = deltas[actor] if actor < len(deltas) else 0
    honba_bonus = tracker.honba * 300
    kyotaku_bonus = tracker.kyotaku * 1000
    pure_hand_points = max(0, won_points - honba_bonus - kyotaku_bonus)

    # 一発・裏ドラ・海底・嶺上等で実打点が計算値より高い場合は飜数を自動補正して完全一致させる
    pts_info = calc_points_from_fu_han(fu_total, total_han, is_oya, is_tsumo)
    if pure_hand_points > pts_info["total_points"] and total_han < 13:
        for test_h in range(total_han + 1, 14):
            test_pts = calc_points_from_fu_han(fu_total, test_h, is_oya, is_tsumo)
            if test_pts["total_points"] >= pure_hand_points:
                diff_h = test_h - total_han
                yakus.append({"name": "一発・裏ドラ・特殊役等", "han": diff_h})
                total_han = test_h
                pts_info = test_pts
                break

    rank_label = f"{fu_total}符 {total_han}飜"
    if pts_info["rank_label"] in ("満貫", "跳満", "倍満", "三倍満", "役満"):
        rank_label = f"{fu_total}符 {total_han}飜 ({pts_info['rank_label']})"

    extra_bonus_parts = []
    if honba_bonus > 0:
        extra_bonus_parts.append(f"{tracker.honba}本場(+{honba_bonus:,}点)")
    if kyotaku_bonus > 0:
        extra_bonus_parts.append(f"供託リーチ棒(+{kyotaku_bonus:,}点)")
    extra_bonus_str = " ＋ ".join(extra_bonus_parts)

    score_calc_detail = {
        "fu_raw": fu_info["fu_raw"],
        "fu_total": fu_total,
        "fu_summary": fu_info["fu_summary"],
        "fu_breakdown": fu_info["fu_breakdown"],
        "total_han": total_han,
        "base_points": pts_info["base_points"],
        "pure_hand_points": pts_info["total_points"],
        "payment_text": pts_info["payment_text"],
        "formula_text": pts_info["formula_text"],
        "extra_bonus_str": extra_bonus_str,
    }

    return yakus, rank_label, score_calc_detail


def analyze_realtime_score_estimate(tracker, tenpai_info: dict, potential_yakus: list[dict]) -> dict:
    """
    プレイ中にリアルタイムで自家の点数計算（符計算・飜計算の予想・候補）を算出する
    - テンパイ時（またはテンパイ打牌がある時）：各待ち牌での具体的な符・飜・役・ロン/ツモ打点を算出
    - 1シャンテン以上の時：現在の面子/雀頭候補から想定符と有力役での打点目安を算出
    """
    pid = tracker.human_pid
    is_oya = (pid == tracker.oya)
    oya_label = "親" if is_oya else "子"
    melds = tracker.melds[pid]
    is_menzen = all(m.get("type") == "暗槓" for m in melds)
    jikaze = WIND_CYCLE[(pid - tracker.oya) % 4]
    bakaze = tracker.bakaze

    cur_sh = tenpai_info.get("current_shanten", 1)
    is_13_tenpai = tenpai_info.get("is_13_tenpai", False)
    tenpai_discards = tenpai_info.get("tenpai_discards", [])

    # ---------------------------------------------------------
    # A. テンパイ中（または打牌でテンパイ可能）の厳密な待ち別点数計算
    # ---------------------------------------------------------
    if is_13_tenpai or len(tenpai_discards) > 0:
        if is_13_tenpai:
            base_hand_13 = list(tracker.hands[pid])
            waits_list = tenpai_info.get("base_waits", [])
            header_mode = f"🔥 現在聴牌（{oya_label}）— アガリ牌別の符・飜・点数計算"
        else:
            best_td = tenpai_discards[0]
            full_raw = list(tracker.hands[pid]) + ([tracker.drawn_tiles[pid]] if tracker.drawn_tiles[pid] else [])
            if best_td["discard"] in full_raw:
                full_raw.remove(best_td["discard"])
            base_hand_13 = full_raw[:13 - len(melds) * 3]
            waits_list = best_td.get("waits", [])
            header_mode = f"⚡ 打 [{tile_ja(best_td['discard'])}] で聴牌（{oya_label}）— 待ち牌別の点数計算"

        wait_calcs = []
        for w in waits_list[:6]:
            w_tile = w["tile"]
            # ダミーの tracker 状態を作ってロン時・ツモ時の役と符を計算
            orig_hand = list(tracker.hands[pid])
            orig_drawn = tracker.drawn_tiles[pid]
            try:
                tracker.hands[pid] = list(base_hand_13)
                tracker.drawn_tiles[pid] = None
                yakus_ron, _, calc_ron = analyze_hora_yakus(tracker, pid, (pid + 1) % 4, w_tile, {})
                tracker.drawn_tiles[pid] = w_tile
                yakus_tsumo, _, calc_tsumo = analyze_hora_yakus(tracker, pid, pid, w_tile, {})
            finally:
                tracker.hands[pid] = orig_hand
                tracker.drawn_tiles[pid] = orig_drawn

            # 「アガリ役成立(海底等)」のフォールバックしか無い場合は役なし判定
            has_real_yaku_ron = not (len(yakus_ron) == 1 and "海底" in yakus_ron[0]["name"])
            has_real_yaku_tsumo = not (len(yakus_tsumo) == 1 and "海底" in yakus_tsumo[0]["name"])

            ron_han = calc_ron["total_han"] if has_real_yaku_ron else 0
            tsumo_han = calc_tsumo["total_han"] if has_real_yaku_tsumo else 0

            ron_pts = calc_points_from_fu_han(calc_ron["fu_total"], ron_han, is_oya, False)
            tsumo_pts = calc_points_from_fu_han(calc_tsumo["fu_total"], tsumo_han, is_oya, True)

            # 門前で未リーチなら「リーチ時（+1飜）」の点数も併記
            riichi_note = ""
            if is_menzen and not tracker.riichi[pid]:
                r_ron = calc_points_from_fu_han(calc_ron["fu_total"], (ron_han + 1) if has_real_yaku_ron else 1 + sum(y["han"] for y in yakus_ron if "ドラ" in y["name"]), is_oya, False)
                r_tsumo = calc_points_from_fu_han(calc_tsumo["fu_total"], tsumo_han + 1, is_oya, True)
                riichi_note = f"リーチ時: ロン {r_ron['total_points']:,}点 / ツモ {r_tsumo['payment_text']}"

            yaku_names_str = "・".join(f"{y['name'].split(' ')[0]}({y['han']})" for y in yakus_tsumo if "海底" not in y["name"])
            wait_calcs.append({
                "wait_tile": w_tile,
                "wait_tile_ja": tile_ja(w_tile),
                "remaining": w["remaining"],
                "fu_ron": calc_ron["fu_total"],
                "han_ron": ron_han,
                "ron_text": f"{calc_ron['fu_total']}符{ron_han}飜 {ron_pts['total_points']:,}点" if ron_han > 0 else "役なし(ロン不可)",
                "fu_tsumo": calc_tsumo["fu_total"],
                "han_tsumo": tsumo_han,
                "tsumo_text": f"{calc_tsumo['fu_total']}符{tsumo_han}飜 {tsumo_pts['payment_text']}" if tsumo_han > 0 else "役なし",
                "fu_summary": calc_ron["fu_summary"],
                "yaku_summary": yaku_names_str or "役なし（リーチで1飜）",
                "riichi_note": riichi_note,
            })

        return {
            "mode": "tenpai",
            "title": header_mode,
            "is_oya": is_oya,
            "is_menzen": is_menzen,
            "wait_calcs": wait_calcs,
            "estimates": [],
        }

    # ---------------------------------------------------------
    # B. 1シャンテン以上のリアルタイム符・飜・打点予想コース
    # ---------------------------------------------------------
    hand_raw = list(tracker.hands[pid]) + ([tracker.drawn_tiles[pid]] if tracker.drawn_tiles[pid] else [])
    hand_norm = [norm_tile(t) for t in hand_raw]
    counts_hand = Counter(hand_norm)

    # 現在の手牌にある確定・準確定の符要素を集計
    fu_notes = ["副底20符"]
    est_base_fu = 20
    for t, c in counts_hand.items():
        if c >= 3:
            f_v = 8 if t in YAOCHU_TILES else 4
            est_base_fu += f_v
            fu_notes.append(f"暗刻({tile_ja(t)})+{f_v}符")
        elif c == 2 and t in {"P", "F", "C", bakaze, jikaze}:
            est_base_fu += 2
            fu_notes.append(f"役牌対子({tile_ja(t)})+2符")

    for m in melds:
        mtype = m.get("type", "")
        mtiles = m.get("tiles", [])
        if not mtiles or mtype == "チー":
            continue
        t0 = norm_tile(mtiles[0])
        is_yc = (t0 in YAOCHU_TILES)
        if mtype == "ポン":
            f_v = 4 if is_yc else 2
            est_base_fu += f_v
            fu_notes.append(f"ポン({tile_ja(t0)})+{f_v}符")
        elif mtype in ("カン", "明槓", "加槓"):
            f_v = 16 if is_yc else 8
            est_base_fu += f_v
            fu_notes.append(f"明槓({tile_ja(t0)})+{f_v}符")
        elif mtype == "暗槓":
            f_v = 32 if is_yc else 16
            est_base_fu += f_v
            fu_notes.append(f"暗槓({tile_ja(t0)})+{f_v}符")

    est_ron_fu = ((est_base_fu + (10 if is_menzen else 0) + 9) // 10) * 10
    est_ron_fu = max(30, est_ron_fu)
    est_tsumo_fu = ((est_base_fu + 2 + 9) // 10) * 10
    est_tsumo_fu = max(30 if not is_menzen else 20, est_tsumo_fu)

    dora_list = [get_actual_dora(m) for m in tracker.dora_markers]
    all_raw = hand_raw + [t for m in melds for t in m.get("tiles", [])]
    dora_cnt = sum(1 for t in all_raw if norm_tile(t) in dora_list) + sum(1 for t in all_raw if t in ("5mr", "5pr", "5sr"))

    estimates = []
    # コース1: 最有力の手役候補（potential_yakus上位）で上がった場合
    main_yakus = [y for y in potential_yakus if not y["name"].startswith("ドラ") and not y["name"].startswith("立直")]
    if main_yakus:
        top_y = main_yakus[0]
        import re
        m_num = re.search(r"(\d+)", top_y["han"])
        y_han = int(m_num.group(1)) if m_num else 1
        tot_h = y_han + dora_cnt
        ron_p = calc_points_from_fu_han(est_ron_fu, tot_h, is_oya, False)
        tsumo_p = calc_points_from_fu_han(est_tsumo_fu, tot_h + (1 if is_menzen else 0), is_oya, True)
        dora_str = f"＋ドラ{dora_cnt}" if dora_cnt > 0 else ""
        estimates.append({
            "route_name": f"🎯 本線: {top_y['name']}{dora_str}",
            "fu_han_text": f"想定 {est_ron_fu}符 {tot_h}飜",
            "ron_text": f"ロン: {ron_p['total_points']:,}点 ({ron_p['rank_label']})",
            "tsumo_text": f"ツモ: {tsumo_p['payment_text']}",
            "formula_hint": ron_p["formula_text"],
        })

    # コース2: 門前リーチ（＋最有力役 or ドラ）コース
    if is_menzen:
        r_han = 1 + dora_cnt + (1 if any("タンヤオ" in y["name"] or "平和" in y["name"] or "役牌" in y["name"] for y in main_yakus[:2]) else 0)
        r_extra_name = "＋役1飜" if r_han > 1 + dora_cnt else ""
        dora_str = f"＋ドラ{dora_cnt}" if dora_cnt > 0 else ""
        ron_p = calc_points_from_fu_han(est_ron_fu, r_han, is_oya, False)
        tsumo_p = calc_points_from_fu_han(est_tsumo_fu, r_han + 1, is_oya, True)
        estimates.append({
            "route_name": f"🔥 門前リーチ{r_extra_name}{dora_str}",
            "fu_han_text": f"ロン{est_ron_fu}符{r_han}飜 / ツモ{est_tsumo_fu}符{r_han+1}飜",
            "ron_text": f"ロン: {ron_p['total_points']:,}点 ({ron_p['rank_label']})",
            "tsumo_text": f"ツモ(+1飜): {tsumo_p['payment_text']}",
            "formula_hint": tsumo_p["formula_text"],
        })

# --- web_app.py 1390行目〜1397行目付近 ---
    return {
        "mode": "shanten",
        "title": f"🧮 リアルタイム点数・符・飜予想（{oya_label}・現在{shanten_ja(cur_sh)}）",
        "is_oya": is_oya,
        "is_menzen": is_menzen,
        "fu_progress_text": f"現在の符構成: {' + '.join(fu_notes)} （ロン想定:{est_ron_fu}符 / ツモ想定:{est_tsumo_fu}符）",  # ← "fu_progress_text" のスペースを詰める
        "wait_calcs": [],
        "estimates": estimates,
    }

def analyze_potential_yakus(tracker) -> list[dict]:
    pid = tracker.human_pid
    hand_raw = list(tracker.hands[pid])
    if tracker.drawn_tiles[pid] is not None:
        hand_raw.append(tracker.drawn_tiles[pid])

    melds = tracker.melds[pid]
    is_menzen = all(m.get("type") == "暗槓" for m in melds)

    meld_tiles_raw = []
    for m in melds:
        meld_tiles_raw.extend(m.get("tiles", []))

    all_raw = hand_raw + meld_tiles_raw
    all_norm = [norm_tile(t) for t in all_raw]
    hand_norm = [norm_tile(t) for t in hand_raw]
    counts_all = Counter(all_norm)
    counts_hand = Counter(hand_norm)

    jikaze = WIND_CYCLE[(pid - tracker.oya) % 4]
    bakaze = tracker.bakaze

    suggestions = []

    dora_list = [get_actual_dora(m) for m in tracker.dora_markers]
    owned_doras = [t for t in all_raw if norm_tile(t) in dora_list]
    owned_reds = [t for t in all_raw if t in ("5mr", "5pr", "5sr")]
    total_dora_num = len(owned_doras) + len(owned_reds)
    if total_dora_num > 0:
        detail_parts = []
        if owned_doras:
            detail_parts.append(f"ドラ:{','.join(owned_doras)}")
        if owned_reds:
            detail_parts.append(f"赤:{','.join(owned_reds)}")
        suggestions.append({
            "name": f"ドラ計 {total_dora_num}枚",
            "han": f"+{total_dora_num}飜",
            "status": "保有中",
            "progress": 100,
            "desc": f"手牌に {' / '.join(detail_parts)} があります"
        })

    yakuhai_candidates = [
        ("P", "役牌: 白"),
        ("F", "役牌: 發"),
        ("C", "役牌: 中"),
        (bakaze, f"場風: {WIND_KANJI.get(bakaze, bakaze)}"),
        (jikaze, f"自風: {WIND_KANJI.get(jikaze, jikaze)}"),
    ]
    seen_yh = set()
    for code, label in yakuhai_candidates:
        if code in seen_yh:
            continue
        seen_yh.add(code)
        c = counts_all[code]
        if c >= 3:
            suggestions.append({
                "name": label,
                "han": "1飜",
                "status": "確定",
                "progress": 100,
                "desc": f"{WIND_KANJI.get(code, code)}が3枚揃っています（鳴いても1飜確定）"
            })
        elif c == 2:
            suggestions.append({
                "name": label,
                "han": "1飜",
                "status": "有力",
                "progress": 80,
                "desc": f"{WIND_KANJI.get(code, code)}が対子です。ポンまたは暗刻で1飜確定"
            })

    meld_has_yaochu = any(norm_tile(t) in YAOCHU_TILES for t in meld_tiles_raw)
    if not meld_has_yaochu:
        yaochu_in_hand = [t for t in hand_norm if t in YAOCHU_TILES]
        yc_count = len(yaochu_in_hand)
        if yc_count == 0 and len(all_norm) >= 13:
            suggestions.append({
                "name": "断幺九 (タンヤオ)",
                "han": "1飜",
                "status": "確定圏内",
                "progress": 100,
                "desc": "すべて中張牌(2〜8)で構成されています"
            })
        elif 1 <= yc_count <= 3:
            pct = max(40, int((14 - yc_count * 3) / 14 * 100))
            suggestions.append({
                "name": "断幺九 (タンヤオ)",
                "han": "1飜",
                "status": "有力" if yc_count <= 2 else "候補",
                "progress": pct,
                "desc": f"1・9・字牌（{','.join(yaochu_in_hand)}）をあと{yc_count}枚処理で成立"
            })

    if is_menzen:
        suggestions.append({
            "name": "立直 (リーチ)",
            "han": "1飜",
            "status": "門前",
            "progress": 70,
            "desc": "鳴かずに聴牌すればリーチ可能"
        })
        yh_set = {"P", "F", "C", bakaze, jikaze}
        yh_pairs = sum(1 for y in yh_set if counts_hand[y] >= 2)
        number_tiles_cnt = sum(1 for t in hand_norm if t not in HONOR_TILES)
        if yh_pairs == 0 and number_tiles_cnt >= 11:
            suggestions.append({
                "name": "平和 (ピンフ)",
                "han": "1飜 (門前)",
                "status": "有力",
                "progress": 75,
                "desc": "4順子＋役牌以外の雀頭＋両面待ちで成立"
            })

    pairs_count = sum(1 for v in counts_hand.values() if v >= 2)
    triplets_count = sum(1 for v in counts_all.values() if v >= 3)
    if is_menzen and pairs_count >= 4:
        suggestions.append({
            "name": "七対子 (チートイツ)",
            "han": "2飜 (門前)",
            "status": "有力" if pairs_count >= 5 else "候補",
            "progress": min(95, int(pairs_count / 6 * 100)),
            "desc": f"現在 {pairs_count}対子（6対子で聴牌）"
        })
    if pairs_count + triplets_count >= 5 and triplets_count >= 1:
        suggestions.append({
            "name": "対々和 (トイトイ)",
            "han": "2飜",
            "status": "候補",
            "progress": 65,
            "desc": "ポンを仕掛けて4刻子＋1雀頭で成立"
        })

    suit_names = {"m": "萬子", "p": "筒子", "s": "索子"}
    honor_cnt = sum(1 for t in all_norm if t in HONOR_TILES)
    for s_code, s_label in suit_names.items():
        other_meld = any(
            len(norm_tile(t)) == 2 and norm_tile(t)[1] != s_code
            for t in meld_tiles_raw
        )
        if other_meld:
            continue
        suit_cnt = sum(1 for t in all_norm if len(t) == 2 and t[1] == s_code)
        total_honitsu = suit_cnt + honor_cnt
        if suit_cnt >= 10:
            suggestions.append({
                "name": f"清一色 ({s_label})",
                "han": "6飜 (鳴き5飜)",
                "status": "有力",
                "progress": min(95, int(suit_cnt / 13 * 100)),
                "desc": f"{s_label}だけで {suit_cnt}枚保有"
            })
        elif total_honitsu >= 9 and suit_cnt >= 6:
            suggestions.append({
                "name": f"混一色 ({s_label}ホンイツ)",
                "han": "3飜 (鳴き2飜)",
                "status": "有力" if total_honitsu >= 11 else "候補",
                "progress": min(95, int(total_honitsu / 13 * 100)),
                "desc": f"{s_label}({suit_cnt}枚)＋字牌({honor_cnt}枚)で計{total_honitsu}枚"
            })

    best_sanshoku = (0, "")
    for start_n in range(1, 8):
        seq_nums = [start_n, start_n + 1, start_n + 2]
        suits_with_part = 0
        total_matched = 0
        for s_code in ("m", "p", "s"):
            matched = sum(1 for n in seq_nums if counts_all[f"{n}{s_code}"] > 0)
            if matched > 0:
                suits_with_part += 1
                total_matched += matched
        if suits_with_part == 3 and total_matched >= 6:
            if total_matched > best_sanshoku[0]:
                best_sanshoku = (total_matched, f"{start_n}{start_n+1}{start_n+2}")

    if best_sanshoku[0] >= 6:
        cnt, seq_label = best_sanshoku
        suggestions.append({
            "name": f"三色同順 ({seq_label})",
            "han": "2飜 (鳴き1飜)",
            "status": "有力" if cnt >= 7 else "候補",
            "progress": int(cnt / 9 * 100),
            "desc": f"3スートに「{seq_label}」のパーツあり（{cnt}/9枚）"
        })

    for s_code, s_label in suit_names.items():
        distinct_nums = sum(1 for n in range(1, 10) if counts_all[f"{n}{s_code}"] > 0)
        if distinct_nums >= 6:
            suggestions.append({
                "name": f"一気通貫 ({s_label})",
                "han": "2飜 (鳴き1飜)",
                "status": "有力" if distinct_nums >= 7 else "候補",
                "progress": int(distinct_nums / 9 * 100),
                "desc": f"{s_label}の1〜9のうち {distinct_nums}種類を保有"
            })

    yc_types = sum(1 for t in YAOCHU_TILES if counts_all[t] > 0)
    if is_menzen and yc_types >= 8:
        suggestions.append({
            "name": "国士無双 (役満)",
            "han": "役満 (門前)",
            "status": "有力" if yc_types >= 10 else "候補",
            "progress": min(96, int(yc_types / 13 * 100)),
            "desc": f"1・9・字牌が現在 {yc_types}/13種類 揃っています！"
        })

    suggestions.sort(key=lambda x: x["progress"], reverse=True)
    return suggestions[:5]


class TableTracker:
    def __init__(self, human_pid: int = 0):
        self.human_pid = human_pid
        self.last_kyoku_summary = ""
        self.pending_round_result = None
        self.step_animations = []
        self.hands = {i: [] for i in range(4)}
        self.drawn_tiles = {i: None for i in range(4)}
        self.player_meld_count = {i: 0 for i in range(4)}
        self.player_last_discard = {i: None for i in range(4)}
        self.round_events_by_pid = {i: [] for i in range(4)}
        self.reset_kyoku()

    def reset_kyoku(self):
        self.bakaze = "E"
        self.kyoku = 1
        self.honba = 0
        self.kyotaku = 0
        self.oya = 0
        self.scores = [25000, 25000, 25000, 25000]
        self.dora_markers = []
        self.wall_remaining = 70
        self.discards = {i: [] for i in range(4)}
        self.melds = {i: [] for i in range(4)}
        self.call_history = []
        self.round_review_list = []  # ← 追加：1局ごとの振り返りリスト（次局開始時に自動クリア）
        self.riichi = {i: False for i in range(4)}
        self.pending_riichi_decl = {i: False for i in range(4)}
        self.genbutsu = {i: set() for i in range(4)}
        self.last_discard = None

    def _remove_from_player_hand(self, pid: int, tile: str):
        hand = self.hands[pid]
        if tile in hand:
            hand.remove(tile)
            return
        for t in hand:
            if norm_tile(t) == norm_tile(tile):
                hand.remove(t)
                return

    def update_from_player_obs(self, obs_pid: int, event_str: str):
        ev = json.loads(event_str)
        etype = ev.get("type")

        # 各プレイヤーの現在局イベント履歴を記録（start_kyoku でそのプレイヤーの履歴をリセット）
        if etype == "start_kyoku":
            self.round_events_by_pid[obs_pid] = [event_str]
        else:
            self.round_events_by_pid[obs_pid].append(event_str)

        # ====================================================================
        # Part 1: 各プレイヤー(obs_pid)固有の手牌・ツモ牌追跡（完全独立・他家に干渉しない）
        # ====================================================================
        if etype == "start_kyoku":
            tehais = ev.get("tehais", [[]] * 4)
            if obs_pid < len(tehais) and "?" not in tehais[obs_pid]:
                self.hands[obs_pid] = list(tehais[obs_pid])
                self.drawn_tiles[obs_pid] = None
                self.player_meld_count[obs_pid] = 0
                self.player_last_discard[obs_pid] = None

        elif etype == "tsumo":
            actor = ev.get("actor")
            pai = ev.get("pai")
            if actor == obs_pid and pai and pai != "?":
                if self.drawn_tiles[obs_pid] is not None:
                    self.hands[obs_pid].append(self.drawn_tiles[obs_pid])
                self.drawn_tiles[obs_pid] = pai

        elif etype == "dahai":
            actor = ev.get("actor")
            pai = ev.get("pai")
            tsumogiri = ev.get("tsumogiri", False)
            norm_p = norm_tile(pai)
            self.player_last_discard[obs_pid] = {"actor": actor, "pai": pai}

            if actor == obs_pid:
                if tsumogiri and self.drawn_tiles[obs_pid] is not None and norm_tile(self.drawn_tiles[obs_pid]) == norm_p:
                    self.drawn_tiles[obs_pid] = None
                else:
                    if self.drawn_tiles[obs_pid] is not None:
                        self.hands[obs_pid].append(self.drawn_tiles[obs_pid])
                        self.drawn_tiles[obs_pid] = None
                    self._remove_from_player_hand(obs_pid, pai)

        elif etype in ("pon", "chi", "daiminkan", "ankan", "kakan"):
            actor = ev.get("actor")
            pai = ev.get("pai", "")
            consumed = ev.get("consumed", [])
            if actor == obs_pid:
                if self.drawn_tiles[obs_pid] is not None:
                    self.hands[obs_pid].append(self.drawn_tiles[obs_pid])
                    self.drawn_tiles[obs_pid] = None
                if etype == "kakan":
                    self._remove_from_player_hand(obs_pid, pai)
                else:
                    for c in consumed:
                        self._remove_from_player_hand(obs_pid, c)
                    self.player_meld_count[obs_pid] += 1

        # ====================================================================
        # Part 2: 卓全体の共通盤面更新は human_pid (0) のストリームでのみ実行
        # （他家の遅延ストリームによる前局の打牌・和了の混入を100%遮断する）
        # ====================================================================
        if obs_pid != self.human_pid:
            return

        if etype == "start_kyoku":
            self.reset_kyoku()
            self.bakaze = ev.get("bakaze", "E")
            self.kyoku = ev.get("kyoku", 1)
            self.honba = ev.get("honba", 0)
            self.kyotaku = ev.get("kyotaku", 0)
            self.oya = ev.get("oya", 0)
            self.scores = list(ev.get("scores", [25000] * 4))
            self.dora_markers = [ev.get("dora_marker", "E")]
            self.step_animations.clear()

        elif etype == "dora":
            self.dora_markers.append(ev.get("dora_marker", "E"))

        elif etype == "tsumo":
            self.wall_remaining = max(0, self.wall_remaining - 1)

        elif etype == "dahai":
            actor = ev.get("actor")
            pai = ev.get("pai")
            tsumogiri = ev.get("tsumogiri", False)
            norm_p = norm_tile(pai)

            self.last_discard = {"actor": actor, "pai": pai}
            is_riichi_tile = self.pending_riichi_decl.get(actor, False)
            self.pending_riichi_decl[actor] = False

            self.discards[actor].append({
                "pai": pai,
                "tsumogiri": tsumogiri,
                "is_riichi": is_riichi_tile,
                "called_by": None,
                "called_by_name": None,
                "call_type": None,
            })
            self.genbutsu[actor].add(norm_p)
            for r_pid in range(4):
                if self.riichi[r_pid]:
                    self.genbutsu[r_pid].add(norm_p)

            self.step_animations.append({
                "kind": "dahai",
                "actor": actor,
                "actor_name": SEAT_NAMES[actor],
                "pai": pai,
                "tsumogiri": tsumogiri,
                "is_riichi": is_riichi_tile
            })

        elif etype == "reach":
            actor = ev.get("actor")
            self.riichi[actor] = True
            self.pending_riichi_decl[actor] = True
            self.step_animations.append({
                "kind": "call",
                "actor": actor,
                "actor_name": SEAT_NAMES[actor],
                "call_text": "リーチ！"
            })

        elif etype == "reach_accepted":
            actor = ev.get("actor")
            self.riichi[actor] = True
            if "scores" in ev:
                self.scores = list(ev["scores"])
            self.kyotaku += 1

        elif etype in ("pon", "chi", "daiminkan", "ankan", "kakan"):
            actor = ev.get("actor")
            pai = ev.get("pai", "")
            consumed = ev.get("consumed", [])
            target = ev.get("target")
            if target is None:
                if etype in ("pon", "chi", "daiminkan") and self.last_discard:
                    target = self.last_discard["actor"]
                else:
                    target = actor

            from_short = SEAT_SHORT_NAMES[target] if (target is not None and 0 <= target < 4) else ""

            if etype in ("pon", "chi", "daiminkan"):
                label = {"pon": "ポン", "chi": "チー", "daiminkan": "明槓"}[etype]
                self.melds[actor].append({
                    "type": label,
                    "tiles": [pai] + list(consumed),
                    "called_tile": pai,
                    "from_pid": target,
                    "from_name": from_short,
                })
                if target is not None and target in self.discards and len(self.discards[target]) > 0:
                    self.discards[target][-1]["called_by"] = actor
                    self.discards[target][-1]["called_by_name"] = SEAT_SHORT_NAMES[actor]
                    self.discards[target][-1]["call_type"] = label

                call_txt = f"◀ {from_short}から {label}！ [{tile_ja(pai)}]"
                self.call_history.append({
                    "actor": actor,
                    "actor_name": SEAT_SHORT_NAMES[actor],
                    "target": target,
                    "target_name": from_short,
                    "type": label,
                    "pai": pai,
                    "pai_ja": tile_ja(pai),
                })
            elif etype == "ankan":
                t0 = consumed[0] if consumed else ""
                self.melds[actor].append({
                    "type": "暗槓",
                    "tiles": list(consumed),
                    "called_tile": None,
                    "from_pid": actor,
                    "from_name": "暗槓",
                })
                call_txt = f"暗槓！ [{tile_ja(t0)}]"
            else:
                updated = False
                for m in self.melds[actor]:
                    if m["type"] == "ポン" and m["tiles"] and norm_tile(m["tiles"][0]) == norm_tile(pai):
                        m["type"] = "加槓"
                        m["tiles"] = list(m["tiles"]) + [pai]
                        updated = True
                        break
                if not updated:
                    self.melds[actor].append({
                        "type": "加槓",
                        "tiles": [pai] + list(consumed),
                        "called_tile": pai,
                        "from_pid": actor,
                        "from_name": "加槓",
                    })
                call_txt = f"加槓！ [{tile_ja(pai)}]"

            self.step_animations.append({
                "kind": "call",
                "actor": actor,
                "actor_name": SEAT_NAMES[actor],
                "target": target,
                "call_text": call_txt
            })

        elif etype == "hora":
            actor = ev.get("actor", 0)
            target = ev.get("target", 0)
            pai = (
                ev.get("pai")
                or (self.drawn_tiles[actor] if actor == target else None)
                or (self.last_discard["pai"] if self.last_discard else None)
                or (self.hands[actor][-1] if self.hands[actor] else "1m")
            )
            deltas = ev.get("deltas", [0] * 4)
            new_scores = ev.get("scores", [s + d for s, d in zip(self.scores, deltas)])

            if actor != target and target in self.discards and len(self.discards[target]) > 0:
                self.discards[target][-1]["called_by"] = actor
                self.discards[target][-1]["called_by_name"] = SEAT_SHORT_NAMES[actor]
                self.discards[target][-1]["call_type"] = "ロン"

            yakus, rank_label, score_calc = analyze_hora_yakus(self, actor, target, pai, ev)
            way_str = "ツモ和了" if actor == target else f"◀ {SEAT_NAMES[target]} から ロン和了"
            bakaze_ja = WIND_KANJI.get(self.bakaze, self.bakaze)
            round_title = f"{bakaze_ja}{self.kyoku}局 {self.honba}本場"
            winning_hand_sorted = sorted(
                [t for t in self.hands[actor] if t and t != "?"],
                key=lambda x: SORT_ORDER.get(x, 99)
            )

            finish_call = f"ツモ！ [{tile_ja(pai)}]" if actor == target else f"◀ {SEAT_SHORT_NAMES[target]}から ロン！ [{tile_ja(pai)}]"
            self.step_animations.append({
                "kind": "call",
                "actor": actor,
                "actor_name": SEAT_NAMES[actor],
                "target": target,
                "call_text": finish_call
            })

            self.pending_round_result = {
                "type": "hora",
                "round_title": round_title,
                "actor": actor,
                "actor_name": SEAT_NAMES[actor],
                "target": target,
                "target_name": SEAT_NAMES[target],
                "is_tsumo": (actor == target),
                "way_str": way_str,
                "win_pai": pai,
                "hand": winning_hand_sorted,
                "melds": list(self.melds[actor]),
                "dora_markers": list(self.dora_markers),
                "uradora_markers": ev.get("uradora_markers") or [],
                "yakus": yakus,
                "rank_label": rank_label,
                "score_calc": score_calc,
                "won_points": deltas[actor],
                "deltas": deltas,
                "scores": new_scores,
                "round_end_animations": list(self.step_animations),
                "round_end_discards": {i: list(self.discards[i]) for i in range(4)},
                "round_review_list": list(self.round_review_list),
            }
            self.scores = list(new_scores)
            self.last_kyoku_summary = f"【前局結果】{SEAT_NAMES[actor]} が {way_str} ({deltas[actor]:+d}点)"

        elif etype == "ryukyoku":
            deltas = ev.get("deltas", [0] * 4)
            new_scores = ev.get("scores", [s + d for s, d in zip(self.scores, deltas)])
            bakaze_ja = WIND_KANJI.get(self.bakaze, self.bakaze)
            round_title = f"{bakaze_ja}{self.kyoku}局 {self.honba}本場"

            self.step_animations.append({
                "kind": "call",
                "actor": 0,
                "actor_name": "卓中央",
                "call_text": "流局"
            })

            self.pending_round_result = {
                "type": "ryukyoku",
                "round_title": round_title,
                "way_str": "流局（荒牌平局）",
                "deltas": deltas,
                "scores": new_scores,
                "round_end_animations": list(self.step_animations),
                "round_end_discards": {i: list(self.discards[i]) for i in range(4)},
                "round_review_list": list(self.round_review_list),
            }
            self.scores = list(new_scores)
            self.last_kyoku_summary = "【前局結果】流局"


def build_actor_riichi_action(actor_pid: int, obs, choice: str, evals: list[dict], last_resp: str, tracker: TableTracker):
    if obs is None:
        return None

    hand = tracker.hands[actor_pid]
    drawn_tile = tracker.drawn_tiles[actor_pid]
    last_d = tracker.player_last_discard.get(actor_pid) or tracker.last_discard

    # 1. まず obs.legal_actions() から構築したマップを直接参照
    lmap = extract_legal_action_map(obs)

    if choice == "hora":
        hora_act = find_hora_action_from_obs(obs, actor_pid, tracker)
        if hora_act is not None:
            return hora_act

    # ★修正：last_resp を使うのは「last_resp自体のアクションコードが choice と完全一致する場合のみ」にする
    # （カスタム教師AIで1位が入れ替わった際に、打牌を選んだのに last_resp の "reach" 等が発動するのを防止）
    if last_resp:
        try:
            resp_dict = json.loads(last_resp)
            resp_code = classify_mjai_to_action_code(resp_dict)
            if resp_code == choice:
                act = obs.select_action_from_mjai(resp_dict)
                if act is not None:
                    return act
        except Exception:
            pass

    if choice in lmap and lmap[choice]:
        if choice in TILES:
            prefer_tsumogiri = (drawn_tile is not None and norm_tile(drawn_tile) == norm_tile(choice))
            # リーチ宣言直後(pending_riichi_decl)でない限り、純粋なダマ打牌を優先選択する
            is_declaring_riichi = tracker.pending_riichi_decl.get(actor_pid, False)
            cands = lmap[choice]
            if not is_declaring_riichi:
                non_r = [pair for pair in cands if not _is_riichi_attached_action(pair[0], pair[1])]
                if non_r:
                    cands = non_r
            for act_obj, mj in cands:
                if mj.get("tsumogiri") == prefer_tsumogiri:
                    return act_obj
            return cands[0][0]
        return lmap[choice][0][0]

    # 赤牌と通常牌の表記揺れ吸収（5m <-> 5mr 等）
    if choice in TILES:
        for code_key, act_list in lmap.items():
            if code_key in TILES and norm_tile(code_key) == norm_tile(choice) and act_list:
                return act_list[0][0]

    if choice == "none":
        act = obs.select_action_from_mjai({"type": "none"})
        if act is not None:
            return act

    if choice in TILES:
        prefer_tsumogiri = (drawn_tile is not None and norm_tile(drawn_tile) == norm_tile(choice))
        tile_variants = [choice]
        if norm_tile(choice) != choice:
            tile_variants.append(norm_tile(choice))
        elif choice in ("5m", "5p", "5s"):
            tile_variants.append(f"{choice}r")

        for p_var in tile_variants:
            for t_flag in ([True, False] if prefer_tsumogiri else [False, True]):
                mjai = {"type": "dahai", "actor": actor_pid, "pai": p_var, "tsumogiri": t_flag}
                act = obs.select_action_from_mjai(mjai)
                if act is not None:
                    return act

    if choice == "reach":
        act = obs.select_action_from_mjai({"type": "reach", "actor": actor_pid})
        if act is not None:
            return act

    if choice == "ryukyoku":
        act = obs.select_action_from_mjai({"type": "ryukyoku", "actor": actor_pid})
        if act is not None:
            return act

    if choice == "pon" and last_d:
        target = last_d["actor"]
        pai = last_d["pai"]
        same_tiles = [t for t in hand if norm_tile(t) == norm_tile(pai)]
        for comb in itertools.combinations(same_tiles, 2):
            mjai = {"type": "pon", "actor": actor_pid, "target": target, "pai": pai, "consumed": list(comb)}
            act = obs.select_action_from_mjai(mjai)
            if act is not None:
                return act

    if choice in ("chi_low", "chi_mid", "chi_high") and last_d:
        target = last_d["actor"]
        pai = last_d["pai"]
        base_pai = norm_tile(pai)
        if len(base_pai) == 2 and base_pai[0].isdigit() and base_pai[1] in ("m", "p", "s"):
            num = int(base_pai[0])
            suit = base_pai[1]
            offset_map = {
                "chi_low": [(1, 2), (-2, -1)],
                "chi_mid": [(-1, 1)],
                "chi_high": [(-2, -1), (1, 2)],
            }
            for d1, d2 in offset_map[choice]:
                n1, n2 = num + d1, num + d2
                if 1 <= n1 <= 9 and 1 <= n2 <= 9:
                    t1_cands = [t for t in hand if norm_tile(t) == f"{n1}{suit}"]
                    t2_cands = [t for t in hand if norm_tile(t) == f"{n2}{suit}"]
                    for c1 in t1_cands:
                        for c2 in t2_cands:
                            mjai = {"type": "chi", "actor": actor_pid, "target": target, "pai": pai, "consumed": [c1, c2]}
                            act = obs.select_action_from_mjai(mjai)
                            if act is not None:
                                return act

    if choice == "kan":
        if drawn_tile is None and last_d:
            target = last_d["actor"]
            pai = last_d["pai"]
            same_tiles = [t for t in hand if norm_tile(t) == norm_tile(pai)]
            if len(same_tiles) >= 3:
                mjai = {"type": "daiminkan", "actor": actor_pid, "target": target, "pai": pai, "consumed": same_tiles[:3]}
                act = obs.select_action_from_mjai(mjai)
                if act is not None:
                    return act
        else:
            all_tiles = hand + ([drawn_tile] if drawn_tile else [])
            for t in set(norm_tile(x) for x in all_tiles):
                group = [x for x in all_tiles if norm_tile(x) == t]
                if len(group) == 4:
                    act = obs.select_action_from_mjai({"type": "ankan", "actor": actor_pid, "consumed": group})
                    if act is not None:
                        return act
                for x in group:
                    act = obs.select_action_from_mjai({"type": "kakan", "actor": actor_pid, "pai": x})
                    if act is not None:
                        return act

    if last_resp:
        try:
            act = obs.select_action_from_mjai(json.loads(last_resp))
            if act is not None:
                return act
        except Exception:
            pass
    legal = obs.legal_actions()
    return legal[0] if legal else None


def choose_opponent_action_by_level(
    pid: int,
    obs,
    evals: list[dict],
    last_resp: str,
    tracker: TableTracker,
    ai_level: int
):
    hora_act = find_hora_action_from_obs(obs, pid, tracker)
    if hora_act is not None:
        return hora_act

    if not evals or len(evals) == 1 or ai_level >= 5:
        return build_actor_riichi_action(
            pid, obs, evals[0]["action"] if evals else "", evals, last_resp, tracker
        )

    valid_names = [m["action"] for m in evals]
    is_discard_phase = any(name in TILES for name in valid_names)

    if not is_discard_phase and "none" in valid_names:
        pass_prob_map = {1: 0.85, 2: 0.65, 3: 0.35, 4: 0.10}
        if random.random() < pass_prob_map.get(ai_level, 0.0):
            return build_actor_riichi_action(pid, obs, "none", evals, last_resp, tracker)

    n_cands = len(evals)
    if ai_level == 4:
        pool = evals[:min(2, n_cands)]
        weights = [0.80, 0.20][:len(pool)]
    elif ai_level == 3:
        pool = evals[:min(4, n_cands)]
        weights = [0.50, 0.25, 0.15, 0.10][:len(pool)]
    elif ai_level == 2:
        pool = evals[:min(6, n_cands)]
        weights = [1.0] * len(pool)
    else:
        pool = evals[1:] if n_cands > 1 else evals
        weights = [1.0] * len(pool)

    chosen_item = random.choices(pool, weights=weights, k=1)[0]
    return build_actor_riichi_action(pid, obs, chosen_item["action"], evals, last_resp, tracker)


class GameSession:
    def __init__(self):
        print("AIモデル(Mortal)をロードしています...")
        self.bots = {pid: load_mortal_bot(pid) for pid in range(4)}
        self.human_pid = 0
        self.strictness_mode = "standard"
        self.ai_level = 3
        self.training_theme = "normal"
        self.teacher_type = "mortal"        # ← 追加：現在の教師AIタイプ ("mortal" | "search" ...)
        self.search_preset = "standard"     # ← 追加：探索型AIの探索強度 ("fast" | "standard" | "deep")
        self.roman_preset = "dream"         # ← 追加：ロマン型AIのロマン度 ("practical" | "dream" | "madness")
        self.speed_preset = "rapid"         # ← 追加：スピード特化
        self.enable_retry = True
        self.event_log_buffer = deque(maxlen=300)
        self.action_log_buffer = deque(maxlen=120)
        self.stats = {
            "total_turns": 0,
            "first_try_good": 0,
            "top1_matches": 0,
            "mistakes": 0,
            "mistake_log": []
        }
        self.reset_game(clear_stats=True)

    def _create_themed_env(self, theme: str):
        max_attempts = 1 if theme == "normal" else 450
        best_env = None
        best_score = -1e9

        for _ in range(max_attempts):
            env = RiichiEnv(game_mode="4p-red-half", rule=GameRule.default_tenhou())
            obs_dict = env.reset()
            if theme == "normal":
                return env, obs_dict

            cached_events = {pid: list(obs.new_events()) for pid, obs in obs_dict.items()}
            hand_raw = []
            dora_marker = "E"
            bakaze = "E"
            oya = 0

            for ev_str in cached_events.get(self.human_pid, []):
                ev = json.loads(ev_str)
                if ev.get("type") == "start_kyoku":
                    bakaze = ev.get("bakaze", "E")
                    oya = ev.get("oya", 0)
                    dora_marker = ev.get("dora_marker", "E")
                    tehais = ev.get("tehais", [[]] * 4)
                    if self.human_pid < len(tehais):
                        hand_raw = list(tehais[self.human_pid])
                elif ev.get("type") == "tsumo" and ev.get("actor") == self.human_pid:
                    pai = ev.get("pai")
                    if pai and pai != "?":
                        hand_raw.append(pai)

            qualified, score = evaluate_theme_hand_fitness(
                theme, hand_raw, dora_marker, bakaze, oya, self.human_pid
            )
            if score > best_score:
                best_score = score
                best_env = (env, obs_dict, cached_events)
            if qualified:
                return env, obs_dict, cached_events

        return best_env

    def reset_game(self, clear_stats: bool = False):
        if clear_stats:
            self.stats = {
                "total_turns": 0,
                "first_try_good": 0,
                "top1_matches": 0,
                "mistakes": 0,
                "mistake_log": []
            }
        self.event_log_buffer.clear()
        self.action_log_buffer.clear()

        res = self._create_themed_env(self.training_theme)
        if len(res) == 2:
            self.env, self.obs_dict = res
            self.initial_cached_events = None
        else:
            self.env, self.obs_dict, self.initial_cached_events = res

        self.tracker = TableTracker(human_pid=self.human_pid)
        self.pending_actions = {}
        self.human_obs = None
        self.human_last_resp = None
        self.raw_mortal_evals = []
        self.current_evals = []
        self.post_discard_hand = None
        self.allowed_choices = []
        self.retry_mode = False
        self.advance_until_human_turn()

    def _is_yaku_less_agari_shape(self) -> bool:
        drawn_now = self.tracker.drawn_tiles[self.human_pid]
        valid_names = [m["action"] for m in self.current_evals]
        if drawn_now is not None and "hora" not in valid_names:
            full_norm = [norm_tile(x) for x in (self.tracker.hands[self.human_pid] + [drawn_now])]
            sh_check = calc_shanten_all(full_norm, len(self.tracker.melds[self.human_pid]))
            if sh_check["shanten"] <= -1:
                return True
        return False

    def get_acceptable_moves(self, evals: list[dict]) -> tuple[list[dict], float]:
        if not evals:
            return [], 0.0

        if self._is_yaku_less_agari_shape():
            return evals[:3], 0.0

        valid_names = [m["action"] for m in evals]
        is_discard_phase = any(name in TILES for name in valid_names)
        is_few_options = (not is_discard_phase) or (len(evals) <= 3)

        if self.strictness_mode == "top1":
            return [evals[0]], 100.0
        elif self.strictness_mode == "strict":
            threshold = 75.0 if is_few_options else 65.0
        else:
            threshold = 55.0 if is_few_options else 45.0

        acceptable = []
        for idx, m in enumerate(evals):
            if (idx < 3 and (idx == 0 or m["score"] >= threshold)) or m["action"] == "hora":
                acceptable.append(m)
        return acceptable, threshold

    def _ensure_human_evals_synchronized(self, obs, evals: list[dict], last_resp: str) -> list[dict]:
        """
        Mortalの評価リスト(evals)と riichienv の実合法手(obs.legal_actions())を完全同期し、
        万が一の手牌リストのズレや赤牌表記揺れ・選択不能バグを自動修復する
        """
        if obs is not None:
            # ★修正：リーチ宣言直後でも手牌枚数ズレが起きないよう、常に14枚状態の整合性をチェックする
            dahai_pais = []
            for act in obs.legal_actions():
                mj = action_to_mjai_dict(act)
                if mj.get("type") == "dahai" and mj.get("pai"):
                    p = mj["pai"]
                    if p not in dahai_pais or dahai_pais.count(p) < (list(self.tracker.hands[self.human_pid]) + ([self.tracker.drawn_tiles[self.human_pid]] if self.tracker.drawn_tiles[self.human_pid] else [])).count(p):
                        dahai_pais.append(p)

            num_melds = len(self.tracker.melds[self.human_pid])
            expected_13 = (4 - num_melds) * 3 + 1
            expected_14 = expected_13 + 1

            # もし tracker 側の手牌+ツモ牌が14枚を超えている場合は drawn_tiles の重複を解消
            cur_hand = list(self.tracker.hands[self.human_pid])
            cur_drawn = self.tracker.drawn_tiles[self.human_pid]
            if cur_drawn is not None and len(cur_hand) >= expected_14:
                if cur_drawn in cur_hand:
                    cur_hand.remove(cur_drawn)
                else:
                    cur_drawn = cur_hand.pop()
                self.tracker.hands[self.human_pid] = cur_hand
                self.tracker.drawn_tiles[self.human_pid] = cur_drawn

            if not self.tracker.riichi.get(self.human_pid, False):
                if self.tracker.drawn_tiles[self.human_pid] is not None and len(dahai_pais) == expected_14:
                    cur_full = list(self.tracker.hands[self.human_pid]) + [self.tracker.drawn_tiles[self.human_pid]]
                    if Counter(cur_full) != Counter(dahai_pais):
                        drawn_cand = self.tracker.drawn_tiles[self.human_pid]
                        if drawn_cand not in dahai_pais:
                            drawn_cand = dahai_pais[-1]
                        rem_hand = list(dahai_pais)
                        rem_hand.remove(drawn_cand)
                        self.tracker.hands[self.human_pid] = rem_hand
                        self.tracker.drawn_tiles[self.human_pid] = drawn_cand

        lmap = extract_legal_action_map(obs)
        can_hora = ("hora" in lmap) or (find_hora_action_from_obs(obs, self.human_pid, self.tracker) is not None)

        if not evals and last_resp:
            try:
                resp_obj = json.loads(last_resp)
                rtype = resp_obj.get("type")
                if rtype == "hora" or can_hora:
                    evals = [{"action": "hora", "q_value": 10.0, "score": 100.0, "prob": 100.0}]
                elif rtype == "dahai" and resp_obj.get("pai"):
                    evals = [{"action": resp_obj["pai"], "q_value": 0.0, "score": 100.0, "prob": 100.0}]
            except Exception:
                pass

        if lmap:
            synced_evals = []
            seen_codes = set()
            for item in evals:
                act_code = item["action"]
                if act_code in lmap:
                    synced_evals.append(item)
                    seen_codes.add(act_code)
                elif act_code in TILES:
                    alt_code = next(
                        (k for k in lmap.keys() if k in TILES and norm_tile(k) == norm_tile(act_code)),
                        None
                    )
                    if alt_code and alt_code not in seen_codes:
                        new_item = dict(item)
                        new_item["action"] = alt_code
                        synced_evals.append(new_item)
                        seen_codes.add(alt_code)

            for legal_code in lmap.keys():
                if legal_code not in seen_codes:
                    default_q = 10.0 if legal_code == "hora" else -5.0
                    default_sc = 100.0 if legal_code == "hora" else 15.0
                    synced_evals.append({
                        "action": legal_code,
                        "q_value": default_q,
                        "score": default_sc,
                        "prob": 0.5,
                    })
                    seen_codes.add(legal_code)

            if synced_evals:
                evals = synced_evals

        if can_hora:
            existing_hora = next((m for m in evals if m["action"] == "hora"), None)
            if existing_hora is None:
                evals.insert(0, {"action": "hora", "q_value": 10.0, "score": 100.0, "prob": 99.9})
            if len(evals) == 1 and self.tracker.drawn_tiles[self.human_pid] is not None:
                drawn = self.tracker.drawn_tiles[self.human_pid]
                evals.append({"action": drawn, "q_value": -10.0, "score": 10.0, "prob": 0.1})
            elif len(evals) == 1 and self.tracker.drawn_tiles[self.human_pid] is None:
                evals.append({"action": "none", "q_value": -10.0, "score": 10.0, "prob": 0.1})

        return evals

    def _apply_teacher_ai(self, raw_evals: list[dict]) -> list[dict]:
        """現在の self.teacher_type に応じた教師AIで評価リストを再計算する"""
        if not raw_evals:
            return []
        teacher = get_teacher_ai(self.teacher_type)
        if teacher.teacher_id == "mortal":
            return teacher.evaluate(raw_evals, self.tracker, self.human_obs, {}, {}, [])

        tenpai_info = analyze_hand_shanten_and_ukeire(self.tracker)
        danger_info = analyze_danger_tiles(self.tracker)
        potential_yakus = analyze_potential_yakus(self.tracker)
        score_estimate = analyze_realtime_score_estimate(self.tracker, tenpai_info, potential_yakus)
        active_preset = self.roman_preset
        if teacher.teacher_id == "roman":
            active_preset = self.roman_preset
        elif teacher.teacher_id == "speed":
            active_preset = self.speed_preset
        else:
            active_preset = self.search_preset
        return teacher.evaluate(
            base_evals=raw_evals,
            tracker=self.tracker,
            obs=self.human_obs,
            tenpai_info=tenpai_info,
            danger_info=danger_info,
            potential_yakus=potential_yakus,
            score_estimate=score_estimate,
            preset_key=active_preset,       # ← 更新
        )
    def _finalize_if_game_done(self, last_step_actions: dict = None):
        """半荘終了(env.done() == True)時に残りのイベント回収と最終局結果の生成・選択状態のクリアを行う"""
        if not self.env.done():
            return

        t = self.tracker

        if self.obs_dict:
            for pid, obs in list(self.obs_dict.items()):
                try:
                    for ev_str in obs.new_events():
                        t.update_from_player_obs(pid, ev_str)
                except Exception:
                    pass

        human_round_evs = t.round_events_by_pid.get(self.human_pid, [])
        if human_round_evs:
            h_start = json.loads(human_round_evs[0])
            h_key = (h_start.get("bakaze"), h_start.get("kyoku"), h_start.get("honba"))
            best_pid = self.human_pid
            max_len = len(human_round_evs)
            for pid in range(4):
                p_evs = t.round_events_by_pid.get(pid, [])
                if len(p_evs) > max_len:
                    p_start = json.loads(p_evs[0])
                    if (p_start.get("bakaze"), p_start.get("kyoku"), p_start.get("honba")) == h_key:
                        best_pid = pid
                        max_len = len(p_evs)
            if best_pid != self.human_pid:
                for extra_ev_str in t.round_events_by_pid[best_pid][len(human_round_evs):]:
                    t.update_from_player_obs(self.human_pid, extra_ev_str)

        if last_step_actions:
            for pid, act_obj in last_step_actions.items():
                mj = action_to_mjai_dict(act_obj)
                if mj.get("type") == "dahai":
                    pai = mj.get("pai", "")
                    tsumogiri = bool(mj.get("tsumogiri", False))
                    if pai and (not t.last_discard or t.last_discard.get("actor") != pid or t.last_discard.get("pai") != pai):
                        if tsumogiri and t.drawn_tiles[pid] is not None and norm_tile(t.drawn_tiles[pid]) == norm_tile(pai):
                            t.drawn_tiles[pid] = None
                        else:
                            if t.drawn_tiles[pid] is not None:
                                t.hands[pid].append(t.drawn_tiles[pid])
                                t.drawn_tiles[pid] = None
                            t._remove_from_player_hand(pid, pai)
                        t.last_discard = {"actor": pid, "pai": pai}
                        t.discards[pid].append({
                            "pai": pai, "tsumogiri": tsumogiri, "is_riichi": False,
                            "called_by": None, "called_by_name": None, "call_type": None,
                        })
                        t.step_animations.append({
                            "kind": "dahai", "actor": pid, "actor_name": SEAT_NAMES[pid],
                            "pai": pai, "tsumogiri": tsumogiri, "is_riichi": False,
                        })

        final_scores = list(self.env.scores())
        prev_scores = list(t.scores)
        deltas = [f - p for f, p in zip(final_scores, prev_scores)]
        bakaze_ja = WIND_KANJI.get(t.bakaze, t.bakaze)
        round_title = f"{bakaze_ja}{t.kyoku}局 {t.honba}本場 (半荘終了)"

        if t.pending_round_result is None:
            hora_actor = None
            if last_step_actions:
                for pid, act_obj in last_step_actions.items():
                    if action_to_mjai_dict(act_obj).get("type") == "hora":
                        hora_actor = pid
                        break

            max_delta = max(deltas) if deltas else 0
            plus_pids = [i for i, d in enumerate(deltas) if d > 0]
            minus_pids = [i for i, d in enumerate(deltas) if d < 0]

            if hora_actor is not None or (len(plus_pids) == 1 and (max_delta > 3000 or t.wall_remaining > 2)):
                actor = hora_actor if hora_actor is not None else plus_pids[0]
                target = minus_pids[0] if len(minus_pids) == 1 else actor
                win_pai = (
                    (t.drawn_tiles[actor] if actor == target else None)
                    or (t.last_discard["pai"] if t.last_discard else None)
                    or (t.hands[actor][-1] if t.hands[actor] else "1m")
                )
                if actor != target and target in t.discards and len(t.discards[target]) > 0:
                    t.discards[target][-1]["called_by"] = actor
                    t.discards[target][-1]["called_by_name"] = SEAT_SHORT_NAMES[actor]
                    t.discards[target][-1]["call_type"] = "ロン"

                yakus, rank_label, score_calc = analyze_hora_yakus(
                    t, actor, target, win_pai, {"deltas": deltas, "scores": final_scores}
                )
                way_str = "ツモ和了" if actor == target else f"◀ {SEAT_NAMES[target]} から ロン和了"
                finish_call = f"ツモ！ [{tile_ja(win_pai)}]" if actor == target else f"◀ {SEAT_SHORT_NAMES[target]}から ロン！ [{tile_ja(win_pai)}]"
                t.step_animations.append({
                    "kind": "call", "actor": actor, "actor_name": SEAT_NAMES[actor],
                    "target": target, "call_text": finish_call,
                })
                winning_hand_sorted = sorted(
                    [x for x in t.hands[actor] if x and x != "?"],
                    key=lambda x: SORT_ORDER.get(x, 99)
                )
                t.pending_round_result = {
                    "type": "hora",
                    "round_title": round_title,
                    "actor": actor,
                    "actor_name": SEAT_NAMES[actor],
                    "target": target,
                    "target_name": SEAT_NAMES[target],
                    "is_tsumo": (actor == target),
                    "way_str": way_str,
                    "win_pai": win_pai,
                    "hand": winning_hand_sorted,
                    "melds": list(t.melds[actor]),
                    "dora_markers": list(t.dora_markers),
                    "uradora_markers": [],
                    "yakus": yakus,
                    "rank_label": rank_label,
                    "score_calc": score_calc,
                    "won_points": deltas[actor],
                    "deltas": deltas,
                    "scores": final_scores,
                    "round_end_animations": list(t.step_animations),
                    "round_end_discards": {i: list(t.discards[i]) for i in range(4)},
                    "round_review_list": list(t.round_review_list),
                }
            else:
                t.step_animations.append({
                    "kind": "call", "actor": 0, "actor_name": "卓中央", "call_text": "流局（半荘終了）"
                })
                t.pending_round_result = {
                    "type": "ryukyoku",
                    "round_title": round_title,
                    "way_str": "流局（半荘終了）",
                    "deltas": deltas,
                    "scores": final_scores,
                    "round_end_animations": list(t.step_animations),
                    "round_end_discards": {i: list(t.discards[i]) for i in range(4)},
                    "round_review_list": list(t.round_review_list),
                }

        t.scores = final_scores
        self.allowed_choices = []
        self.current_evals = []
        self.retry_mode = False

    def advance_until_human_turn(self):
        safety_counter = 0
        last_step_actions = dict(self.pending_actions) if self.pending_actions else {}
        while not self.env.done():
            safety_counter += 1
            if safety_counter > 500:
                break

            self.pending_actions = {}
            human_needs_input = False

            ordered_pids = [p for p in range(4) if p != self.human_pid] + [self.human_pid]

            for pid in ordered_pids:
                if pid not in self.obs_dict:
                    continue
                obs = self.obs_dict[pid]
                last_resp = None

                if self.initial_cached_events is not None and pid in self.initial_cached_events:
                    events_list = self.initial_cached_events.pop(pid)
                    if not self.initial_cached_events:
                        self.initial_cached_events = None
                else:
                    events_list = obs.new_events()

                for event_str in events_list:
                    try:
                        ev_obj = json.loads(event_str)
                        if pid == self.human_pid or ev_obj.get("type") != "tsumo" or ev_obj.get("actor") == pid:
                            self.event_log_buffer.append(ev_obj)
                    except Exception:
                        pass
                    self.tracker.update_from_player_obs(pid, event_str)
                    resp = self.bots[pid].react(event_str)
                    if resp is not None:
                        last_resp = resp

                evals = parse_evaluations(last_resp)

                if pid != self.human_pid:
                    self.pending_actions[pid] = choose_opponent_action_by_level(
                        pid, obs, evals, last_resp, self.tracker, self.ai_level
                    )
                else:
                    raw_evals = self._ensure_human_evals_synchronized(obs, evals, last_resp)
                    # ★修正：自動スキップするのは「リーチ宣言直後ではなく、かつ既にリーチ後のツモ切り（または候補がnoneのみ）」のときだけにする
                    # 特に「リーチボタンを押した直後の宣言牌選択（pending_riichi_decl[0] == True）」は候補が1枚でも必ず画面に表示してプレイヤーに切らせる
                    is_declaring_riichi_now = self.tracker.pending_riichi_decl.get(self.human_pid, False)
                    has_hora = any(m["action"] == "hora" for m in raw_evals)

                    if (
                        len(raw_evals) <= 1
                        and not has_hora
                        and not is_declaring_riichi_now
                    ):
                        act = build_actor_riichi_action(
                            pid, obs, raw_evals[0]["action"] if raw_evals else "", raw_evals, last_resp, self.tracker
                        )
                        self.pending_actions[pid] = act
                    else:
                        self.human_obs = obs
                        self.human_last_resp = last_resp
                        self.raw_mortal_evals = raw_evals
                        self.current_evals = self._apply_teacher_ai(raw_evals)
                        self.allowed_choices = [m["action"] for m in self.current_evals]
                        self.retry_mode = False
                        human_needs_input = True

            if human_needs_input:
                return

            last_step_actions = dict(self.pending_actions)
            self.obs_dict = self.env.step(self.pending_actions)
        if self.env.done():
            self._finalize_if_game_done(last_step_actions)


    def get_state_payload(self, feedback=None):
        bakaze_ja = WIND_KANJI.get(self.tracker.bakaze, self.tracker.bakaze)
        sorted_hand = sorted(self.tracker.hands[self.human_pid], key=lambda x: SORT_ORDER.get(x, 99))
        valid_names = [m["action"] for m in self.current_evals]
        is_discard_phase = any(name in TILES for name in valid_names) or (self.tracker.drawn_tiles[self.human_pid] is not None)
        special_actions = [
            {"id": a, "label": ACTION_LABELS.get(a, a)}
            for a in valid_names if a not in TILES
        ]

        drawn_now = self.tracker.drawn_tiles[self.human_pid]
        yaku_warning = None
        if self._is_yaku_less_agari_shape():
            best_fix = self.current_evals[0]["action"] if self.current_evals else ""
            yaku_warning = (
                f"🚨 【役なし警告】 手牌は4面子1雀頭の形になっていますが、役がないためツモ和了できません！"
                f"（AI推奨の 打 [{tile_ja(best_fix)}] などでタンヤオ等の役あり聴牌へ組み替えるか、形式聴牌を維持してください）"
            )

        players_info = []
        for pid in range(4):
            w_code = WIND_CYCLE[(pid - self.tracker.oya) % 4]
            p_sorted_hand = sorted(self.tracker.hands[pid], key=lambda x: SORT_ORDER.get(x, 99))
            players_info.append({
                "pid": pid,
                "name": SEAT_NAMES[pid],
                "wind": WIND_KANJI.get(w_code, w_code),
                "is_oya": (pid == self.tracker.oya),
                "score": self.tracker.scores[pid],
                "riichi": self.tracker.riichi[pid],
                "hand_count": len(self.tracker.hands[pid]) or max(1, 13 - len(self.tracker.melds[pid]) * 3),
                "hand": p_sorted_hand,
                "drawn_tile": self.tracker.drawn_tiles[pid],
                "discards": self.tracker.discards[pid],
                "melds": self.tracker.melds[pid],
            })

        round_result = self.tracker.pending_round_result
        self.tracker.pending_round_result = None

        step_animations = list(self.tracker.step_animations)
        self.tracker.step_animations.clear()

        potential_yakus = analyze_potential_yakus(self.tracker)
        tenpai_info = analyze_hand_shanten_and_ukeire(self.tracker)
        if yaku_warning:
            tenpai_info["yaku_warning"] = yaku_warning

        # ▼ 追加：リアルタイム点数計算（符計算・飜計算の予想・候補）
        score_estimate = analyze_realtime_score_estimate(self.tracker, tenpai_info, potential_yakus)


        danger_info = analyze_danger_tiles(self.tracker)
        acceptable_moves, threshold = self.get_acceptable_moves(self.current_evals)
        teacher_obj = get_teacher_ai(self.teacher_type)  # ← ★この1行を追加！

        # ▼ 追加：現在の手番局面に対するAI質問プロンプトを生成
        current_ai_prompt = build_llm_question_prompt(
            self.tracker,
            self.current_evals,
            tenpai_info,
            danger_info,
            potential_yakus,
            user_choice=None,
            teacher_name=teacher_obj.name,  # ← ★教師AI名を渡す
        )
        eval_hint_map = {
            m["action"]: {
                "rank": i + 1,
                "score": m["score"],
                "prob": m["prob"],
                "teacher_note": m.get("teacher_note", ""),
                "is_acceptable": m["action"] in [x["action"] for x in acceptable_moves]
            }
            for i, m in enumerate(self.current_evals)
        }

        # ▼ 追加：現在の手牌（現手番）に対する上位3候補データを構築（ヒントON時表示用）
        acceptable_names_now = [x["action"] for x in acceptable_moves]
        u_map_now = tenpai_info.get("discard_ukeire_map", {})
        current_top_3 = []
        for i, m in enumerate(self.current_evals[:3]):
            act_name = m["action"]
            u_entry = u_map_now.get(act_name) or u_map_now.get(norm_tile(act_name))
            shanten_part = shanten_ja(u_entry["shanten"]) if u_entry else ""
            ukeire_part = f"{u_entry['ukeire_types']}種{u_entry['total_remaining']}枚" if u_entry else ""
            current_top_3.append({
                "rank": i + 1,
                "action": act_name,
                "label": ACTION_LABELS.get(act_name, act_name),
                "score": m["score"],
                "prob": m["prob"],
                "q_value": m["q_value"],
                "ukeire_badge": f"{shanten_part} / {ukeire_part}" if u_entry else "",
                "shanten_part": shanten_part,
                "ukeire_part": ukeire_part,
                "teacher_note": m.get("teacher_note", ""),
                "mortal_rank": m.get("mortal_rank"),
                "is_acceptable": act_name in acceptable_names_now,
            })

        theme_meta = TRAINING_THEMES.get(self.training_theme, TRAINING_THEMES["normal"])
        post_discard_hand = self.post_discard_hand
        self.post_discard_hand = None
        return {
            "done": self.env.done(),
            "final_scores": self.env.scores() if self.env.done() else None,
            "round_str": f"{bakaze_ja}{self.tracker.kyoku}局",
            "honba": self.tracker.honba,
            "kyotaku": self.tracker.kyotaku,
            "wall_remaining": self.tracker.wall_remaining,
            "dora_markers": self.tracker.dora_markers,
            "actual_doras": [get_actual_dora(m) for m in self.tracker.dora_markers],
            "last_kyoku_summary": self.tracker.last_kyoku_summary,
            "round_result": round_result,
            "step_animations": step_animations,
            "post_discard_hand": post_discard_hand,     # ← ★追加：打牌直後のソート済み13枚
            "potential_yakus": potential_yakus,
            "score_estimate": score_estimate,
            "call_history": self.tracker.call_history,
            "tenpai_info": tenpai_info,
            "danger_info": danger_info,
            "players": players_info,
            "hand": sorted_hand,
            "drawn_tile": drawn_now,
            "is_call_chance": (not is_discard_phase) and (not self.env.done()),
            "last_discard": {
                "actor": self.tracker.last_discard["actor"],
                "actor_name": SEAT_NAMES[self.tracker.last_discard["actor"]],
                "pai": self.tracker.last_discard["pai"]
            } if self.tracker.last_discard else None,
            "special_actions": special_actions,
            "allowed_choices": self.allowed_choices,
            "retry_mode": self.retry_mode,
            "enable_retry": self.enable_retry,
            "strictness_mode": self.strictness_mode,
            "ai_level": self.ai_level,
            "training_theme": self.training_theme,
            "training_theme_name": theme_meta["name"],
            "training_theme_desc": theme_meta["desc"],
            "current_threshold": threshold,
            "eval_hint_map": eval_hint_map,
            "current_top_3": current_top_3,
            "stats": self.stats,
            "current_ai_prompt": current_ai_prompt,
            "feedback": feedback,
            "teacher_type": self.teacher_type,
            "teacher_name": teacher_obj.name,
            "teacher_desc": teacher_obj.description,
            "available_teachers": get_teachers_metadata(),
            "search_preset": self.search_preset,
            "available_search_presets": get_search_presets_metadata(),
            "roman_preset": self.roman_preset,
            "available_roman_presets": get_roman_presets_metadata(),
            "speed_preset": self.speed_preset,
            "available_speed_presets": get_speed_presets_metadata(),
        }

    def submit_human_choice(self, choice: str):
        if self.env.done():
            return self.get_state_payload()

        raw_choice = choice
        drawn_now = self.tracker.drawn_tiles[self.human_pid]
        if (
            "hora" in self.allowed_choices
            and drawn_now is not None
            and choice in TILES
            and norm_tile(choice) == norm_tile(drawn_now)
        ):
            choice = "hora"

        if choice not in self.allowed_choices:
            alt = next((c for c in self.allowed_choices if c in TILES and norm_tile(c) == norm_tile(choice)), None)
            if alt:
                choice = alt

        # もし allowed_choices に無くても riichienv の実合法手に存在するなら救済する
        lmap = extract_legal_action_map(self.human_obs)
        if choice not in self.allowed_choices and choice in lmap:
            self.allowed_choices.append(choice)
            if not any(m["action"] == choice for m in self.current_evals):
                self.current_evals.append({
                    "action": choice, "q_value": -5.0, "score": 20.0, "prob": 0.5
                })

        self.action_log_buffer.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "raw_choice": raw_choice,
            "resolved_choice": choice,
            "allowed_choices": list(self.allowed_choices),
            "retry_mode": self.retry_mode,
        })

        if choice not in self.allowed_choices:
            return self.get_state_payload(feedback={
                "status": "invalid",
                "message": f"[{choice}] は現在選択できません。ハイライトされた合格候補（またはアガリボタン）から選んでください。"
            })
        pre_tenpai_info = analyze_hand_shanten_and_ukeire(self.tracker)
        pre_danger_info = analyze_danger_tiles(self.tracker)
        pre_potential_yakus = analyze_potential_yakus(self.tracker)
        comparison_reasons = generate_ai_comparison_explanation(
            choice, self.current_evals, self.tracker, pre_tenpai_info, pre_danger_info
        )
        teacher_obj = get_teacher_ai(self.teacher_type)
        decision_ai_prompt = build_llm_question_prompt(
            self.tracker,
            self.current_evals,
            pre_tenpai_info,
            pre_danger_info,
            pre_potential_yakus,
            user_choice=choice,
            teacher_name=teacher_obj.name,
        )

        chosen_eval = next(m for m in self.current_evals if m["action"] == choice)
        rank = self.current_evals.index(chosen_eval) + 1
        top_3_moves = self.current_evals[:3]

        acceptable_moves, threshold = self.get_acceptable_moves(self.current_evals)
        acceptable_names = [m["action"] for m in acceptable_moves]
        # リーチ宣言直後の宣言牌選択中であれば、テンパイ打牌はすべて合格扱いにする
        is_declaring_riichi_step = self.tracker.pending_riichi_decl.get(self.human_pid, False)
        is_acceptable = (choice in acceptable_names) or is_declaring_riichi_step

        u_map = pre_tenpai_info.get("discard_ukeire_map", {})
        top_3_formatted = []
        for i, m in enumerate(top_3_moves):
            act_name = m["action"]
            u_entry = u_map.get(act_name) or u_map.get(norm_tile(act_name))
            ukeire_badge = ""
            shanten_part = ""
            ukeire_part = ""
            if u_entry:
                shanten_part = shanten_ja(u_entry["shanten"])
                ukeire_part = f"{u_entry['ukeire_types']}種{u_entry['total_remaining']}枚"
                ukeire_badge = f"{shanten_part} / {ukeire_part}"
            top_3_formatted.append({
                "rank": i + 1,
                "action": act_name,
                "label": ACTION_LABELS.get(act_name, act_name),
                "score": m["score"],
                "prob": m["prob"],
                "q_value": m["q_value"],
                "ukeire_badge": ukeire_badge,
                "shanten_part": shanten_part,
                "ukeire_part": ukeire_part,
                "teacher_note": m.get("teacher_note", ""),
                "mortal_rank": m.get("mortal_rank"),
                "is_acceptable": act_name in acceptable_names
            })

        # リーチ宣言ボタンを押した直後の「宣言牌を切るステップ」では、すでにリーチボタン押下時に1ターン計上しているため二重加算しない
        if not self.retry_mode and not is_declaring_riichi_step:
            self.stats["total_turns"] += 1
            bakaze_ja = WIND_KANJI.get(self.tracker.bakaze, self.tracker.bakaze)
            turn_num = len(self.tracker.discards[self.human_pid]) + 1
            best_m = self.current_evals[0]

            if rank == 1:
                self.stats["top1_matches"] += 1
            else:
                hand_snap = sorted(self.tracker.hands[self.human_pid], key=lambda x: SORT_ORDER.get(x, 99))
                drawn_snap = self.tracker.drawn_tiles[self.human_pid]
                is_call_snap = (drawn_snap is None and not any(m["action"] in TILES for m in self.current_evals))
                self.tracker.round_review_list.append({
                    "turn_num": turn_num,
                    "round_turn_str": f"{bakaze_ja}{self.tracker.kyoku}局 {turn_num}巡目",
                    "hand": hand_snap,
                    "drawn_tile": drawn_snap,
                    "is_call_chance": is_call_snap,
                    "call_target_pai": self.tracker.last_discard["pai"] if (is_call_snap and self.tracker.last_discard) else None,
                    "chosen": choice,
                    "chosen_label": f"打 {tile_ja(choice)}" if choice in TILES else ACTION_LABELS.get(choice, choice),
                    "chosen_rank": rank,
                    "chosen_score": chosen_eval["score"],
                    "chosen_prob": chosen_eval["prob"],
                    "best": best_m["action"],
                    "best_label": f"打 {tile_ja(best_m['action'])}" if best_m["action"] in TILES else ACTION_LABELS.get(best_m["action"], best_m["action"]),
                    "best_score": best_m["score"],
                    "best_prob": best_m["prob"],
                    "is_acceptable": is_acceptable,
                    "reason_summary": comparison_reasons[1] if len(comparison_reasons) > 1 else (comparison_reasons[0] if comparison_reasons else ""),
                    "ai_prompt": decision_ai_prompt,
                })

            if is_acceptable:
                self.stats["first_try_good"] += 1
            else:
                self.stats["mistakes"] += 1
                self.stats["mistake_log"].insert(0, {
                    "round": f"{bakaze_ja}{self.tracker.kyoku}局 {turn_num}巡目",
                    "chosen": ACTION_LABELS.get(choice, choice),
                    "chosen_score": chosen_eval["score"],
                    "chosen_rank": rank,
                    "best": ACTION_LABELS.get(best_m["action"], best_m["action"]),
                    "best_score": best_m["score"]
                })
                self.stats["mistake_log"] = self.stats["mistake_log"][:15]

        if is_acceptable or not self.enable_retry:
            if is_acceptable:
                status_str = "good"
                msg_str = f"【好手】合格基準（上位3位以内＆スコア{threshold:.0f}点以上）をクリア！対局を続行します。"
            else:
                status_str = "bad_proceed"
                msg_str = (
                    f"【悪手（再選択OFF）】第{rank}位（{chosen_eval['score']}点）で基準外ですが、"
                    f"再選択機能がOFFのためそのまま対局を進行します。"
                )

            # ★追加：牌を切った直後のソート済み13枚手牌を保存（UIでCPU行動中に表示するため）
            if choice in TILES:
                full_before = list(self.tracker.hands[self.human_pid])
                if self.tracker.drawn_tiles[self.human_pid] is not None:
                    full_before.append(self.tracker.drawn_tiles[self.human_pid])
                if choice in full_before:
                    full_before.remove(choice)
                else:
                    alt_rm = next((t for t in full_before if norm_tile(t) == norm_tile(choice)), None)
                    if alt_rm:
                        full_before.remove(alt_rm)
                self.post_discard_hand = sorted(full_before, key=lambda x: SORT_ORDER.get(x, 99))
            elif choice == "none":
                self.post_discard_hand = sorted(self.tracker.hands[self.human_pid], key=lambda x: SORT_ORDER.get(x, 99))
            else:
                self.post_discard_hand = None

            feedback = {
                "status": status_str,
                "choice": choice,
                "choice_label": ACTION_LABELS.get(choice, choice),
                "rank": rank,
                "total_candidates": len(self.current_evals),
                "score": chosen_eval["score"],
                "prob": chosen_eval["prob"],
                "q_value": chosen_eval["q_value"],
                "top_3": top_3_formatted,
                "comparison_reasons": comparison_reasons,
                "ai_question_prompt": decision_ai_prompt,
                "message": msg_str
            }
            self.tracker.step_animations.clear()
            act = build_actor_riichi_action(
                self.human_pid, self.human_obs, choice, self.current_evals, self.human_last_resp, self.tracker
            )
            self.pending_actions[self.human_pid] = act
            self.obs_dict = self.env.step(self.pending_actions)
            self.advance_until_human_turn()
            return self.get_state_payload(feedback=feedback)
        else:
            self.allowed_choices = acceptable_names
            self.retry_mode = True
            if rank <= 3:
                reason_msg = (
                    f"【悪手判定】順位は第{rank}位ですが、評価スコアが極端に低い"
                    f"（{chosen_eval['score']}点 < 基準{threshold:.0f}点）ため悪手です！"
                    f"低評価の候補を除外しました。金色に光る最善手から選び直してください。"
                )
            else:
                reason_msg = (
                    f"【悪手判定】第{rank}位（{chosen_eval['score']}点）で基準外です。"
                    f"金色で強調された合格候補（{len(acceptable_names)}手）から選び直してください。"
                )
            feedback = {
                "status": "bad",
                "choice": choice,
                "choice_label": ACTION_LABELS.get(choice, choice),
                "rank": rank,
                "total_candidates": len(self.current_evals),
                "score": chosen_eval["score"],
                "prob": chosen_eval["prob"],
                "q_value": chosen_eval["q_value"],
                "top_3": top_3_formatted,
                "comparison_reasons": comparison_reasons,
                "ai_question_prompt": decision_ai_prompt,
                "message": reason_msg
            }
            return self.get_state_payload(feedback=feedback)

    def dump_diagnostic_log(self, user_note: str = "", client_state: dict = None) -> dict:
        """現在の内部状態・手牌・合法手・直近イベントを diagnostic_logs フォルダにJSON保存する"""
        log_dir = os.path.join(BASE_DIR, "diagnostic_logs")
        os.makedirs(log_dir, exist_ok=True)
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"mahjong_diag_{now_str}.json"
        filepath = os.path.join(log_dir, filename)

        legal_list = []
        if self.human_obs is not None:
            for a in self.human_obs.legal_actions():
                legal_list.append({
                    "repr": str(a),
                    "mjai": action_to_mjai_dict(a),
                    "classified_code": classify_mjai_to_action_code(action_to_mjai_dict(a)),
                })

        t = self.tracker
        diag_data = {
            "timestamp": datetime.now().isoformat(),
            "user_note": user_note,
            "round": f"{t.bakaze}{t.kyoku}局 {t.honba}本場 (供託:{t.kyotaku})",
            "wall_remaining": t.wall_remaining,
            "scores": t.scores,
            "settings": {
                "ai_level": self.ai_level,
                "strictness_mode": self.strictness_mode,
                "training_theme": self.training_theme,
                "enable_retry": self.enable_retry,
                "retry_mode": self.retry_mode,
            },
            "tracker_state": {
                "hands": t.hands,
                "drawn_tiles": t.drawn_tiles,
                "melds": t.melds,
                "riichi": t.riichi,
                "last_discard": t.last_discard,
                "player_last_discard": t.player_last_discard,
                "discards_count": {i: len(t.discards[i]) for i in range(4)},
            },
            "human_decision_state": {
                "allowed_choices": self.allowed_choices,
                "current_evals": self.current_evals,
                "human_last_resp": json.loads(self.human_last_resp) if self.human_last_resp else None,
                "riichienv_legal_actions": legal_list,
            },
            "recent_action_history": list(self.action_log_buffer),
            "recent_mjai_events": list(self.event_log_buffer)[-60:],
            "client_ui_snapshot": client_state,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(diag_data, f, ensure_ascii=False, indent=2)

        latest_path = os.path.join(log_dir, "mahjong_diag_latest.json")
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(diag_data, f, ensure_ascii=False, indent=2)

        return {
            "ok": True,
            "filename": filename,
            "filepath": filepath,
            "legal_count": len(legal_list),
            "allowed_choices": self.allowed_choices,
        }


session = GameSession()


@app.route("/")
def index():
    cancel_pending_shutdown()
    return send_file(get_resource_path("index.html"))


@app.route("/api/state", methods=["GET"])
def api_state():
    cancel_pending_shutdown()
    with _session_lock:
        return jsonify(session.get_state_payload())


@app.route("/api/action", methods=["POST"])
def api_action():
    cancel_pending_shutdown()
    data = request.get_json() or {}
    choice = data.get("choice", "")
    with _session_lock:
        return jsonify(session.submit_human_choice(choice))


@app.route("/api/settings", methods=["POST"])
def api_settings():
    cancel_pending_shutdown()
    data = request.get_json() or {}
    theme_changed = False
    

    with _session_lock:
        if "device" in data:
            from teachers.search_teacher import load_search_weights, save_search_weights
            save_search_weights({"device": data["device"]})

        if "strictness_mode" in data:
            mode = data["strictness_mode"]
            if mode in ("standard", "strict", "top1"):
                session.strictness_mode = mode
                if session.retry_mode:
                    acceptable, _ = session.get_acceptable_moves(session.current_evals)
                    session.allowed_choices = [m["action"] for m in acceptable]

        if "ai_level" in data:
            lvl = int(data["ai_level"])
            if 1 <= lvl <= 5:
                session.ai_level = lvl

        if "training_theme" in data:
            new_theme = data["training_theme"]
            if new_theme in TRAINING_THEMES and new_theme != session.training_theme:
                session.training_theme = new_theme
                theme_changed = True

        if "enable_retry" in data:
            session.enable_retry = bool(data["enable_retry"])
            if not session.enable_retry and session.retry_mode:
                session.retry_mode = False
                session.allowed_choices = [m["action"] for m in session.current_evals]

        if theme_changed or data.get("redeal"):
            session.reset_game(clear_stats=False)

        re_eval_needed = False
        if "teacher_type" in data:
            new_teacher = data["teacher_type"]
            if new_teacher != session.teacher_type:
                session.teacher_type = new_teacher
                re_eval_needed = True

        if "search_preset" in data:
            new_preset = data["search_preset"]
            if new_preset != session.search_preset:
                session.search_preset = new_preset
                if session.teacher_type == "search":
                    re_eval_needed = True
        
        if "roman_preset" in data:
            new_r_preset = data["roman_preset"]
            if new_r_preset != session.roman_preset:
                session.roman_preset = new_r_preset
                if session.teacher_type == "roman":
                    re_eval_needed = True

        # api_settings 内に追加:
        if "speed_preset" in data:
            new_s_preset = data["speed_preset"]
            if new_s_preset != session.speed_preset:
                session.speed_preset = new_s_preset
                if session.teacher_type == "speed":
                    re_eval_needed = True

        if re_eval_needed and session.raw_mortal_evals and not (theme_changed or data.get("redeal")):
            session.current_evals = session._apply_teacher_ai(session.raw_mortal_evals)
            if session.retry_mode:
                acceptable, _ = session.get_acceptable_moves(session.current_evals)
                session.allowed_choices = [m["action"] for m in acceptable]
            else:
                session.allowed_choices = [m["action"] for m in session.current_evals]

        

        return jsonify(session.get_state_payload())


@app.route("/api/reset", methods=["POST"])
def api_reset():
    cancel_pending_shutdown()
    with _session_lock:
        session.reset_game(clear_stats=True)
        return jsonify(session.get_state_payload())


@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    cancel_pending_shutdown()
    return jsonify({"ok": True})


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    with _shutdown_lock:
        my_token = _shutdown_token

    def delayed_exit(token_snapshot: int):
        time.sleep(1.5)
        with _shutdown_lock:
            if _shutdown_token == token_snapshot:
                os._exit(0)

    threading.Thread(target=delayed_exit, args=(my_token,), daemon=True).start()
    return jsonify({"status": "shutdown_scheduled"})


def open_app_window(url: str = "http://127.0.0.1:5000"):
    candidate_browsers = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    no_window_flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for browser_path in candidate_browsers:
        if os.path.exists(browser_path):
            try:
                subprocess.Popen(
                    [browser_path, f"--app={url}", "--window-size=1680,980"],
                    creationflags=no_window_flag,
                )
                return
            except Exception:
                pass
    webbrowser.open(url)


if __name__ == "__main__":
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    Timer(0.8, lambda: open_app_window("http://127.0.0.1:5000")).start()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)