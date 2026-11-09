# optuna_tuner.py
import os
import sys
import json
import time
import random
import argparse
from copy import deepcopy
from datetime import datetime
import numpy as np
import torch

try:
    import optuna
    from optuna.samplers import TPESampler
    from optuna.pruners import MedianPruner
    from optuna.trial import TrialState
except ImportError:
    print("エラー: optuna がインストールされていません。以下のコマンドを実行してください:")
    print("  pip install optuna")
    sys.exit(1)

from riichienv import RiichiEnv, GameRule
from ai_evaluator import parse_evaluations, load_mortal_bot, get_device_status_info
from teachers import get_teacher_ai
from teachers.search_teacher import load_search_weights, save_search_weights
import arena_benchmark as ab
import web_app as wa

PARAM_KEYS = (
    "guardrail_q_window",
    "override_strength",
    "w_gpu_ev",
    "w_trap_wait",
    "w_pre_defense",
    "w_oya_control",
    "w_wall_kabe",
    "w_flush_restrain",
    "w_keiten_push",
    "w_placement_cond",
)


class LuckFreeTracker:
    """
    運の要素を排除するため、
    1. 「探索AIがMortalと違う手を選んだ局（介入局）」のみの収支・和了・放銃
    2. 運に左右されない「介入時の期待値改善度（受け入れ枚数差・危険度削減量）」
    を精密追跡するトラッカー
    """
    def __init__(self):
        self.intervened_rounds = 0
        self.non_intervened_rounds = 0
        self.intervened_net_points = 0.0
        self.intervened_wins = 0
        self.intervened_deal_ins = 0

        self.total_interventions = 0
        self.safety_gain_sum = 0.0
        self.ukeire_gain_sum = 0.0


def run_luck_controlled_game(
    game_idx: int,
    seed_base: int,
    bots: dict,
    seat_to_player: dict[int, ab.PlayerStats],
    luck_tracker: LuckFreeTracker,
    search_seat_pid: int,
):
    game_seed = seed_base + game_idx * 97
    random.seed(game_seed)
    np.random.seed(game_seed)
    torch.manual_seed(game_seed)

    env = RiichiEnv(game_mode="4p-red-half", rule=GameRule.default_tenhou())
    try:
        obs_dict = env.reset(seed=game_seed)
    except TypeError:
        obs_dict = env.reset()

    tracker = wa.TableTracker(human_pid=0)
    round_state = {
        "counted_participation": False,
        "intervened_this_round": False,
    }
    step_count = 0

    while not env.done():
        step_count += 1
        if step_count > 2000:
            break

        pending_actions = {}
        for pid in range(4):
            if pid not in obs_dict:
                continue
            obs = obs_dict[pid]
            last_resp = None

            for ev_str in obs.new_events():
                ev = json.loads(ev_str)
                etype = ev.get("type")
                if pid == 0 and etype == "start_kyoku":
                    round_state = {
                        "counted_participation": False,
                        "intervened_this_round": False,
                    }
                if pid == 0 and etype in ("hora", "ryukyoku"):
                    ab.record_round_outcome_stats(ev, tracker, seat_to_player, round_state)
                    deltas = ev.get("deltas", [0, 0, 0, 0])
                    my_delta = float(deltas[search_seat_pid]) if search_seat_pid < len(deltas) else 0.0
                    if round_state.get("intervened_this_round", False):
                        luck_tracker.intervened_rounds += 1
                        luck_tracker.intervened_net_points += my_delta
                        if etype == "hora":
                            if ev.get("actor") == search_seat_pid:
                                luck_tracker.intervened_wins += 1
                            elif ev.get("target") == search_seat_pid and ev.get("actor") != search_seat_pid:
                                luck_tracker.intervened_deal_ins += 1
                    else:
                        luck_tracker.non_intervened_rounds += 1

                tracker.update_from_player_obs(pid, ev_str)
                resp = bots[pid].react(ev_str)
                if resp is not None:
                    last_resp = resp

            raw_evals = parse_evaluations(last_resp)
            p_st = seat_to_player[pid]

            if pid != search_seat_pid:
                act = ab.choose_action_with_teacher(pid, obs, raw_evals, last_resp, tracker, p_st)
            else:
                hora_act = wa.find_hora_action_from_obs(obs, pid, tracker)
                if hora_act is not None:
                    act = hora_act
                else:
                    synced_evals = ab.sync_evals_for_seat(pid, obs, raw_evals, last_resp, tracker)
                    if len(synced_evals) <= 1:
                        act = wa.build_actor_riichi_action(
                            pid, obs, synced_evals[0]["action"] if synced_evals else "", synced_evals, last_resp, tracker
                        )
                    else:
                        mortal_best = synced_evals[0]["action"]
                        orig_h_pid = tracker.human_pid
                        orig_last_d = tracker.last_discard
                        try:
                            tracker.human_pid = pid
                            if tracker.player_last_discard.get(pid):
                                tracker.last_discard = tracker.player_last_discard[pid]
                            tenpai_info = wa.analyze_hand_shanten_and_ukeire(tracker)
                            danger_info = wa.analyze_danger_tiles(tracker)
                            teacher = get_teacher_ai("search")
                            final_evals = teacher.evaluate(
                                base_evals=synced_evals,
                                tracker=tracker,
                                obs=obs,
                                tenpai_info=tenpai_info,
                                danger_info=danger_info,
                                potential_yakus=[],
                                score_estimate=None,
                                preset_key=p_st.preset,
                                custom_params=p_st.custom_params,
                            )
                        finally:
                            tracker.human_pid = orig_h_pid
                            tracker.last_discard = orig_last_d

                        chosen = final_evals[0]["action"] if final_evals else mortal_best
                        p_st.total_decisions += 1
                        if chosen == mortal_best:
                            p_st.mortal_top1_matches += 1
                        else:
                            round_state["intervened_this_round"] = True
                            luck_tracker.total_interventions += 1
                            d_map = danger_info.get("tiles", {}) if danger_info.get("active") else {}
                            r_mortal = float(d_map.get(mortal_best, {}).get("risk_score", 20.0))
                            r_chosen = float(d_map.get(chosen, {}).get("risk_score", 20.0))
                            if danger_info.get("active"):
                                luck_tracker.safety_gain_sum += (r_mortal - r_chosen) / 25.0
                            luck_tracker.ukeire_gain_sum += float(final_evals[0].get("teacher_bonus", 0.0))

                        act = wa.build_actor_riichi_action(pid, obs, chosen, final_evals, last_resp, tracker)

            pending_actions[pid] = act

        obs_dict = env.step(pending_actions)

    if obs_dict:
        for pid in range(4):
            if pid in obs_dict:
                for ev_str in obs_dict[pid].new_events():
                    ev = json.loads(ev_str)
                    if pid == 0 and ev.get("type") in ("hora", "ryukyoku"):
                        ab.record_round_outcome_stats(ev, tracker, seat_to_player, round_state)
                    tracker.update_from_player_obs(pid, ev_str)

    final_scores = list(env.scores())
    seat_ranking = sorted(range(4), key=lambda s: (final_scores[s], -s), reverse=True)
    for rank_idx, seat_pid in enumerate(seat_ranking):
        rank_num = rank_idx + 1
        sc = final_scores[seat_pid]
        p_st = seat_to_player[seat_pid]
        p_st.games_played += 1
        p_st.rank_counts[rank_num] += 1
        p_st.total_final_score += sc


def compute_luck_reduced_objective(p_stat: ab.PlayerStats, lt: LuckFreeTracker) -> float:
    g = max(1, p_stat.games_played)
    int_rounds = max(1, lt.intervened_rounds)

    # ① 介入局のみの純粋パフォーマンス（無関係な局での被ツモ親っかぶり運を完全排除）
    avg_int_pts = lt.intervened_net_points / int_rounds
    int_win_rate = (lt.intervened_wins / int_rounds) * 100.0
    int_deal_rate = (lt.intervened_deal_ins / int_rounds) * 100.0
    intervention_outcome_score = (avg_int_pts / 60.0) + (int_win_rate - int_deal_rate * 1.7) * 0.85

    # ② 運ゼロの打牌プロセス品質スコア
    avg_safety_gain = lt.safety_gain_sum / max(1, lt.total_interventions)
    process_quality_score = avg_safety_gain * 15.0

    # ③ 着順安定度（ラス回避・平均順位）
    r1 = p_stat.rank_counts.get(1, 0)
    r2 = p_stat.rank_counts.get(2, 0)
    r4 = p_stat.rank_counts.get(4, 0)
    rentai_rate = (r1 + r2) / g
    last_rate = r4 / g
    placement_score = (2.50 - p_stat.avg_rank) * 35.0 + (rentai_rate - 0.50) * 25.0 - (last_rate * 45.0)

    # ④ 差別化（Mortal一致率 74%〜89.5% ターゲット）制約
    match_rate = p_stat.mortal_match_rate
    if match_rate > 89.5:
        uniqueness_score = -(match_rate - 89.5) * 8.0
    elif 74.0 <= match_rate <= 89.5:
        uniqueness_score = +18.0
    else:
        uniqueness_score = -(74.0 - match_rate) * 3.5

    total_obj = (
        0.45 * intervention_outcome_score
        + 0.20 * process_quality_score
        + 0.35 * placement_score
        + uniqueness_score
    )
    return round(float(total_obj), 2)


def main():
    parser = argparse.ArgumentParser(description="Optuna DB継続学習 + 運排除メトリクス パラメータ最適化")
    parser.add_argument("--trials", type=int, default=20, help="今回追加で実行するTrial数")
    parser.add_argument("--games", type=int, default=12, help="1Trialあたりの最大評価半荘数(4の倍数推奨)")
    parser.add_argument("--preset", type=str, default="blitz", help="探索プリセット(blitz|fast|standard)")
    parser.add_argument("--seed", type=int, default=202611, help="共通乱数シード基準値")
    parser.add_argument("--device", type=str, default="cuda", help="使用デバイス")
    parser.add_argument("--study-name", type=str, default="mahjong_meta_v1", help="Optuna Study名(DB内の識別名)")
    parser.add_argument("--db-file", type=str, default="arena_results/optuna_study.db", help="SQLite DBファイルパス")
    parser.add_argument("--reset-db", action="store_true", help="既存の同名Study履歴をリセットして一から学習し直す")
    args = parser.parse_args()

    os.environ["MAHJONG_DEVICE"] = args.device
    dev_info = get_device_status_info(args.device)
    bots = wa.session.bots if dev_info["active_device"] == "cuda" else {
        pid: load_mortal_bot(pid, device_pref="cpu") for pid in range(4)
    }

    out_dir = os.path.join(wa.BASE_DIR, "arena_results")
    os.makedirs(out_dir, exist_ok=True)

    # SQLite ストレージURLの構築
    db_abs_path = os.path.abspath(os.path.join(wa.BASE_DIR, args.db_file))
    os.makedirs(os.path.dirname(db_abs_path), exist_ok=True)
    storage_url = f"sqlite:///{db_abs_path.replace(chr(92), '/')}"

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    if args.reset_db:
        try:
            optuna.delete_study(study_name=args.study_name, storage=storage_url)
            print(f"🗑️ 既存の Study '{args.study_name}' をリセットしました。")
        except Exception:
            pass

    study = optuna.create_study(
        study_name=args.study_name,
        storage=storage_url,
        load_if_exists=True,
        direction="maximize",
        sampler=TPESampler(seed=42, multivariate=True),
        pruner=MedianPruner(n_startup_trials=4, n_warmup_steps=4, interval_steps=4),
    )

    existing_trials = len(study.trials)
    completed_trials = [t for t in study.trials if t.state == TrialState.COMPLETE]

    print("=" * 114)
    print(f"🔬 【Optuna DB継続学習ベイズ最適化 (TPE + 段階的枝刈り + 介入局抽出・運排除評価)】")
    print(f" ・デバイス  : {dev_info['active_device']} ({dev_info['gpu_name']})")
    print(f" ・DB保存先  : {db_abs_path} (Study名: '{args.study_name}')")
    if existing_trials > 0:
        best_so_far = f"{study.best_value:+.2f} (Trial #{study.best_trial.number})" if completed_trials else "未完走"
        print(f" ・継続学習  : 📂 過去 {existing_trials} Trials (完走 {len(completed_trials)} 件 / 歴代Best: {best_so_far}) を引き継ぎます！")
        print(f" ・今回の実行: +{args.trials} Trials 追加実行 (累計 {existing_trials + args.trials} Trials へ)")
    else:
        print(f" ・新規学習  : 🆕 新規Studyを作成しました (今回 {args.trials} Trials 実行)")
    print("=" * 114)

    # 完走したTrialがまだ0件の場合は、現在の search_params.json を基準Trialとして登録する
    current_best_params = load_search_weights({"device": args.device, "gpu_fast_mode": True})
    if len(completed_trials) == 0:
        study.enqueue_trial({k: current_best_params[k] for k in PARAM_KEYS if k in current_best_params})

    def objective(trial: optuna.Trial) -> float:
        cand_params = {
            "device": args.device,
            "gpu_fast_mode": True,
            "guardrail_q_window": round(trial.suggest_float("guardrail_q_window", 0.65, 1.35), 3),
            "override_strength":  round(trial.suggest_float("override_strength", 0.95, 1.65), 3),
            "w_gpu_ev":           round(trial.suggest_float("w_gpu_ev", 0.70, 1.90), 3),
            "w_trap_wait":        round(trial.suggest_float("w_trap_wait", 0.80, 2.20), 3),
            "w_pre_defense":      round(trial.suggest_float("w_pre_defense", 0.90, 2.30), 3),
            "w_oya_control":      round(trial.suggest_float("w_oya_control", 0.70, 2.00), 3),
            "w_wall_kabe":        round(trial.suggest_float("w_wall_kabe", 0.60, 1.90), 3),
            "w_flush_restrain":   round(trial.suggest_float("w_flush_restrain", 0.50, 1.80), 3),
            "w_keiten_push":      round(trial.suggest_float("w_keiten_push", 0.60, 1.90), 3),
            "w_placement_cond":   round(trial.suggest_float("w_placement_cond", 0.80, 2.20), 3),
        }

        cand_player = ab.PlayerStats(0, "Optuna探索AI", "search", args.preset, cand_params)
        mortal_players = [ab.PlayerStats(i, f"Mortal_{i}", "mortal", "standard") for i in range(1, 4)]
        all_players = [cand_player] + mortal_players
        luck_tracker = LuckFreeTracker()

        t_start = time.perf_counter()
        for g_idx in range(args.games):
            shift = g_idx % 4
            order = [0, 1, 2, 3][shift:] + [0, 1, 2, 3][:shift]
            seat_to_player = {seat_pid: all_players[entry_idx] for seat_pid, entry_idx in enumerate(order)}
            search_seat_pid = order.index(0)

            run_luck_controlled_game(
                g_idx, args.seed, bots, seat_to_player, luck_tracker, search_seat_pid
            )

            if (g_idx + 1) % 4 == 0 and (g_idx + 1) < args.games:
                inter_score = compute_luck_reduced_objective(cand_player, luck_tracker)
                trial.report(inter_score, step=g_idx + 1)
                if trial.should_prune():
                    dt = time.perf_counter() - t_start
                    dist = f"{cand_player.rank_counts[1]}-{cand_player.rank_counts[2]}-{cand_player.rank_counts[3]}-{cand_player.rank_counts[4]}"
                    int_r = max(1, luck_tracker.intervened_rounds)
                    trial.set_user_attr("status", f"pruned_at_{g_idx+1}_games")
                    trial.set_user_attr("games_played", g_idx + 1)
                    trial.set_user_attr("avg_rank", round(cand_player.avg_rank, 3))
                    trial.set_user_attr("rank_counts", cand_player.rank_counts)
                    trial.set_user_attr("win_rate", round(cand_player.win_rate, 2))
                    trial.set_user_attr("deal_in_rate", round(cand_player.deal_in_rate, 2))
                    trial.set_user_attr("mortal_match_rate", round(cand_player.mortal_match_rate, 2))
                    trial.set_user_attr("intervened_avg_pts", round(luck_tracker.intervened_net_points / int_r, 1))
                    trial.set_user_attr("params", cand_params)
                    print(
                        f"[Trial {trial.number:>3} | ✂️ {g_idx+1}半荘で枝刈り ({dt:>4.1f}s)] "
                        f"中間Score={inter_score:+.1f} | 順位:{cand_player.avg_rank:.2f}[{dist}] | 一致率:{cand_player.mortal_match_rate:.1f}%"
                    )
                    raise optuna.TrialPruned()

        final_obj = compute_luck_reduced_objective(cand_player, luck_tracker)
        dt = time.perf_counter() - t_start
        dist = f"{cand_player.rank_counts[1]}-{cand_player.rank_counts[2]}-{cand_player.rank_counts[3]}-{cand_player.rank_counts[4]}"
        int_r = max(1, luck_tracker.intervened_rounds)
        int_pts = luck_tracker.intervened_net_points / int_r

        trial.set_user_attr("status", "completed")
        trial.set_user_attr("games_played", args.games)
        trial.set_user_attr("avg_rank", round(cand_player.avg_rank, 3))
        trial.set_user_attr("avg_score", round(cand_player.avg_final_score, 1))
        trial.set_user_attr("rank_counts", cand_player.rank_counts)
        trial.set_user_attr("win_rate", round(cand_player.win_rate, 2))
        trial.set_user_attr("deal_in_rate", round(cand_player.deal_in_rate, 2))
        trial.set_user_attr("mortal_match_rate", round(cand_player.mortal_match_rate, 2))
        trial.set_user_attr("intervened_rounds", luck_tracker.intervened_rounds)
        trial.set_user_attr("intervened_avg_pts", round(int_pts, 1))
        trial.set_user_attr("params", cand_params)

        print(
            f"[Trial {trial.number:>3} | ✅ {args.games}半荘完走 ({dt:>4.1f}s)] "
            f"運排除Score={final_obj:+.2f} | 順位:{cand_player.avg_rank:.2f}位[{dist}] | "
            f"介入局収支:{int_pts:+.0f}点/局({luck_tracker.intervened_rounds}局) | "
            f"和了:{cand_player.win_rate:.1f}% | 放銃:{cand_player.deal_in_rate:.1f}% | "
            f"★一致率:{cand_player.mortal_match_rate:.1f}%"
        )
        return final_obj

    try:
        study.optimize(objective, n_trials=args.trials)
    except KeyboardInterrupt:
        print("\n⚠️ ユーザーによる中断(Ctrl+C)を検知しました。現在までのDB学習結果を集計・保存します...")

    completed_after = [t for t in study.trials if t.state == TrialState.COMPLETE]
    if not completed_after:
        print("完走したTrialがまだありません。")
        return

    best_trial = study.best_trial
    best_params = best_trial.user_attrs.get("params", best_trial.params)
    best_params["device"] = args.device
    best_params["gpu_fast_mode"] = True
    save_search_weights(best_params)

    print("\n" + "=" * 114)
    print(f"🏆 【Optuna 運排除チューニング集計完了！】 (累計 {len(study.trials)} Trials / 完走 {len(completed_after)} 件)")
    print(f" 🌟 歴代ベスト Trial #{best_trial.number} (運排除Score: {best_trial.value:+.2f})")
    print(f" ・平均順位: {best_trial.user_attrs.get('avg_rank')}位 {best_trial.user_attrs.get('rank_counts')}")
    print(f" ・介入局での純粋平均収支: {best_trial.user_attrs.get('intervened_avg_pts'):+.1f} 点/局")
    print(f" ・Mortal一致率: {best_trial.user_attrs.get('mortal_match_rate')}%")
    print("-" * 114)

    importances_dict = {}
    try:
        importances = optuna.importance.get_param_importances(study)
        importances_dict = {k: round(float(v), 4) for k, v in importances.items()}
        print("📊 【7大メタ戦略 パラメータ重要度ランキング (累計全Trialから算出)】")
        for idx, (k, imp) in enumerate(importances.items(), 1):
            bar = "█" * int(imp * 40)
            print(f"  {idx:>2}位: {k:<22} | 重要度: {imp*100:>5.1f}% {bar}")
    except Exception:
        pass

    log_path = os.path.join(out_dir, f"optuna_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    trials_history = []
    for tr in study.trials:
        trials_history.append({
            "trial_number": tr.number,
            "state": str(tr.state).split(".")[-1],
            "score": round(tr.value, 2) if tr.value is not None else None,
            "games_played": tr.user_attrs.get("games_played"),
            "avg_rank": tr.user_attrs.get("avg_rank"),
            "avg_score": tr.user_attrs.get("avg_score"),
            "rank_counts": tr.user_attrs.get("rank_counts"),
            "win_rate": tr.user_attrs.get("win_rate"),
            "deal_in_rate": tr.user_attrs.get("deal_in_rate"),
            "mortal_match_rate": tr.user_attrs.get("mortal_match_rate"),
            "intervened_rounds": tr.user_attrs.get("intervened_rounds"),
            "intervened_avg_pts": tr.user_attrs.get("intervened_avg_pts"),
            "params": tr.user_attrs.get("params", tr.params),
        })

    export_log = {
        "timestamp": datetime.now().isoformat(),
        "study_name": args.study_name,
        "db_file": db_abs_path,
        "total_trials_in_db": len(study.trials),
        "completed_trials_in_db": len(completed_after),
        "best_trial_number": best_trial.number,
        "best_score": round(best_trial.value, 2) if best_trial.value is not None else None,
        "best_avg_rank": best_trial.user_attrs.get("avg_rank"),
        "best_rank_counts": best_trial.user_attrs.get("rank_counts"),
        "best_mortal_match_rate": best_trial.user_attrs.get("mortal_match_rate"),
        "best_intervened_avg_pts": best_trial.user_attrs.get("intervened_avg_pts"),
        "param_importances": importances_dict,
        "best_params": best_params,
        "trials": trials_history,
    }

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(export_log, f, ensure_ascii=False, indent=2)

    print("-" * 114)
    print("💾 歴代ベストパラメータを teachers/search_params.json に自動保存しました:")
    print(json.dumps(best_params, ensure_ascii=False, indent=2))
    print(f"📄 Optuna詳細ログJSONを保存しました: {log_path}")
    print(f"🗄️ 次回も同じコマンドを実行すると、DB ({args.db_file}) から続きを自動学習します。")
    print("=" * 114)


if __name__ == "__main__":
    main()