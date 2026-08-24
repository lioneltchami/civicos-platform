import importlib.util
from pathlib import Path

path = Path(__file__).parents[1] / "apps/appointments/tests/test_delivery_state.py"
spec = importlib.util.spec_from_file_location("delivery_tests", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
for name in sorted(dir(module)):
    if name.startswith("test_"):
        getattr(module, name)()
        print(f"PASS {name}")
print("PASS all delivery-state checks")
