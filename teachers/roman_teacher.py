# teachers/roman_teacher.py
from collections import Counter
from .base_teacher import BaseTeacherAI

# ==============================================================================
# Q1: ロマン度プリセット定数（UIのセレクトボックスおよびここからいつでも調整可能）
# ==============================================================================
ROMAN_PRESETS = {
    "practical": {
        "label": "🔥 実戦ロマン (高打点・ドラ重視)",
        "bonus_scale": 0.90,          # ロマン加点の全体倍率
        "allow_shanten_back": 1,      # 許容するシャンテン戻し幅
        "back_penalty_relief": 0.55,  # シャンテン戻し時のMortal減点の緩和率
        "dora_keep_weight": 1.15,     # ドラ・赤ドラ保持の重み
        "push_threshold": 75,         # 他家リーチに対して全ツッパするロマン進行度(%)
    },
    "dream": {
        "label": "💎 夢追いロマン (大物手・役満狙い)",
        "bonus_scale": 1.45,
        "allow_shanten_back": 1,
        "back_penalty_relief": 0.85,
        "dora_keep_weight": 1.45,
        "push_threshold": 65,
    },
    "madness": {
        "label": "👑 狂気ロマン (役満・倍満至上主義)",
        "bonus_scale": 2.30,
        "allow_shanten_back": 2,
        "back_penalty_relief": 1.10,
        "dora_keep_weight": 1.80,
        "push_threshold": 50,
    },
}

ALL_34_TILES = [
    "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
    "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
    "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
    "E", "S", "W", "N", "P", "F", "C"
]
HONOR_TILES = {"E", "S", "W", "N", "P", "F", "C"}
DRAGONS = {"P", "F", "C"}
WINDS = {"E", "S", "W", "N"}
TERMINALS = {"1m", "9m", "1p", "9p", "1s", "9s"}
YAOCHU_TILES = TERMINALS | HONOR_TILES
GREEN_TILES = {"2s", "3s", "4s", "6s", "8s", "F"}
TILES_SET = set(ALL_34_TILES) | {"5mr", "5pr", "5sr"}
SUIT_NAMES = {"m": "萬子", "p": "筒子", "s": "索子"}


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


class RomanTeacherAI(BaseTeacherAI):
    teacher_id = "roman"
    name = "タイプB：ロマン型 (役満・高打点特化)"
    description = "Mortalの評価をベースにしつつ、役満や清一色・混一色・三色・一気通貫・ドラ多用などの高打点ルートに強い加点補正を行い、夢のある手作りをガイドするコーチです。"

    def _evaluate_hand_romance(
        self,
        closed_raw: list[str],
        melds: list[dict],
        dora_set: set[str],
    ) -> dict:
        """
        与えられた手牌（打牌後の13枚＋副露）の「ロマン役スコア」「最有力ロマン役名」「進行度(%)」を算出する
        """
        is_menzen = all(m.get("type") == "暗槓" for m in melds)
        meld_tiles_raw = [t for m in melds for t in m.get("tiles", [])]
        all_raw = list(closed_raw) + meld_tiles_raw

        closed_norm = [norm_tile(t) for t in closed_raw]
        all_norm = [norm_tile(t) for t in all_raw]
        total_len = max(13, len(all_norm))

        c_closed = Counter(closed_norm)
        c_all = Counter(all_norm)

        # ドラ・赤ドラ枚数
        dora_cnt = sum(1 for t in all_raw if norm_tile(t) in dora_set)
        red_cnt = sum(1 for t in all_raw if t in ("5mr", "5pr", "5sr"))
        total_dora = dora_cnt + red_cnt

        yakus_found = []  # (bonus_points, progress_pct, tier_label, yaku_name_desc)

        # ======================================================================
        # Tier S: 役満・準役満クラス
        # ======================================================================
        # 1. 国士無双（門前かつ幺九牌8種以上）
        if is_menzen:
            yc_types = sum(1 for t in YAOCHU_TILES if c_closed[t] > 0)
            yc_total = sum(1 for t in closed_norm if t in YAOCHU_TILES)
            if yc_types >= 8:
                prog = min(99, int((yc_types / 13.0) * 100))
                b_pt = (yc_types - 7) * 0.42 + (yc_total * 0.05)
                yakus_found.append((b_pt, prog, "TierS", f"👑国士無双({yc_types}/13種)"))

        # 2. 四暗刻・三暗刻（対子・暗刻の数）
        closed_ankou = sum(1 for v in c_closed.values() if v >= 3) + sum(
            1 for m in melds if m.get("type") == "暗槓"
        )
        closed_pairs = sum(1 for v in c_closed.values() if v == 2)
        if is_menzen and (closed_ankou + closed_pairs >= 4) and (closed_ankou >= 1 or closed_pairs >= 4):
            prog = min(98, int(((closed_ankou * 2 + closed_pairs) / 8.0) * 100))
            b_pt = closed_ankou * 0.55 + closed_pairs * 0.22
            label = "👑四暗刻" if (closed_ankou >= 2 or closed_ankou + closed_pairs >= 5) else "💎三暗刻/四暗刻"
            yakus_found.append((b_pt, prog, "TierS", f"{label}(暗刻{closed_ankou}・対子{closed_pairs})"))
        elif not is_menzen and (closed_ankou >= 2 and closed_ankou + closed_pairs >= 3):
            prog = min(92, int(((closed_ankou * 2 + closed_pairs) / 6.0) * 100))
            b_pt = closed_ankou * 0.40 + closed_pairs * 0.15
            yakus_found.append((b_pt, prog, "TierA", f"💎三暗刻(暗刻{closed_ankou}・対子{closed_pairs})"))

        # 3. 大三元・小三元
        dragon_trips = sum(1 for d in DRAGONS if c_all[d] >= 3)
        dragon_pairs = sum(1 for d in DRAGONS if c_all[d] == 2)
        dragon_total = sum(c_all[d] for d in DRAGONS)
        if dragon_trips + dragon_pairs >= 2 and dragon_total >= 5:
            prog = min(99, int((dragon_total / 9.0) * 100))
            b_pt = dragon_trips * 0.75 + dragon_pairs * 0.40 + (dragon_total * 0.06)
            d_label = "👑大三元" if (dragon_trips >= 2 or dragon_total >= 7) else "💎小三元/大三元"
            yakus_found.append((b_pt, prog, "TierS", f"{d_label}(三元牌{dragon_total}枚)"))

        # 4. 小四喜・大四喜
        wind_trips = sum(1 for w in WINDS if c_all[w] >= 3)
        wind_pairs = sum(1 for w in WINDS if c_all[w] == 2)
        wind_total = sum(c_all[w] for w in WINDS)
        if wind_trips + wind_pairs >= 3 and wind_total >= 7:
            prog = min(99, int((wind_total / 11.0) * 100))
            b_pt = wind_trips * 0.70 + wind_pairs * 0.35
            yakus_found.append((b_pt, prog, "TierS", f"👑四喜和(風牌{wind_total}枚)"))

        # 5. 字一色・混老頭・清老頭
        yaochu_cnt = sum(1 for t in all_norm if t in YAOCHU_TILES)
        honor_cnt = sum(1 for t in all_norm if t in HONOR_TILES)
        if honor_cnt >= 8:
            prog = min(99, int((honor_cnt / total_len) * 100))
            b_pt = (honor_cnt - 7) * 0.50
            yakus_found.append((b_pt, prog, "TierS", f"👑字一色(字牌{honor_cnt}枚)"))
        elif yaochu_cnt >= 10 and (closed_ankou + closed_pairs + len(melds) >= 4):
            prog = min(98, int((yaochu_cnt / total_len) * 100))
            b_pt = (yaochu_cnt - 9) * 0.38
            yakus_found.append((b_pt, prog, "TierS", f"💎混老頭/清老頭(幺九牌{yaochu_cnt}枚)"))

        # 6. 緑一色
        green_cnt = sum(1 for t in all_norm if t in GREEN_TILES)
        if green_cnt >= 9:
            prog = min(99, int((green_cnt / total_len) * 100))
            b_pt = (green_cnt - 8) * 0.50
            yakus_found.append((b_pt, prog, "TierS", f"👑緑一色(緑牌{green_cnt}枚)"))

        # ======================================================================
        # Tier A: 清一色・混一色・九蓮宝燈・二盃口・純全帯幺九
        # ======================================================================
        for s_code, s_label in SUIT_NAMES.items():
            other_meld = any(
                len(norm_tile(t)) == 2 and norm_tile(t)[1] != s_code
                for t in meld_tiles_raw
            )
            if other_meld:
                continue
            suit_cnt = sum(1 for t in all_norm if len(t) == 2 and t[1] == s_code)
            honitsu_cnt = suit_cnt + honor_cnt

            # 九蓮宝燈チェック（門前かつ一色11枚以上＋1・9が各2枚以上）
            if is_menzen and suit_cnt >= 11 and c_closed[f"1{s_code}"] >= 2 and c_closed[f"9{s_code}"] >= 2:
                uniq_nums = sum(1 for n in range(1, 10) if c_closed[f"{n}{s_code}"] > 0)
                if uniq_nums >= 7:
                    prog = min(99, int((suit_cnt / 13.0) * 100))
                    b_pt = (suit_cnt - 9) * 0.55
                    yakus_found.append((b_pt, prog, "TierS", f"👑九蓮宝燈/門前清一色({s_label}{suit_cnt}枚)"))
                    continue

            if suit_cnt >= 9:
                prog = min(98, int((suit_cnt / total_len) * 100))
                b_pt = (suit_cnt - 8) * (0.42 if is_menzen else 0.34)
                yakus_found.append((b_pt, prog, "TierA", f"💎清一色({s_label}{suit_cnt}枚)"))
            elif honitsu_cnt >= 9 and suit_cnt >= 6:
                prog = min(96, int((honitsu_cnt / total_len) * 100))
                b_pt = (honitsu_cnt - 8) * (0.28 if is_menzen else 0.22) + (suit_cnt * 0.03)
                yakus_found.append((b_pt, prog, "TierA", f"💎混一色({s_label}+{honor_cnt}字=計{honitsu_cnt}枚)"))

        # 七対子・二盃口（門前対子手）
        if is_menzen and closed_pairs + closed_ankou >= 4:
            pair_like = closed_pairs + closed_ankou
            prog = min(95, int((pair_like / 6.0) * 100))
            b_pt = (pair_like - 3) * 0.22 + (total_dora * 0.08)
            yakus_found.append((b_pt, prog, "TierA", f"💎七対子/二盃口({pair_like}対子)"))

        # ======================================================================
        # Tier B: 三色同順・一気通貫・対々和・チャンタ/ジュンチャン
        # ======================================================================
        # 三色同順
        best_sanshoku = (0, "")
        for start_n in range(1, 8):
            seq_nums = (start_n, start_n + 1, start_n + 2)
            suits_hit = 0
            tiles_hit = 0
            for s_code in ("m", "p", "s"):
                m_cnt = sum(1 for n in seq_nums if c_all[f"{n}{s_code}"] > 0)
                if m_cnt > 0:
                    suits_hit += 1
                    tiles_hit += m_cnt
            if suits_hit == 3 and tiles_hit >= 6 and tiles_hit > best_sanshoku[0]:
                best_sanshoku = (tiles_hit, f"{start_n}{start_n+1}{start_n+2}")

        if best_sanshoku[0] >= 6:
            cnt, seq_str = best_sanshoku
            prog = min(96, int((cnt / 9.0) * 100))
            b_pt = (cnt - 5) * (0.24 if is_menzen else 0.16)
            yakus_found.append((b_pt, prog, "TierB", f"🌟三色同順[{seq_str}]({cnt}/9枚)"))

        # 一気通貫
        for s_code, s_label in SUIT_NAMES.items():
            uniq_in_suit = sum(1 for n in range(1, 10) if c_all[f"{n}{s_code}"] > 0)
            if uniq_in_suit >= 6:
                prog = min(96, int((uniq_in_suit / 9.0) * 100))
                b_pt = (uniq_in_suit - 5) * (0.23 if is_menzen else 0.15)
                yakus_found.append((b_pt, prog, "TierB", f"🌟一気通貫[{s_label}]({uniq_in_suit}/9種)"))

        # 対々和
        total_trips = sum(1 for v in c_all.values() if v >= 3)
        total_pairs = sum(1 for v in c_all.values() if v == 2)
        if total_trips + total_pairs >= 4 and (total_trips >= 1 or total_pairs >= 4):
            prog = min(95, int(((total_trips * 2 + total_pairs) / 8.0) * 100))
            b_pt = total_trips * 0.26 + total_pairs * 0.12
            yakus_found.append((b_pt, prog, "TierB", f"🌟対々和(刻子{total_trips}・対子{total_pairs})"))

        # チャンタ・純全帯幺九（123, 789, 幺九牌の比率）
        chanta_tiles = sum(
            1 for t in all_norm
            if t in YAOCHU_TILES or (len(t) == 2 and t[0] in ("2", "3", "7", "8"))
        )
        if yaochu_cnt >= 5 and chanta_tiles >= 11:
            is_junchan = (honor_cnt == 0)
            prog = min(94, int((chanta_tiles / total_len) * 100))
            b_pt = (yaochu_cnt - 4) * (0.24 if is_junchan else 0.17)
            c_name = "💎純全帯幺九" if is_junchan else "🌟混全帯幺九(チャンタ)"
            yakus_found.append((b_pt, prog, "TierB", f"{c_name}(端絡み{chanta_tiles}枚)"))

        # スコア順にソート（メインのロマン役＋複合ロマン役のボーナス合算）
        yakus_found.sort(key=lambda x: x[0], reverse=True)
        if yakus_found:
            primary_bonus, top_prog, top_tier, top_desc = yakus_found[0]
            secondary_bonus = sum(x[0] * 0.35 for x in yakus_found[1:3])
            sub_names = [x[3] for x in yakus_found[1:2]]
            combined_desc = top_desc + (f" ＋ {sub_names[0]}" if sub_names else "")
            yaku_bonus = primary_bonus + secondary_bonus
        else:
            yaku_bonus = 0.0
            top_prog = 0
            top_tier = "None"
            combined_desc = ""

        return {
            "yaku_bonus": yaku_bonus,
            "dora_count": total_dora,
            "top_progress": top_prog,
            "top_tier": top_tier,
            "desc": combined_desc,
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
        preset_key: str = "dream",
    ) -> list[dict]:
        if not base_evals:
            return []

        items = self.snapshot_mortal_baseline(base_evals)
        if len(items) == 1:
            items[0]["teacher_note"] = "💎 [ロマン型AI]: 唯一の実合法手です"
            return items

        preset = ROMAN_PRESETS.get(preset_key, ROMAN_PRESETS["dream"])
        bonus_scale = preset["bonus_scale"]
        allow_back = preset["allow_shanten_back"]
        back_relief = preset["back_penalty_relief"]
        dora_weight = preset["dora_keep_weight"]
        push_threshold = preset["push_threshold"]

        pid = tracker.human_pid
        melds = list(tracker.melds[pid])
        is_menzen = all(m.get("type") == "暗槓" for m in melds)
        dora_set = {get_actual_dora(m) for m in tracker.dora_markers}

        full_raw = list(tracker.hands[pid]) + ([tracker.drawn_tiles[pid]] if tracker.drawn_tiles[pid] else [])
        base_romance = self._evaluate_hand_romance(full_raw, melds, dora_set)

        u_map = tenpai_info.get("discard_ukeire_map", {})
        min_shanten = tenpai_info.get("current_shanten", 1)
        d_map = danger_info.get("tiles", {}) if danger_info.get("active") else {}

        # MortalのQ値の広がり幅を取得し、ロマン加点が適切に順位を動かせるようスケーリング
        top3_qs = [x["mortal_q"] for x in items[:min(5, len(items))]]
        q_spread = max(0.45, min(2.2, (max(top3_qs) - min(top3_qs)) if len(top3_qs) > 1 else 0.8))

        for item in items:
            act = item["action"]
            m_q = item["mortal_q"]
            bonus_q = 0.0
            notes = []

            if act == "hora":
                item["q_value"] = m_q + 15.0
                item["teacher_note"] = "🎉 【ロマン成就！】 和了（アガリ）を決めて高打点を獲得しましょう！"
                continue

            # ------------------------------------------------------------------
            # Case A: 打牌アクションの評価
            # ------------------------------------------------------------------
            if act in TILES_SET:
                after_raw = list(full_raw)
                if act in after_raw:
                    after_raw.remove(act)
                elif norm_tile(act) in [norm_tile(x) for x in after_raw]:
                    target_rm = next(x for x in after_raw if norm_tile(x) == norm_tile(act))
                    after_raw.remove(target_rm)

                rom = self._evaluate_hand_romance(after_raw, melds, dora_set)
                u_entry = u_map.get(act) or u_map.get(norm_tile(act))
                sh_after = u_entry["shanten"] if u_entry else min_shanten
                sh_diff = sh_after - min_shanten  # 0なら最短シャンテン維持、1以上ならシャンテン戻し

                # 1. ロマン役の進行度・ボーナス加点
                if rom["yaku_bonus"] > 0:
                    raw_rom_add = rom["yaku_bonus"] * bonus_scale * (0.65 + 0.35 * q_spread)
                    bonus_q += raw_rom_add
                    if rom["desc"]:
                        notes.append(f"{rom['desc']}(進行{rom['top_progress']}%)")

                # 2. ドラ・赤ドラ保持ボーナス vs ドラ切りペナルティ（Q2: ドラ徹底保護）
                is_act_dora = (norm_tile(act) in dora_set) or (act in ("5mr", "5pr", "5sr"))
                if is_act_dora:
                    dora_pen = -0.95 * dora_weight * q_spread
                    bonus_q += dora_pen
                    notes.append("⚠️ドラ放出ペナルティ")
                elif rom["dora_count"] > 0:
                    dora_keep_add = rom["dora_count"] * 0.22 * dora_weight
                    bonus_q += dora_keep_add
                    if rom["dora_count"] >= 2:
                        notes.append(f"🔥ドラ{rom['dora_count']}枚保持")

                # 3. シャンテン戻しの救済（Q3: ロマン役が有力ならシャンテン戻しのMortal減点を打ち消す）
                if sh_diff > 0:
                    if sh_diff <= allow_back and (rom["top_progress"] >= 55 or rom["top_tier"] == "TierS"):
                        # Mortalがシャンテン戻しに与えた大きなマイナスQ値を緩和・逆転可能にする
                        best_mortal_q = items[0]["mortal_q"]
                        q_deficit = max(0.0, best_mortal_q - m_q)
                        relief = q_deficit * back_relief
                        bonus_q += relief
                        notes.append(f"📐ロマン狙い{sh_diff}シャンテン戻し許容")
                    else:
                        # ロマン役が無いのに意味なくシャンテン戻しする手は減点
                        bonus_q -= 0.65 * sh_diff

                # 4. 他家リーチ・副露に対する「熱血ロマン押し」（Q5）
                d_entry = d_map.get(act)
                if d_entry and d_entry["risk_score"] >= 50:
                    is_high_romance = (
                        rom["top_progress"] >= push_threshold
                        or rom["top_tier"] == "TierS"
                        or rom["dora_count"] >= 3
                    )
                    if is_high_romance and sh_after <= 1:
                        # Mortalが危険牌に与えたオリ減点を打ち消して真っ直ぐ押させる
                        best_mortal_q = items[0]["mortal_q"]
                        q_deficit = max(0.0, best_mortal_q - m_q)
                        bonus_q += q_deficit * 0.80 + 0.35 * bonus_scale
                        notes.append("⚔️勝負手・熱血全ツッパ推奨！")

            # ------------------------------------------------------------------
            # Case B: リーチ判断（Q4: 裏ドラ・一発のロマンを求めて積極リーチ）
            # ------------------------------------------------------------------
            elif act == "reach":
                # 役満（TierS）が目の前でリーチすると出アガリしにくい場合を除き、リーチを強く推奨
                bonus_q += 0.55 * bonus_scale
                notes.append("🔥門前リーチ＋一発・裏ドラのロマン追及！")

            # ------------------------------------------------------------------
            # Case C: 鳴き（ポン・チー・カン） vs スルー判断（Q4: メリハリ副露）
            # ------------------------------------------------------------------
            elif act in ("pon", "chi_low", "chi_mid", "chi_high", "kan"):
                last_d = tracker.last_discard
                called_pai = last_d["pai"] if last_d else ""
                called_norm = norm_tile(called_pai)

                # 門前限定ロマン（国士・四暗刻・七対子・二盃口）が有力なときは鳴きを減点
                if any(k in base_romance["desc"] for k in ("国士", "四暗刻", "七対子", "九蓮")):
                    bonus_q -= 0.85 * bonus_scale
                    notes.append("🛑門前役満/対子ロマンが消える鳴き")
                else:
                    # 鳴いても成立する大三元・混一色・清一色・対々和・三色・一気通貫・ドラ3なら積極加点
                    is_koutsu_call = act in ("pon", "kan")
                    if is_koutsu_call and called_norm in DRAGONS and base_romance["top_tier"] == "TierS":
                        bonus_q += 1.10 * bonus_scale
                        notes.append("👑三元牌ポン！大三元・小三元へ前進")
                    elif "清一色" in base_romance["desc"] or "混一色" in base_romance["desc"]:
                        bonus_q += 0.60 * bonus_scale
                        notes.append(f"⚡染め手（{base_romance['desc'].split('(')[0]}）積極仕掛け！")
                    elif is_koutsu_call and ("対々和" in base_romance["desc"] or "混老頭" in base_romance["desc"]):
                        bonus_q += 0.55 * bonus_scale
                        notes.append("⚡対々和・刻子手ロマン仕掛け！")
                    elif base_romance["dora_count"] >= 3 or called_norm in dora_set or called_pai in ("5mr", "5pr", "5sr"):
                        bonus_q += 0.50 * bonus_scale
                        notes.append("🔥ドラ絡み高打点仕掛け！")
                    else:
                        # 安い仕掛け（1000点・2000点）はロマン型では減点
                        bonus_q -= 0.40 * bonus_scale
                        notes.append("💤打点が安くなる仕掛けは非推奨")

            elif act == "none":
                # スルー判断：門前ロマンがある時や、鳴くと安くなる時はスルーを加点
                if is_menzen and (base_romance["top_progress"] >= 55 or base_romance["dora_count"] >= 2):
                    bonus_q += 0.45 * bonus_scale
                    notes.append(f"🛡️門前ロマン維持（{base_romance['desc'] or 'メンゼン高打点'}）")

            item["q_value"] = m_q + bonus_q
            item["teacher_bonus"] = round(bonus_q, 4)
            if notes:
                sign_str = f"{bonus_q:+.2f}"
                item["teacher_note"] = f"💎 ロマン補正({sign_str}): " + " / ".join(notes)
            else:
                item["teacher_note"] = "💎 ロマン補正(±0.00): 標準進行"

        sorted_items = self.normalize_and_sort(items, temperature=1.2)

        # ロマン補正によってMortal 1位以外の候補が1位に浮上した場合は強調ラベルを付与
        if sorted_items and sorted_items[0].get("mortal_rank", 1) > 1:
            top = sorted_items[0]
            top["teacher_note"] = (
                f"🌟【ロマン推奨！(Mortal {top['mortal_rank']}位➡1位)】{top.get('teacher_note', '')}"
            )

        return sorted_items