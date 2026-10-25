"""python tools/shop_build.py        — собрать и записать пак магазина (Dota должна быть закрыта)
python tools/shop_build.py off    — удалить пак"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import shop_mod

ok, err = shop_mod.uninstall() if len(sys.argv) > 1 and sys.argv[1] == "off" else shop_mod.build()
print("готово" if ok else err)
