# teachers/base_teacher.py
from copy import deepcopy
import numpy as np


class BaseTeacherAI:
    teacher_id: str = "base"
    name: str = "基底教師AI"
    description: str = ""

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
    ) -> list[dict]:
        """
        base_evals（Mortalの合法手同期済み評価リスト）と盤面解析情報を受け取り、
        教師AI独自のロジックで q_value / score / prob / teacher_note 等を再計算して返す。
        """
        raise NotImplementedError

    @staticmethod
    def snapshot_mortal_baseline(base_evals: list[dict]) -> list[dict]:
        """元のMortalの順位・スコア・Q値を各要素に記録したディープコピーを返す"""
        items = deepcopy(base_evals)
        for idx, item in enumerate(items):
            item["mortal_rank"] = idx + 1
            item["mortal_q"] = float(item.get("q_value", 0.0))
            item["mortal_score"] = float(item.get("score", 0.0))
            item["mortal_prob"] = float(item.get("prob", 0.0))
            item.setdefault("teacher_bonus", 0.0)
            item.setdefault("teacher_note", "")
        return items

    @staticmethod
    def normalize_and_sort(eval_items: list[dict], temperature: float = 1.2) -> list[dict]:
        """
        更新後の `q_value` に基づき、Softmaxで `score` (第1位=100.0) と `prob` (合計100%) を再計算し、
        q_value の降順にソートして返す。
        """
        if not eval_items:
            return []

        items = deepcopy(eval_items)
        # アガリ(hora)が候補にある場合は最優先保護
        q_vals = np.array([float(x.get("q_value", 0.0)) for x in items], dtype=np.float64)
        max_q = np.max(q_vals)

        # オーバーフロー/アンダーフロー防止付きSoftmax
        scaled = np.clip((q_vals - max_q) * temperature, -40.0, 0.0)
        exp_q = np.exp(scaled)
        sum_exp = np.sum(exp_q)
        probs = (exp_q / sum_exp) * 100.0 if sum_exp > 0 else np.full_like(exp_q, 100.0 / len(items))
        scores = exp_q * 100.0

        for item, q, sc, pr in zip(items, q_vals, scores, probs):
            item["q_value"] = round(float(q), 4)
            item["score"] = round(float(sc), 1)
            item["prob"] = round(float(pr), 1)

        items.sort(key=lambda x: (x["action"] == "hora", x["q_value"]), reverse=True)
        if items:
            items[0]["score"] = 100.0
        return items