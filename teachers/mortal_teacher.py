# teachers/mortal_teacher.py
from .base_teacher import BaseTeacherAI


class MortalTeacherAI(BaseTeacherAI):
    teacher_id = "mortal"
    name = "標準型 (Mortal 純粋評価)"
    description = "Mortalのニューラルネットが出力したQ値（局収支期待値）をそのまま用いる標準コーチです。"

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
        if not base_evals:
            return []
        items = self.snapshot_mortal_baseline(base_evals)
        return items