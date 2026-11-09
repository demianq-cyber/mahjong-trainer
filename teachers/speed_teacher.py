# teachers/speed_teacher.py
from collections import Counter
import numpy as np
from .base_teacher import BaseTeacherAI

SPEED_PRESETS = {
    "efficiency": {
        "label": "📐 純粋牌効率 (最大枚数・門前速攻)",
        "ukeire_weight": 1.65,
        "back_penalty": 1.80,
        "call_boost": 0.25,
        "tanyao_rush": 0.70,
        "riichi_rush": 0.85,
        "safety_care": 0.75,
    },
    "rapid": {
        "label": "⚡ 高速仕掛け (喰いタン・役牌速攻)",
        "ukeire_weight": 1.55,
        "back_penalty": 1.65,
        "call_boost": 1.15,
        "tanyao_rush": 1.10,
        "riichi_rush": 0.75,
        "safety_care": 0.55,
    },
    "sonic": {
        "label": "🌪️ 超音速・局回し (鳴き全開・最速和了)",
        "ukeire_weight": 1.50,
        "back_penalty": 2.00,
        "call_boost": 1.85,
        "tanyao_rush": 1.45,
        "riichi_rush": 0.90,
        "safety_care": 0.30,
    },
}

HONOR_TILES = {"E", "S", "W", "N", "P", "F", "C"}
TERMINAL_TILES = {"1m", "9m", "1p", "9p", "1s", "9s"}
YAOCHU_TILES = TERMINAL_TILES | HONOR_TILES
MIDDLE_TILES = {
    "2m", "3m", "4m", "5m", "6m", "7m", "8m",
    "2p", "3p", "4p", "5p", "6p", "7p", "8p",
    "2s", "3s", "4s", "5s", "6s", "7s", "8s",
}
WIND_CYCLE = ["E", "S", "W", "N"]


def norm_tile(tile: str) -> str:
    if not tile or not isinstance(tile, str):
        return ""
    return tile[:2] if tile in ("5mr", "5pr", "5sr") else tile


class SpeedTeacherAI(BaseTeacherAI):
    teacher_id = "speed"
    name = "タイプC：スピード型 (即効・牌効率特化)"
    description = "打点や複雑な駆け引きよりも「受け入れ枚数の最大化」「シャンテン戻し禁止」「喰いタン・役牌の高速仕掛け」を最優先し、誰よりも早くアガり切る手順を指導するコーチです。"

    def _analyze_speed_structure(self, tracker) -> dict:
        """現在の手牌が「喰いタン」「役牌速攻」「門前好形」のどれに向いているかを高速判定する"""
        pid = tracker.human_pid
        hand_raw = list(tracker.hands[pid]) + ([tracker.drawn_tiles[pid]] if tracker.drawn_tiles[pid] else [])
        melds = tracker.melds[pid]
        meld_tiles = [t for m in melds for t in m.get("tiles", [])]

        all_norm = [norm_tile(t) for t in (hand_raw + meld_tiles)]
        hand_norm = [norm_tile(t) for t in hand_raw]
        counts_all = Counter(all_norm)
        counts_hand = Counter(hand_norm)

        jikaze = WIND_CYCLE[(pid - tracker.oya) % 4]
        bakaze = tracker.bakaze
        yakuhai_set = {"P", "F", "C", bakaze, jikaze}
        otakaze_set = {"E", "S", "W", "N"} - {bakaze, jikaze}

        has_yakuhai_triplet = any(counts_all[y] >= 3 for y in yakuhai_set)
        has_yakuhai_pair = any(counts_hand[y] >= 2 for y in yakuhai_set)

        meld_has_yaochu = any(norm_tile(t) in YAOCHU_TILES for t in meld_tiles)
        tanyao_count = sum(1 for t in all_norm if t in MIDDLE_TILES)
        can_kuitan = (not meld_has_yaochu) and (tanyao_count >= 9)

        return {
            "yakuhai_set": yakuhai_set,
            "otakaze_set": otakaze_set,
            "has_yakuhai_triplet": has_yakuhai_triplet,
            "has_yakuhai_pair": has_yakuhai_pair,
            "can_kuitan": can_kuitan,
            "tanyao_count": tanyao_count,
            "counts_hand": counts_hand,
            "num_melds": len(melds),
        }

    def evaluate(
        self,
        base_evals: list[dict],
        tracker,
        obs,
        tenpai_info: dict,
        danger_info: dict,
        potential_yakus: list[dict],
        score_estimate: dict = None,
        preset_key: str = "rapid",
        custom_params: dict = None,
    ) -> list[dict]:
        if not base_evals:
            return []

        items = self.snapshot_mortal_baseline(base_evals)
        if len(items) == 1:
            items[0]["teacher_note"] = "⚡ [スピード型AI]: 唯一の実合法手です"
            return items

        preset = SPEED_PRESETS.get(preset_key, SPEED_PRESETS["rapid"])
        w_ukeire = float(preset["ukeire_weight"])
        w_back_pen = float(preset["back_penalty"])
        w_call = float(preset["call_boost"])
        w_tanyao = float(preset["tanyao_rush"])
        w_riichi = float(preset["riichi_rush"])
        w_safety = float(preset["safety_care"])

        sp_ctx = self._analyze_speed_structure(tracker)
        u_map = tenpai_info.get("discard_ukeire_map", {})
        d_map = danger_info.get("tiles", {}) if danger_info.get("active") else {}
        has_riichi_threat = bool(danger_info.get("has_riichi", False))
        min_sh = tenpai_info.get("current_shanten", 1)

        # 最大受け入れ枚数と種数を取得
        non_back_entries = [v for v in u_map.values() if not v.get("is_shanten_back")]
        max_rem = max((v.get("total_remaining", 0) for v in non_back_entries), default=1)
        max_types = max((v.get("ukeire_types", 0) for v in non_back_entries), default=1)

        # 候補手間のMortal Q値スパン（上位5手）を基準に補正スケールを調整
        top_qs = [x["mortal_q"] for x in items[:min(5, len(items))]]
        q_span = max(0.45, min(2.4, float(max(top_qs) - min(top_qs))))

        last_d = tracker.last_discard
        called_pai_norm = norm_tile(last_d["pai"]) if last_d else ""

        for item in items:
            act = item["action"]
            m_q = item["mortal_q"]

            if act == "hora":
                item["q_value"] = m_q + 20.0
                item["teacher_note"] = "🎉 [最速和了確定]: アガリが最優先です！"
                continue

            u_entry = u_map.get(act) or u_map.get(norm_tile(act))
            d_entry = d_map.get(act)
            risk_sc = float(d_entry["risk_score"]) if d_entry else 0.0

            speed_delta = 0.0
            notes = []

            # ==================================================================
            # 1. 打牌フェーズ：受け入れ枚数最大化 ＆ シャンテン戻し厳禁 ＆ 端牌整理
            # ==================================================================
            if u_entry is not None:
                rem = int(u_entry.get("total_remaining", 0))
                types = int(u_entry.get("ukeire_types", 0))
                is_back = bool(u_entry.get("is_shanten_back", False))
                is_tenpai = bool(u_entry.get("is_tenpai", False))
                is_furiten = bool(u_entry.get("is_furiten", False))

                if is_back:
                    # シャンテン戻しはスピードの天敵として大幅減点
                    speed_delta -= 1.15 * w_back_pen
                    notes.append("🛑シャンテン戻し却下(速度低下)")
                else:
                    rem_ratio = rem / float(max(1, max_rem))
                    type_ratio = types / float(max(1, max_types))
                    eff_score = (0.75 * rem_ratio + 0.25 * type_ratio - 0.50) * 1.40 * w_ukeire
                    speed_delta += eff_score

                    if rem == max_rem and max_rem > 0:
                        notes.append(f"⚡最大受け入れ({types}種{rem}枚)")
                    elif rem >= max_rem - 2 and max_rem >= 8:
                        notes.append(f"📐準最大受け入れ({types}種{rem}枚)")

                    if is_tenpai:
                        if is_furiten:
                            speed_delta -= 0.45
                            notes.append("⚠️フリテン聴牌(出アガリ不可)")
                        else:
                            speed_delta += 0.55
                            notes.append("🔥最速テンパイ到達")

                # 端牌・オタ風の早期処理と喰いタン/役牌パーツの保護
                act_n = norm_tile(act)
                c_hand = sp_ctx["counts_hand"]
                if act_n in sp_ctx["otakaze_set"] and c_hand[act_n] == 1:
                    speed_delta += 0.35 * w_tanyao
                    notes.append("⚡孤立オタ風処理")
                elif act_n in sp_ctx["yakuhai_set"] and c_hand[act_n] == 2:
                    # 速攻の要である役牌対子落としは強く減点
                    speed_delta -= 0.95 * w_tanyao
                    notes.append("🛑役牌対子は鳴き用に死守")
                elif sp_ctx["can_kuitan"] and act_n in YAOCHU_TILES and c_hand[act_n] == 1 and act_n not in sp_ctx["yakuhai_set"]:
                    speed_delta += 0.42 * w_tanyao
                    notes.append("⚡タンヤオ移行・端牌処理")
                elif sp_ctx["can_kuitan"] and act_n in MIDDLE_TILES and not is_back:
                    # 端牌が残っているのに中張牌を切る手を微減点
                    speed_delta -= 0.12 * w_tanyao

            # ==================================================================
            # 2. 副露（ポン・チー）・リーチ判断フェーズ
            # ==================================================================
            elif act in ("pon", "chi_low", "chi_mid", "chi_high"):
                # 役牌ポン、または喰いタン・役牌確定時の仕掛けを強く推進
                is_yakuhai_pon = (act == "pon" and called_pai_norm in sp_ctx["yakuhai_set"])
                is_tanyao_call = (called_pai_norm in MIDDLE_TILES and sp_ctx["can_kuitan"])
                has_fast_yaku = is_yakuhai_pon or sp_ctx["has_yakuhai_triplet"] or sp_ctx["has_yakuhai_pair"] or is_tanyao_call

                if is_yakuhai_pon:
                    speed_delta += 1.05 * w_call
                    notes.append("🚀役牌ポン即発進(1飜確定)")
                elif has_fast_yaku and min_sh <= 2:
                    speed_delta += 0.78 * w_call
                    notes.append("⚡スピード仕掛け(シャンテン短縮)")
                elif preset_key == "sonic" and min_sh <= 2:
                    speed_delta += 0.65 * w_call
                    notes.append("🌪️超音速仕掛け(形テン/後付け)")
                else:
                    speed_delta += 0.20 * w_call
                    notes.append("⚡前進仕掛け")

            elif act == "none":
                # スルー判断：鳴き推進度に応じて減点（efficiencyモードでは門前維持を許容）
                if sp_ctx["has_yakuhai_triplet"] or sp_ctx["can_kuitan"] or (called_pai_norm in sp_ctx["yakuhai_set"]):
                    speed_delta -= 0.55 * w_call
                    notes.append("（スピード重視のため鳴き推奨）")

            elif act == "reach":
                base_rem = int(tenpai_info.get("base_total_remaining", 0))
                if base_rem >= 4 or not (sp_ctx["has_yakuhai_triplet"] or sp_ctx["can_kuitan"]):
                    speed_delta += 0.55 * w_riichi
                    notes.append(f"🔥先制即リーチ(残り{base_rem}枚)")
                else:
                    # 役あり＆愚形の場合はダマでポロッと出アガリするスピードも評価
                    speed_delta += 0.15 * w_riichi
                    notes.append("⚡リーチまたは迅速ダマ")

            # ==================================================================
            # 3. 最低限の緊急守備ブレーキ（他家リーチかつ自分が2シャンテン以上で無筋ド危険牌のときのみ）
            # ==================================================================
            if has_riichi_threat and min_sh >= 2 and risk_sc >= 70:
                safe_pen = -((risk_sc - 50.0) / 40.0) * w_safety
                speed_delta += safe_pen
                notes.append("⚠️リーチに対し手が遠いため危険牌注意")

            scaled_delta = speed_delta * (q_span * 0.55)
            item["q_value"] = m_q + scaled_delta
            item["teacher_bonus"] = round(scaled_delta, 4)

            note_str = " / ".join(notes[:2]) if notes else "スピード基準評価"
            item["teacher_note"] = f"⚡[{preset['label'][:7]}] {note_str} (補正:{scaled_delta:+.2f})"

        sorted_items = self.normalize_and_sort(items, temperature=1.2)
        if sorted_items and sorted_items[0].get("mortal_rank", 1) > 1:
            top = sorted_items[0]
            top["teacher_note"] = (
                f"💡【最速手順！(Mortal {top['mortal_rank']}位➡1位)】{top.get('teacher_note', '')}"
            )
        return sorted_items