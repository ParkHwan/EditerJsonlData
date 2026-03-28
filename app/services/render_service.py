"""하위 호환 래퍼 — 새 render 패키지로 위임

이 파일은 기존 `from app.services.render_service import ...`
형태의 import를 깨뜨리지 않기 위한 얇은 호환 레이어이다.
새로 작성하는 코드는 `from app.services.render import ...`를 사용하라.
"""
from app.services.render import *  # noqa: F401, F403
from app.services.render import __all__ as _all  # noqa: F401

__all__ = _all  # type: ignore[assignment]
