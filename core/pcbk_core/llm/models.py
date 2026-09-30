"""Белый список моделей LLM-прокси: закреплённая точка ZDR, усилие рассуждения и верхние цены.

Цены — в долларах за 1 млн токенов и **верхние**, а не цены одной точки (утренняя проверка Д4а,
`docs/checks/D4a.md`, У1–У3):
- базовое имя в `provider.only` (`fireworks`, `azure`) пускает и региональные варианты
  (`fireworks/us`, `azure/us`, `azure/eu`), а `provider_matches` их не различает — в таблице максимум
  по всем вариантам базового имени (уровни `/flex` и `/priority` базовым именем не выбираются);
- `tier_*` — цены длинного входа: с `tier_tokens` оценённых токенов входа (Azure — 272 000);
- у точки со скидкой (Gemini на `google-vertex/global`, `discount` 0,5) — цена без скидки: цена в
  списке уже со скидкой, оценка должна пережить её конец.

Вход оценивается по байтам собранного тела: `ceil(байты / bytes_per_token)` токенов. У семейства Gemini
`bytes_per_token = 1`: его токенизатор режет числа по цифре, а ответы историана — почти одни цифры. У
остальных — 3 (`BYTES_PER_TOKEN`). Остаток: нарочно подобранный текст (байтовый откат токенизатора — до
токена на байт) у остальных моделей может стоить до трёх раз больше оценки; упор — лимит ключа $1000.

Модуль без сторонних зависимостей: его импортирует `LlmSettings.from_env` (П8).
"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

# делитель верхней оценки входа по умолчанию (байт на токен); у Gemini — 1, см. строку таблицы
BYTES_PER_TOKEN = 3


@dataclass(frozen=True)
class ModelPolicy:
    model: str
    provider_only: str                          # единственная точка в provider.only
    effort: Literal["low", "medium", "high"]    # reasoning.effort каждого запроса
    in_usd_m: float
    out_usd_m: float
    write_usd_m: float                          # запись кэша
    auto_cache: bool = False                    # cache_control верхнего уровня (только Claude)
    tier_tokens: int | None = None              # с этого числа токенов входа — цены tier_*
    tier_in_usd_m: float = 0.0
    tier_out_usd_m: float = 0.0
    tier_write_usd_m: float = 0.0
    bytes_per_token: int = BYTES_PER_TOKEN      # делитель оценки входа

    def input_tokens(self, body_bytes: int) -> int:
        """Оценённые токены входа: ceil(байты / bytes_per_token), в целых — без округления float."""
        return -(-body_bytes // self.bytes_per_token)

    def _prices(self, n: int) -> tuple[float, float, float]:
        """(вход, выход, запись) при n токенах входа: с `tier_tokens` и больше — цены длинного входа."""
        if self.tier_tokens is not None and n >= self.tier_tokens:
            return self.tier_in_usd_m, self.tier_out_usd_m, self.tier_write_usd_m
        return self.in_usd_m, self.out_usd_m, self.write_usd_m

    def estimate_usd(self, body_bytes: int, max_tokens: int) -> float:
        """Верхняя оценка вызова (Д4а-R13): n × max(вход, запись) + max_tokens × выход, n = input_tokens."""
        n = self.input_tokens(body_bytes)
        inp, out, write = self._prices(n)
        return n * max(inp, write) / 1e6 + max_tokens * out / 1e6

    def max_price(self, n: int) -> dict:
        """`provider.max_price` запроса с n оценёнными токенами входа — та же ступень цен, что у оценки.

        $ за 1 млн токенов, плата за запрос — 0. Точку дороже OpenRouter не выберет и откажет
        (`pcbk_upstream_rejected`), а не спишет больше оценки. Ступень берётся по n: цена длинного входа
        при коротком теле пустила бы точку дороже той, по которой считана оценка.
        """
        inp, out, _ = self._prices(n)
        return {"prompt": inp, "completion": out, "request": 0}


# ровно семь, по порядку research/08; основание каждой цены — в комментарии строки
CANDIDATES: tuple[ModelPolicy, ...] = (
    # точка, вариантов нет
    ModelPolicy("z-ai/glm-5.3", "fireworks", "high", 1.40, 4.40, 0.0),
    # максимум — fireworks/us (точка fireworks: 0.22 / 0.66)
    ModelPolicy("deepseek/deepseek-v4.1-flash", "fireworks", "high", 0.45, 1.80, 0.45),
    # без скидки 0,5: 0.75 / 3.75 × 2; токен на цифру — байт на токен
    ModelPolicy("google/gemini-3.8-flash", "google-vertex/global", "medium", 1.50, 7.50, 0.0, bytes_per_token=1),
    # максимум — azure/us, azure/eu (+10 %); длинный вход 4.00 / 15.00 / 5.00 + те же 10 %
    ModelPolicy("openai/gpt-6-sol", "azure", "medium", 2.20, 11.00, 2.75,
                tier_tokens=272_000, tier_in_usd_m=4.40, tier_out_usd_m=16.50, tier_write_usd_m=5.50),
    # максимум — fireworks/us (точка fireworks: 0.15 / 0.50)
    ModelPolicy("z-ai/glm-5.3-flash", "fireworks", "low", 0.225, 0.75, 0.0),
    # максимум — azure/us, azure/eu (+10 %); длинный вход 0.20 / 0.75 / 0.25 + те же 10 %
    ModelPolicy("openai/gpt-6-luna", "azure", "medium", 0.11, 0.55, 0.1375,
                tier_tokens=272_000, tier_in_usd_m=0.22, tier_out_usd_m=0.825, tier_write_usd_m=0.275),
    # точка, вариантов нет
    ModelPolicy("anthropic/claude-sonnet-5.5", "google-vertex/global", "low", 2.00, 10.00, 2.50, auto_cache=True),
)
_BY_NAME = {p.model: p for p in CANDIDATES}

PROVIDER_FIXED = MappingProxyType({"allow_fallbacks": False, "zdr": True, "data_collection": "deny",
                                   "require_parameters": True})


def select_models(names: str) -> dict[str, ModelPolicy]:
    """Разрешённые модели по `LLM_MODELS`: пусто — все семь по порядку таблицы.

    Иначе — имена через запятую (пробелы вокруг имён срезаются), порядок — как в строке. Неизвестное
    или пустое имя и повтор — `ValueError("LLM_MODELS: …")`.
    """
    if not names.strip():
        return dict(_BY_NAME)
    chosen: dict[str, ModelPolicy] = {}
    for name in (part.strip() for part in names.split(",")):
        if not name:
            raise ValueError("LLM_MODELS: пустое имя модели в списке")
        if name not in _BY_NAME:
            raise ValueError(f"LLM_MODELS: неизвестная модель «{name}»")
        if name in chosen:
            raise ValueError(f"LLM_MODELS: модель «{name}» названа дважды")
        chosen[name] = _BY_NAME[name]
    return chosen


def _squash(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def provider_matches(pinned: str, reported: str) -> bool:
    """Точка из ответа (`provider`) — закреплённая: без регистра и знаков одна строка — начало другой.

    `google-vertex/global` ~ `Google Vertex`, `fireworks` ~ `Fireworks`. Пустое после чистки имя не
    совпадает ни с чем: пустая строка — начало любой.
    """
    if not isinstance(pinned, str) or not isinstance(reported, str):
        return False
    a, b = _squash(pinned), _squash(reported)
    return bool(a) and bool(b) and (a.startswith(b) or b.startswith(a))
