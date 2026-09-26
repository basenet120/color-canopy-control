"""The simulator world (05 decision 4, 07 §7): device profiles, pinned fault settings, and the SimDriver's data.

Nothing in this package talks to a network. `conductor/drivers/sim.py` is the driver; this package holds the
device profiles it serves (`profiles.py`) and the latency/fault model it runs (`settings.py`).
"""
