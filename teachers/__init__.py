# teachers/__init__.py
from .base_teacher import BaseTeacherAI
from .mortal_teacher import MortalTeacherAI
from .search_teacher import SearchTeacherAI, SEARCH_PRESETS
from .roman_teacher import RomanTeacherAI, ROMAN_PRESETS
from .speed_teacher import SpeedTeacherAI, SPEED_PRESETS

TEACHER_REGISTRY: dict[str, BaseTeacherAI] = {
    "mortal": MortalTeacherAI(),
    "search": SearchTeacherAI(),
    "roman": RomanTeacherAI(),
    "speed": SpeedTeacherAI(),
}


def get_teacher_ai(teacher_id: str) -> BaseTeacherAI:
    return TEACHER_REGISTRY.get(teacher_id, TEACHER_REGISTRY["mortal"])


def get_teachers_metadata() -> list[dict]:
    return [
        {
            "id": t.teacher_id,
            "name": t.name,
            "description": t.description,
        }
        for t in TEACHER_REGISTRY.values()
    ]


def get_search_presets_metadata() -> list[dict]:
    return [{"id": k, "label": v["label"]} for k, v in SEARCH_PRESETS.items()]


def get_roman_presets_metadata() -> list[dict]:
    return [{"id": k, "label": v["label"]} for k, v in ROMAN_PRESETS.items()]


def get_speed_presets_metadata() -> list[dict]:
    return [{"id": k, "label": v["label"]} for k, v in SPEED_PRESETS.items()]