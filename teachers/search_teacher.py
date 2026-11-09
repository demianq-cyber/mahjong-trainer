# teachers/search_teacher.py
import os
import json
from collections import Counter
import numpy as np
import torch
from .base_teacher import BaseTeacherAI


import sys

# exe化（PyInstaller）時に内部バンドルされた search_params.json を優先参照する
if getattr(sys, "frozen", False):
    _BASE_TEACHER_DIR = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "teachers")
else:
    _BASE_TEACHER_DIR = os.path.dirname(os.path.abspath(__file__))

PARAMS_FILE_PATH = os.path.join(_BASE_TEACHER_DIR, "search_params.json")


DEFAULT_SEARCH_WEIGHTS = {
    "device": "cuda",
    "gpu_fast_mode": True,
    "guardrail_q_window": 0.85,     # Mortal 1位とのQ値差がこの範囲内の候補すべてをメタ戦略の逆転対象にする
    "override_strength": 1.15,      # メタ戦略＆GPU探索がMortalの順位を覆す全体強度（1.0以上で一致率75~88%に誘導）
    "w_gpu_ev": 1.10,               # GPUロールアウト（実山ツモ進行率・受け入れ枚数）の重み
    "w_trap_wait": 1.35,            # 【メタ①】対AIトラップ待ち（筋引っ掛け・端牌・字牌待ち）への加点
    "w_pre_defense": 1.45,          # 【メタ②】中盤の先切りスリム化（無筋3~7早期処理＆字牌/安牌温存）への加点
    "w_oya_control": 1.25,          # 【メタ③】親番連荘重視 ＆ 他家親番の早期蹴り（親流し仕掛け）への加点
    "w_wall_kabe": 1.10,            # 【メタ④】カベ（ノーチャンス・ワンチャンス）の山読み・安全度加点
    "w_flush_restrain": 1.20,       # 【メタ⑤】他家の染め手（ホンイツ）気配に対する中張牌絞り加点
    "w_keiten_push": 1.30,          # 【メタ⑥】終盤（残り山18枚以下）の形式聴牌（ケイテン）奪取加点
    "w_placement_cond": 1.50,       # 【メタ⑦】南場・オーラスの着順点差条件（リード死守・安手鳴き禁止・逆転条件）
}

SEARCH_PRESETS = {
    "fast": {
        "label": "⚡ 高速・7大メタ戦略 (4096並列)",
        "max_rollout_turns": 7,
        "gpu_batch_samples": 4096,
        "window_scale": 1.0,
    },
    "standard": {
        "label": "⚖️ 標準・7大メタ戦略 (6144並列)",
        "max_rollout_turns": 8,
        "gpu_batch_samples": 6144,
        "window_scale": 1.1,
    },
}


ALL_34_TILES = [
    "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
    "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
    "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
    "E", "S", "W", "N", "P", "F", "C"
]
TILE_TO_IDX = {t: i for i, t in enumerate(ALL_34_TILES)}
HONOR_TILES = {"E", "S", "W", "N", "P", "F", "C"}
TERMINAL_TILES = {"1m", "9m", "1p", "9p", "1s", "9s"}
YAOCHU_TILES = TERMINAL_TILES | HONOR_TILES
MIDDLE_TILES = {
    "3m", "4m", "5m", "6m", "7m",
    "3p", "4p", "5p", "6p", "7p",
    "3s", "4s", "5s", "6s", "7s",
}
TILES_SET = set(ALL_34_TILES) | {"5mr", "5pr", "5sr"}


def load_search_weights(override_params: dict = None) -> dict:
    params = dict(DEFAULT_SEARCH_WEIGHTS)
    if os.path.exists(PARAMS_FILE_PATH):
        try:
            with open(PARAMS_FILE_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                params.update(loaded)
        except Exception:
            pass
    if override_params and isinstance(override_params, dict):
        params.update(override_params)
    return params


def save_search_weights(params: dict):
    merged = load_search_weights(params)
    with open(PARAMS_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)


def norm_tile(tile: str) -> str:
    if not tile or not isinstance(tile, str):
        return ""
    return tile[:2] if tile in ("5mr", "5pr", "5sr") else tile


def get_actual_dora(marker: str) -> str:
    m = norm_tile(marker)
    if len(m) == 2 and m[1] in ("m", "p", "s"):
        num = int(m[0])
        return f"{1 if num == 9 else num + 1}{m[1]}"
    if m in ("E", "S", "W", "N"):
        idx = ["E", "S", "W", "N"].index(m)
        return ["E", "S", "W", "N"][(idx + 1) % 4]
    if m in ("P", "F", "C"):
        idx = ["P", "F", "C"].index(m)
        return ["P", "F", "C"][(idx + 1) % 3]
    return m


class SearchTeacherAI(BaseTeacherAI):
    teacher_id = "search"
    name = "タイプA：探索型 (7大メタ戦略＋GPU山読み)"
    description = "GPU山読みに加え、①対AIトラップ待ち、②中盤先切りスリム化、③親流し/連荘、④壁読み、⑤染め手絞り、⑥終盤ケイテン、⑦着順条件逆算の7大メタ戦略を駆使するコーチです。"

    def _build_unseen_and_visible(self, tracker) -> tuple[list[str], Counter]:
        vis = Counter()
        for pid in range(4):
            for d in tracker.discards[pid]:
                vis[norm_tile(d["pai"])] += 1
            for m in tracker.melds[pid]:
                mtype = m.get("type", "")
                tiles = m.get("tiles", [])
                if mtype in ("ポン", "チー", "明槓", "カン", "加槓"):
                    for t in tiles[1:]:
                        vis[norm_tile(t)] += 1
                else:
                    for t in tiles:
                        vis[norm_tile(t)] += 1
        for d in tracker.dora_markers:
            vis[norm_tile(d)] += 1
        h_pid = tracker.human_pid
        for t in tracker.hands[h_pid]:
            vis[norm_tile(t)] += 1
        if tracker.drawn_tiles[h_pid] is not None:
            vis[norm_tile(tracker.drawn_tiles[h_pid])] += 1

        pool = []
        for t in ALL_34_TILES:
            rem = max(0, 4 - vis[t])
            pool.extend([t] * rem)
        return pool, vis

    def _detect_opponent_flush_suits(self, tracker) -> set[str]:
        """【メタ⑤】他家（特に下家や副露者）の河と鳴きから、染め手（ホンイツ/チンイツ）警戒スートを検知する"""
        pid = tracker.human_pid
        flush_suits = set()
        for opp in range(4):
            if opp == pid:
                continue
            melds = tracker.melds[opp]
            discards = [norm_tile(d["pai"]) for d in tracker.discards[opp]]
            if len(discards) < 5 and not melds:
                continue

            # 副露が特定の数牌スートに集まっている場合
            if len(melds) >= 1:
                m_suits = [
                    norm_tile(t)[1] for m in melds for t in m.get("tiles", [])
                    if len(norm_tile(t)) == 2 and norm_tile(t)[1] in ("m", "p", "s")
                ]
                if m_suits and len(set(m_suits)) == 1:
                    s_cand = m_suits[0]
                    # 河にそのスートがほとんど切られていなければ濃厚
                    if sum(1 for d in discards if len(d) == 2 and d[1] == s_cand) <= 1:
                        flush_suits.add(s_cand)

            # 8巡目以降で、特定の数牌スートが河に1枚も切られておらず字牌が切られている場合
            if len(discards) >= 8:
                for s_cand in ("m", "p", "s"):
                    s_disc_cnt = sum(1 for d in discards if len(d) == 2 and d[1] == s_cand)
                    honor_disc_cnt = sum(1 for d in discards if d in HONOR_TILES)
                    if s_disc_cnt == 0 and honor_disc_cnt >= 2:
                        flush_suits.add(s_cand)

        return flush_suits

    def _find_kabe_protected_tiles(self, vis: Counter) -> set[str]:
        """【メタ④】場に3枚以上見えている数牌（カベ）によって外側で孤立・保護されている牌を抽出する"""
        kabe_tiles = set()
        for s in ("m", "p", "s"):
            if vis[f"2{s}"] >= 3:
                kabe_tiles.add(f"1{s}")
            if vis[f"3{s}"] >= 3:
                kabe_tiles.update([f"1{s}", f"2{s}"])
            if vis[f"4{s}"] >= 3 and vis[f"1{s}"] >= 2:
                kabe_tiles.update([f"2{s}", f"3{s}"])
            if vis[f"8{s}"] >= 3:
                kabe_tiles.add(f"9{s}")
            if vis[f"7{s}"] >= 3:
                kabe_tiles.update([f"8{s}", f"9{s}"])
            if vis[f"6{s}"] >= 3 and vis[f"9{s}"] >= 2:
                kabe_tiles.update([f"7{s}", f"8{s}"])
        return kabe_tiles

    def _analyze_full_meta_context(self, tracker, dora_set: set[str], vis: Counter) -> dict:
        """点棒・着順条件・親子関係・巡目・カベ・染め手気配を一括解析する"""
        pid = tracker.human_pid
        scores = list(tracker.scores) if tracker.scores else [25000] * 4
        my_score = scores[pid]

        ranked_pids = sorted(range(4), key=lambda p: (scores[p], -p), reverse=True)
        my_rank = ranked_pids.index(pid) + 1

        top_score = scores[ranked_pids[0]]
        second_score = scores[ranked_pids[1]]
        third_score = scores[ranked_pids[2]]
        last_score = scores[ranked_pids[3]]

        diff_to_above = (scores[ranked_pids[my_rank - 2]] - my_score) if my_rank > 1 else 0
        margin_above_below = (my_score - scores[ranked_pids[my_rank]]) if my_rank < 4 else 0
        margin_above_last = my_score - last_score

        is_oya = (pid == tracker.oya)
        oya_rank = ranked_pids.index(tracker.oya) + 1
        is_south = (tracker.bakaze == "S")
        turn_num = len(tracker.discards[pid]) + 1
        wall_rem = tracker.wall_remaining

        my_raw = list(tracker.hands[pid]) + ([tracker.drawn_tiles[pid]] if tracker.drawn_tiles[pid] else [])
        meld_raw = [t for m in tracker.melds[pid] for t in m.get("tiles", [])]
        all_my_tiles = my_raw + meld_raw
        my_dora_count = sum(1 for t in all_my_tiles if norm_tile(t) in dora_set) + sum(
            1 for t in all_my_tiles if t in ("5mr", "5pr", "5sr")
        )

        # 自分の河にある数牌から「自分の筋（引っ掛け対象）」を計算
        my_discards_norm = {norm_tile(d["pai"]) for d in tracker.discards[pid]}
        my_suji_trap_tiles = set()
        for d in my_discards_norm:
            if len(d) == 2 and d[1] in ("m", "p", "s"):
                n, s = int(d[0]), d[1]
                if n in (4, 5, 6):
                    my_suji_trap_tiles.update([f"{n-3}{s}", f"{n+3}{s}"])
                elif n in (1, 2, 3):
                    my_suji_trap_tiles.add(f"{n+3}{s}")
                elif n in (7, 8, 9):
                    my_suji_trap_tiles.add(f"{n-3}{s}")

        mode = "balanced"
        mode_desc = f"⚖️メタ標準({my_rank}位)"

        if my_rank == 1 and (my_score >= 28500 or (is_south and margin_above_below >= 5000)):
            if is_south and margin_above_below <= 3900:
                mode = "speed_digest"
                mode_desc = f"⚡微差トップ消化(2着差{margin_above_below:,}点)"
            else:
                mode = "defend_lead"
                mode_desc = f"🛡️トップ防衛({my_score:,}点)"
        elif my_rank in (2, 3):
            if is_south and margin_above_last <= 5500:
                mode = "avoid_last"
                mode_desc = f"🚨南場ラス回避({my_rank}位/ラス差{margin_above_last:,}点)"
            elif diff_to_above <= 3200:
                mode = "speed_digest"
                mode_desc = f"⚡着順UP圏内({my_rank}位/上差{diff_to_above:,}点)"
            elif my_rank == 2 and is_south and my_score >= 26000:
                mode = "defend_lead"
                mode_desc = f"🛡️南場2着キープ({my_score:,}点)"
        elif my_rank == 4:
            if diff_to_above <= 2600:
                mode = "speed_digest"
                mode_desc = f"⚡微差ラス抜け(3着差{diff_to_above:,}点)"
            else:
                mode = "comeback"
                mode_desc = f"🔥ラス逆転打点({my_score:,}点/3着差{diff_to_above:,}点)"

        return {
            "my_rank": my_rank,
            "my_score": my_score,
            "diff_to_above": diff_to_above,
            "margin_above_last": margin_above_last,
            "is_oya": is_oya,
            "oya_rank": oya_rank,
            "is_south": is_south,
            "turn_num": turn_num,
            "wall_rem": wall_rem,
            "my_dora_count": my_dora_count,
            "my_suji_trap_tiles": my_suji_trap_tiles,
            "flush_suits": self._detect_opponent_flush_suits(tracker),
            "kabe_tiles": self._find_kabe_protected_tiles(vis),
            "mode": mode,
            "mode_desc": mode_desc,
        }

    def _evaluate_candidates_with_meta_and_gpu(
        self,
        candidates: list[dict],
        tracker,
        tenpai_info: dict,
        danger_info: dict,
        unseen_pool: list[str],
        vis: Counter,
        dora_set: set[str],
        meta_ctx: dict,
        weights: dict,
        max_rollout_turns: int,
        batch_samples: int,
        device: torch.device,
    ) -> dict[str, dict]:
        """
        GPUバッチロールアウトと7大メタ戦略スコアを各候補手ごとに計算する
        """
        if unseen_pool and len(unseen_pool) >= 4:
            pool_indices = torch.tensor([TILE_TO_IDX[t] for t in unseen_pool], dtype=torch.long, device=device)
            pool_len = pool_indices.shape[0]
            draw_len = min(pool_len, max_rollout_turns)
            rand_noise = torch.rand((batch_samples, pool_len), device=device)
            perm = torch.argsort(rand_noise, dim=1)
            my_draws = pool_indices[perm[:, :draw_len]]
        else:
            my_draws = None
            draw_len = 0

        u_map = tenpai_info.get("discard_ukeire_map", {})
        d_map = danger_info.get("tiles", {}) if danger_info.get("active") else {}
        min_sh = tenpai_info.get("current_shanten", 1)
        has_threat = bool(danger_info.get("active", False))

        w_gpu_ev = float(weights.get("w_gpu_ev", 1.10))
        w_trap = float(weights.get("w_trap_wait", 1.35))
        w_pre_def = float(weights.get("w_pre_defense", 1.45))
        w_oya = float(weights.get("w_oya_control", 1.25))
        w_kabe = float(weights.get("w_wall_kabe", 1.10))
        w_flush = float(weights.get("w_flush_restrain", 1.20))
        w_keiten = float(weights.get("w_keiten_push", 1.30))
        w_cond = float(weights.get("w_placement_cond", 1.50))

        opp_genbutsu_union = tracker.genbutsu[1] | tracker.genbutsu[2] | tracker.genbutsu[3]
        suji_traps = meta_ctx["my_suji_trap_tiles"]
        flush_suits = meta_ctx["flush_suits"]
        kabe_tiles = meta_ctx["kabe_tiles"]
        turn_num = meta_ctx["turn_num"]
        wall_rem = meta_ctx["wall_rem"]
        mode = meta_ctx["mode"]

        results = {}
        for c in candidates:
            act = c["action"]
            u_entry = u_map.get(act) or u_map.get(norm_tile(act))
            d_entry = d_map.get(act)
            risk_sc = float(d_entry["risk_score"]) if d_entry else 0.0

            if u_entry and u_entry.get("waits"):
                waits_list = u_entry["waits"]
                wait_idxs = [TILE_TO_IDX[w["tile"]] for w in waits_list if w.get("remaining", 0) > 0 and w["tile"] in TILE_TO_IDX]
                rem_tiles = int(u_entry.get("total_remaining", 0))
                sh_after = int(u_entry.get("shanten", min_sh))
                is_furiten = bool(u_entry.get("is_furiten", False))
                is_back = bool(u_entry.get("is_shanten_back", False))
            else:
                waits_list = tenpai_info.get("base_waits", [])
                wait_idxs = [TILE_TO_IDX[w["tile"]] for w in waits_list if w.get("remaining", 0) > 0 and w["tile"] in TILE_TO_IDX]
                rem_tiles = int(tenpai_info.get("base_total_remaining", 0))
                sh_after = min_sh - (1 if act in ("pon", "chi_low", "chi_mid", "chi_high") else 0)
                is_furiten = bool(tenpai_info.get("base_is_furiten", False))
                is_back = False

            # --- GPU進行率計算 ---
            if my_draws is not None and wait_idxs:
                wait_mask = torch.zeros(34, dtype=torch.bool, device=device)
                wait_mask[wait_idxs] = True
                hits = wait_mask[my_draws]
                any_hit = hits.any(dim=1).float()
                first_hit_turn = torch.argmax(hits.int(), dim=1).float()
                speed_vec = torch.where(
                    any_hit > 0,
                    1.0 - (first_hit_turn / max(1.0, float(draw_len))) * 0.6,
                    torch.zeros_like(any_hit),
                )
                advance_rate = float(any_hit.mean().item()) * 100.0
                speed_score = float(speed_vec.mean().item()) * 100.0
            else:
                advance_rate = 0.0
                speed_score = 0.0

            if is_furiten:
                advance_rate *= 0.48
                speed_score *= 0.48
            if is_back and not has_threat:
                advance_rate *= 0.38
                speed_score *= 0.38

            meta_score = 0.0
            tags = []

            # ==================================================================
            # 🎯 メタ①：対AI「トラップ待ち（筋引っ掛け・字牌・端牌・カベ外待ち）」
            # ==================================================================
            if waits_list and sh_after <= 1 and not is_furiten:
                trap_rem = 0
                for w in waits_list:
                    wt = w["tile"]
                    w_rem = w.get("remaining", 0)
                    if wt in HONOR_TILES or wt in TERMINAL_TILES or wt in suji_traps or wt in kabe_tiles:
                        trap_rem += w_rem
                if trap_rem > 0:
                    trap_ratio = trap_rem / float(max(1, rem_tiles))
                    trap_bonus = (trap_ratio * 0.65 + min(1.0, trap_rem / 6.0) * 0.45) * w_trap
                    if sh_after == 0:
                        trap_bonus *= 1.45  # テンパイ時は出アガリ直結のため大幅強化
                    meta_score += trap_bonus
                    if trap_ratio >= 0.45:
                        tags.append(f"🎯対AIトラップ有効牌({trap_rem}枚)")

            # ==================================================================
            # 🛡️ メタ②：中盤「先切りスリム化（無筋3~7の早期処理＆字牌/現物ストック）」
            # ==================================================================
            if act in TILES_SET:
                act_n = norm_tile(act)
                is_act_dora = (act_n in dora_set) or (act in ("5mr", "5pr", "5sr"))
                if has_threat:
                    # リーチ・高副露発生中：安全度の高い牌を強く推す
                    safe_delta = ((45.0 - risk_sc) / 30.0) * w_pre_def * 1.35
                    meta_score += safe_delta
                    if risk_sc <= 15:
                        tags.append("🛡️現物/安牌切り")
                    elif risk_sc >= 65 and sh_after >= 1:
                        tags.append("🚨高危険牌減点")
                elif turn_num >= 5 and min_sh >= 1:
                    # 先制前の中盤（5巡目以降）：まだリーチが来ていなくても危険な3~7を先に切り、字牌・現物を温存する
                    if act_n in MIDDLE_TILES and not is_act_dora and not is_back:
                        meta_score += 0.62 * w_pre_def
                        tags.append("🛡️中張牌先切りスリム化")
                    elif act_n in HONOR_TILES and vis[act_n] >= 2:
                        # 場に1枚以上切れている安全度の高い字牌は手牌にストックしたいので切るのを抑制
                        meta_score -= 0.70 * w_pre_def
                        tags.append("🛑安牌字牌は温存推奨")
                    elif act_n in opp_genbutsu_union:
                        meta_score -= 0.38 * w_pre_def

            # ==================================================================
            # 👑 メタ③：親番（連荘）最大化 vs 他家親番の早期蹴り（親流し）
            # ==================================================================
            if meta_ctx["is_oya"]:
                # 自分が親番：リーチと前向きな仕掛け・最大受け入れを強化
                if act == "reach":
                    meta_score += 0.65 * w_oya
                    tags.append("👑親リーチ連荘圧迫")
                elif act in ("pon", "chi_low", "chi_mid", "chi_high") and min_sh <= 2:
                    meta_score += 0.45 * w_oya
                    tags.append("👑親番速度仕掛け")
            else:
                # 自分が子で、親が1位または2位の強敵のとき：軽い仕掛けで親を流す
                if meta_ctx["oya_rank"] <= 2 and act in ("pon", "chi_low", "chi_mid", "chi_high") and min_sh <= 2:
                    meta_score += 0.68 * w_oya
                    tags.append("⚡上位親番流し仕掛け")
                elif act == "none" and meta_ctx["oya_rank"] <= 2 and min_sh <= 2:
                    meta_score -= 0.35 * w_oya

            # ==================================================================
            # 🧱 メタ④：カベ（ノーチャンス・ワンチャンス）活用
            # ==================================================================
            if act in TILES_SET:
                act_n = norm_tile(act)
                # カベで守られた牌は後からでも安全に切れる＆縦重なりしやすいため、先に切らず温存優遇
                if act_n in kabe_tiles and not has_threat:
                    meta_score -= 0.25 * w_kabe
                elif has_threat and act_n in kabe_tiles:
                    meta_score += 0.40 * w_kabe
                    tags.append("🧱カベ(ノーチャンス)安牌")

            # ==================================================================
            # 🕵️ メタ⑤：他家の染め手（ホンイツ/チンイツ）スート絞り
            # ==================================================================
            if flush_suits and act in TILES_SET and min_sh >= 1:
                act_n = norm_tile(act)
                if len(act_n) == 2 and act_n[1] in flush_suits:
                    meta_score -= 0.75 * w_flush
                    tags.append(f"🕵️他家染め手({act_n[1]})絞り")

            # ==================================================================
            # ⏱️ メタ⑥：終盤（残り山18枚以下）の形式聴牌（ケイテン）奪取
            # ==================================================================
            if wall_rem <= 18:
                if sh_after <= 0 and not is_back:
                    meta_score += 0.85 * w_keiten
                    tags.append("⏱️終盤ケイテン維持/奪取")
                elif act in ("pon", "chi_low", "chi_mid", "chi_high") and min_sh <= 1:
                    meta_score += 0.80 * w_keiten
                    tags.append("⏱️終盤ケイテン鳴き")
                elif is_back and not has_threat:
                    meta_score -= 0.90 * w_keiten

            # ==================================================================
            # 📊 メタ⑦：南場・点棒状況の厳密な着順条件戦術
            # ==================================================================
            if mode == "defend_lead":
                # リード時：無謀な愚形/安手リーチを抑えてダマテン・安牌抱えに誘導
                if act == "reach" and (meta_ctx["my_dora_count"] == 0 or rem_tiles <= 5):
                    meta_score -= 0.85 * w_cond
                    tags.append("🛡️リード時ダマテン推奨")
                elif act in TILES_SET and sh_after == 0 and not tracker.riichi[tracker.human_pid]:
                    meta_score += 0.45 * w_cond
                    tags.append("🛡️リード時ダマ聴牌")
            elif mode == "avoid_last":
                if has_threat and risk_sc >= 40 and sh_after >= 1:
                    meta_score -= 1.10 * w_cond
                    tags.append("🚨ラス回避・危険牌禁止")
            elif mode == "comeback":
                # ラス目・大差ビハインド：ドラ0〜1の安い仕掛け（1000点・2000点）を禁止し、門前リーチ・ドラ死守
                if act in ("pon", "chi_low", "chi_mid", "chi_high") and meta_ctx["my_dora_count"] <= 1:
                    meta_score -= 0.95 * w_cond
                    tags.append("🛑ラス目・安手仕掛け禁止")
                elif act == "none" and meta_ctx["my_dora_count"] <= 1:
                    meta_score += 0.65 * w_cond
                    tags.append("🔥ラス目・門前打点維持")
                elif act == "reach":
                    meta_score += 0.65 * w_cond
                    tags.append("🔥ラス抜け逆転リーチ")
                elif act in TILES_SET and ((norm_tile(act) in dora_set) or (act in ("5mr", "5pr", "5sr"))):
                    meta_score -= 0.90 * w_cond
                    tags.append("🔥逆転用ドラ保持")
            elif mode == "speed_digest":
                if act in ("pon", "chi_low", "chi_mid", "chi_high") and min_sh <= 2:
                    meta_score += 0.75 * w_cond
                    tags.append("⚡条件クリア速攻鳴き")

            results[act] = {
                "advance_rate": advance_rate,
                "speed_score": speed_score,
                "rem_tiles": rem_tiles,
                "sh_after": sh_after,
                "is_back": is_back,
                "risk_score": risk_sc,
                "meta_score": meta_score,
                "w_gpu_ev": w_gpu_ev,
                "tags": tags,
            }
        return results

    def evaluate(
        self,
        base_evals: list[dict],
        tracker,
        obs,
        tenpai_info: dict,
        danger_info: dict,
        potential_yakus: list[dict],
        score_estimate: dict = None,
        preset_key: str = "standard",
        custom_params: dict = None,
    ) -> list[dict]:
        if not base_evals:
            return []

        items = self.snapshot_mortal_baseline(base_evals)
        if len(items) == 1:
            items[0]["teacher_note"] = "🔍 [7大メタ探索]: 唯一の実合法手です"
            return items

        weights = load_search_weights(custom_params)
        preset = SEARCH_PRESETS.get(preset_key, SEARCH_PRESETS["standard"])

        q_window = float(weights.get("guardrail_q_window", 0.85)) * float(preset.get("window_scale", 1.0))
        override_str = float(weights.get("override_strength", 1.15))

        dev_pref = str(weights.get("device", "auto")).lower()
        use_cuda = (dev_pref in ("auto", "cuda", "gpu")) and torch.cuda.is_available()
        device = torch.device("cuda" if use_cuda else "cpu")

        unseen_pool, vis = self._build_unseen_and_visible(tracker)
        dora_set = {get_actual_dora(m) for m in tracker.dora_markers}
        meta_ctx = self._analyze_full_meta_context(tracker, dora_set, vis)

        best_mortal_q = items[0]["mortal_q"]

        # 上位5候補（またはMortalスコア35点以上・Q値差 q_window 以内）をすべてメタ戦略の比較土俵に乗せる
        viable_candidates = [
            x for x in items[:6]
            if (best_mortal_q - x["mortal_q"]) <= q_window
            or x.get("mortal_score", 0) >= 35.0
            or x["action"] in ("none", "reach", "hora", "pon", "chi_low", "chi_mid", "chi_high")
        ]
        if len(viable_candidates) < 2 and len(items) >= 2:
            viable_candidates = items[:min(3, len(items))]

        eval_map = self._evaluate_candidates_with_meta_and_gpu(
            candidates=viable_candidates,
            tracker=tracker,
            tenpai_info=tenpai_info,
            danger_info=danger_info,
            unseen_pool=unseen_pool,
            vis=vis,
            dora_set=dora_set,
            meta_ctx=meta_ctx,
            weights=weights,
            max_rollout_turns=preset["max_rollout_turns"],
            batch_samples=preset["gpu_batch_samples"],
            device=device,
        )

        # ★重要：Mortalの候補間の実際のQ値スパン（1位と3位の差）を計測し、
        # メタ戦略スコアが確実にそのQ値差を逆転できるスケールに動的正規化する！
        cand_qs = [x["mortal_q"] for x in viable_candidates]
        q_span = max(0.35, min(2.2, float(max(cand_qs) - min(cand_qs))))

        rem_vals = [v["rem_tiles"] for v in eval_map.values()]
        spd_vals = [v["speed_score"] for v in eval_map.values()]
        meta_vals = [v["meta_score"] for v in eval_map.values()]

        mean_rem, std_rem = float(np.mean(rem_vals)), max(1.5, float(np.std(rem_vals)))
        mean_spd, std_spd = float(np.mean(spd_vals)), max(4.0, float(np.std(spd_vals)))
        mean_meta, std_meta = float(np.mean(meta_vals)), max(0.35, float(np.std(meta_vals)))

        has_threat = bool(danger_info.get("active", False))

        for item in items:
            act = item["action"]
            m_q = item["mortal_q"]
            q_gap = best_mortal_q - m_q

            if act == "hora":
                item["q_value"] = m_q + 15.0
                item["teacher_note"] = "🎉 確定: 和了（アガリ）が最善です"
                continue

            if act not in eval_map:
                item["teacher_note"] = f"（悪手ガードレール外: Mortal Q差 -{q_gap:.2f}）"
                continue

            st = eval_map[act]
            z_rem = (st["rem_tiles"] - mean_rem) / std_rem
            z_spd = (st["speed_score"] - mean_spd) / std_spd
            z_meta = (st["meta_score"] - mean_meta) / std_meta

            # 平時の無意味なシャンテン戻しだけガード
            back_pen = -0.90 if (st["is_back"] and not has_threat) else 0.0

            # 総合メタ＆GPUスコア（Zスコア合成）
            combined_z = (
                0.30 * st["w_gpu_ev"] * (0.5 * z_rem + 0.5 * z_spd)
                + 0.70 * z_meta
                + back_pen
            )

            # q_span（候補間のMortal Q値差）を掛けてスケーリングすることで、
            # メタ戦略で優位な2位・3位候補が確実に1位のQ値を追い抜けるようにする
            total_delta = combined_z * (q_span * 0.55) * override_str

            item["q_value"] = m_q + total_delta
            item["teacher_bonus"] = round(total_delta, 4)

            tags_str = " / ".join(st["tags"][:2]) if st["tags"] else f"実山残{st['rem_tiles']}枚・進行{st['advance_rate']:.0f}%"
            item["teacher_note"] = (
                f"🔍[{meta_ctx['mode_desc']}] {tags_str} (補正:{total_delta:+.2f})"
            )

        sorted_items = self.normalize_and_sort(items, temperature=1.2)
        if sorted_items and sorted_items[0].get("mortal_rank", 1) > 1:
            top = sorted_items[0]
            top["teacher_note"] = (
                f"💡【メタ逆転！(Mortal {top['mortal_rank']}位➡1位)】{top.get('teacher_note', '')}"
            )
        return sorted_items