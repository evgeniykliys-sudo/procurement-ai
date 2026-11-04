"""Дымовой тест: каждый модуль пакета импортируется (ловит синтаксис и битые импорты)."""
import importlib
import pkgutil

import procurement


def test_all_modules_import():
    names = [m.name for m in pkgutil.walk_packages(procurement.__path__, "procurement.")]
    assert {"procurement.config", "procurement.data", "procurement.demand", "procurement.supply"} <= set(names)
    for name in names:
        if name.endswith("__main__"):
            continue  # CLI запускается, а не импортируется
        importlib.import_module(name)
