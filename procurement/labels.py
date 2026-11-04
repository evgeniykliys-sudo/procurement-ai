"""Русские подписи кодов статусов, рисков и категорий — общие для дашборда (`site`) и ИИ-записки (`ai`).

Здесь, а не в `site.py`: `ai` не должен тянуть шаблонизатор и Excel ради трёх словарей.
"""

STATUS_LABELS = {
    "order_now": "Заказать сейчас", "order_later": "Заказать позже", "ok": "В норме",
    "review_price": "На пересмотр цены", "dead_sell": "Распродать", "dead_close": "Закрыть карточку",
    "no_demand": "Нет спроса",
}
RISK_LABELS = {
    "deficit_unavoidable": "Дефицит неизбежен", "deficit_if_late": "Дефицит, если опоздать",
    "excess": "Излишек", "late_po": "Заказ опаздывает", "low_reliability": "Недовоз поставщика",
}
CATEGORY_LABELS = {
    "space_heaters": "Обогреватели", "string_trimmers": "Триммеры",
    "trimmer_blades_accessories": "Ножи и леска", "concrete_mixers": "Бетономешалки",
    "sanitary_faucets": "Смесители", "grain_mills": "Зернодробилки",
}
