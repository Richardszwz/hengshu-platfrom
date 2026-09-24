"""保证本组件的 app 包优先于其他组件同名包（router/console 均含 app 包）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
