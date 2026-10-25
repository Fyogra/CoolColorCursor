"""Пересоздаёт app/assets/abilities.json (запасной список скиллов в поставке) из файлов Dota.
python tools/gen_ability_info.py   (нужна установленная Dota 2)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import ability_info

print("героев:", ability_info.generate(ability_info.ASSET_PATH))
