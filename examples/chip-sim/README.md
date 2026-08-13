# This example tree is a human debug aid only. It is not stage acceptance.
# P0/Pn acceptance is the automated chip_sim.Client V0 script
# (examples/chip-sim/python/tests/test_v0_loop.py).

chip-sim vertical layer: Agent-facing RTL + SoC software simulation SDK.

Development order: FakeAgentEnv → Client → V0 tests. Do not lead with
hand-run QEMU/Verilator demos.

```bash
cd examples/chip-sim/python
python -m pytest
```
